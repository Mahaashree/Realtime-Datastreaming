"""
MQTT Collector with Dual-Pipeline Optimization - PRODUCTION READY
Implements adaptive thread allocation for live and offline message processing
Optimized for 50-200 device deployment with separate pipeline queues
Includes device database monitoring for automatic offline message processing
"""
import json
import time
import os
import threading
import ssl
import sqlite3
import glob
from queue import Queue, Full, Empty
from datetime import datetime
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import WriteOptions
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from flask import Flask, jsonify
from threading import Thread

# Import monitoring module
from monitoring import MetricsCollector, StructuredLogger, print_alert_notification
from async_influx_writer import AsyncInfluxWriter

load_dotenv()

# Configuration
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "mqtt-collector-python")

MQTT_USE_TLS = os.getenv("MQTT_USE_TLS", "false").lower() == "true"
MQTT_TLS_INSECURE = os.getenv("MQTT_TLS_INSECURE", "false").lower() == "true"
MQTT_CA_CERTS = os.getenv("MQTT_CA_CERTS", None)
MQTT_CERTFILE = os.getenv("MQTT_CERTFILE", None)
MQTT_KEYFILE = os.getenv("MQTT_KEYFILE", None)
MQTT_USERNAME = os.getenv("MQTT_USERNAME", None)
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", None)

INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "my-org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "vehicle-data")

NUM_WORKER_THREADS = int(os.getenv("COLLECTOR_WORKER_THREADS", "8"))
MAX_QUEUE_SIZE = int(os.getenv("COLLECTOR_MAX_QUEUE_SIZE", "20000"))
BATCH_DRAIN_SIZE = int(os.getenv("BATCH_DRAIN_SIZE", "50"))

# Dual-pipeline configuration
LIVE_QUEUE_MAX_SIZE = int(os.getenv("LIVE_QUEUE_MAX_SIZE", "10000"))
OFFLINE_QUEUE_MAX_SIZE = int(os.getenv("OFFLINE_QUEUE_MAX_SIZE", "10000"))

# Device Database Monitoring Configuration
OFFLINE_QUEUE_DIR = os.getenv("OFFLINE_QUEUE_DIR", "offline_queues")
DEVICE_DB_SCAN_INTERVAL = int(os.getenv("DEVICE_DB_SCAN_INTERVAL", "30"))  # Scan every 30 seconds
DEVICE_DB_BATCH_SIZE = int(os.getenv("DEVICE_DB_BATCH_SIZE", "100"))  # Process 100 messages per scan
THREAD_REBALANCE_INTERVAL = int(os.getenv("THREAD_REBALANCE_INTERVAL", "30"))

# Thread allocation thresholds
OFFLINE_THRESHOLD_MINIMAL = int(os.getenv("OFFLINE_THRESHOLD_MINIMAL", "1000"))
OFFLINE_THRESHOLD_SMALL = int(os.getenv("OFFLINE_THRESHOLD_SMALL", "5000"))
OFFLINE_THRESHOLD_MEDIUM = int(os.getenv("OFFLINE_THRESHOLD_MEDIUM", "20000"))
OFFLINE_THRESHOLD_LARGE = int(os.getenv("OFFLINE_THRESHOLD_LARGE", "50000"))

# Pipeline-specific batch sizes
LIVE_BATCH_SIZE = int(os.getenv("LIVE_BATCH_SIZE", "5"))
OFFLINE_BATCH_SIZE = int(os.getenv("OFFLINE_BATCH_SIZE", "10"))
OFFLINE_BATCH_DELAY = float(os.getenv("OFFLINE_BATCH_DELAY", "0.0"))

# InfluxDB Write Configuration
INFLUXDB_BATCH_SIZE = int(os.getenv("INFLUXDB_BATCH_SIZE", "500"))
INFLUXDB_FLUSH_INTERVAL = int(os.getenv("INFLUXDB_FLUSH_INTERVAL", "500"))

# Monitoring configuration
ENABLE_PROMETHEUS = os.getenv("ENABLE_PROMETHEUS", "true").lower() == "true"
PROMETHEUS_PORT = int(os.getenv("PROMETHEUS_PORT", "9090"))
LOG_FILE = os.getenv("LOG_FILE", "logs/collector.log")
STATS_SERVER_PORT = int(os.getenv("STATS_SERVER_PORT", "9091"))


class AdaptiveThreadAllocator:
    """
    Manages dynamic thread allocation between live and offline pipelines.
    Implements hybrid adaptive strategy with live-first priority.
    """
    
    def __init__(self):
        self.total_threads = NUM_WORKER_THREADS
        self.live_threads = NUM_WORKER_THREADS  # Start with all threads for live
        self.offline_threads = 0
        self.last_rebalance = time.time()
        self.rebalance_interval = THREAD_REBALANCE_INTERVAL
        
        print(f"INFO [ThreadAllocator] Initialized with {self.total_threads} total threads")
        print(f"INFO [ThreadAllocator] Rebalance interval: {self.rebalance_interval}s")
    
    def calculate_allocation(self, offline_queue_size: int) -> tuple:
        """
        Calculate optimal thread allocation based on offline queue size.
        
        Strategy (LIVE-FIRST - all 8 threads for live unless offline needs processing):
        - 0-100 messages: 8 live, 0 offline (default - live has all threads)
        - 101-2,000: 7 live, 1 offline (small backlog - minimal offline help)
        - 2,001-10,000: 6 live, 2 offline (medium backlog - moderate offline help)
        - 10,001-30,000: 5 live, 3 offline (large backlog - significant offline help)
        - 30,000+: 4 live, 4 offline (huge backlog - maximum offline help)
        
        Philosophy: Keep all 8 threads for live unless offline queue is substantial.
        Live data always gets priority with minimum 4 threads guaranteed.
        """
        if offline_queue_size <= 100:
            # Default state: All threads for live (most of the time)
            return (8, 0)
        elif offline_queue_size <= 2000:
            # Small backlog: Give 1 thread to offline
            return (7, 1)
        elif offline_queue_size <= 10000:
            # Medium backlog: Give 2 threads to offline
            return (6, 2)
        elif offline_queue_size <= 30000:
            # Large backlog: Give 3 threads to offline
            return (5, 3)
        else:
            # Huge backlog: Maximum offline help (4 threads each)
            return (4, 4)
    
    def should_rebalance(self) -> bool:
        """Check if enough time has passed for rebalancing."""
        current_time = time.time()
        if current_time - self.last_rebalance >= self.rebalance_interval:
            self.last_rebalance = current_time
            return True
        return False
    
    def get_allocation(self, offline_queue_size: int) -> tuple:
        """Get current thread allocation, rebalancing if needed."""
        if self.should_rebalance():
            new_live, new_offline = self.calculate_allocation(offline_queue_size)
            
            if new_live != self.live_threads or new_offline != self.offline_threads:
                print(f"INFO [ThreadAllocator] Rebalancing: {self.live_threads}L/{self.offline_threads}O -> {new_live}L/{new_offline}O (queue: {offline_queue_size})")
                self.live_threads = new_live
                self.offline_threads = new_offline
        
        return (self.live_threads, self.offline_threads)
    
    def get_stats(self) -> dict:
        """Get current allocation statistics."""
        return {
            'total_threads': self.total_threads,
            'live_threads': self.live_threads,
            'offline_threads': self.offline_threads,
            'last_rebalance': self.last_rebalance,
            'rebalance_interval': self.rebalance_interval
        }


class DeviceDatabaseMonitor:
    """
    Monitors device SQLite databases and automatically processes offline messages.
    Ensures no messages are left behind in device databases.
    """
    
    def __init__(self, offline_queue, metrics, logger):
        self.offline_queue = offline_queue
        self.metrics = metrics
        self.logger = logger
        self.shutdown_event = threading.Event()
        self.scan_interval = DEVICE_DB_SCAN_INTERVAL
        self.batch_size = DEVICE_DB_BATCH_SIZE
        self.offline_queue_dir = OFFLINE_QUEUE_DIR
        
        # Statistics
        self.total_devices_scanned = 0
        self.total_messages_found = 0
        self.total_messages_processed = 0
        self.last_scan_time = 0
        
        self.logger.info("Device database monitor initialized", 
                        scan_interval=self.scan_interval,
                        batch_size=self.batch_size,
                        queue_dir=self.offline_queue_dir)
    
    def _scan_device_databases(self):
        """Scan all device SQLite databases for offline messages."""
        try:
            # Find all device database files
            db_pattern = os.path.join(self.offline_queue_dir, "*.db")
            db_files = glob.glob(db_pattern)
            
            if not db_files:
                return
            
            devices_with_messages = 0
            total_messages_found = 0
            
            for db_file in db_files:
                try:
                    device_id = os.path.basename(db_file).replace('.db', '')
                    messages_found = self._process_device_database(db_file, device_id)
                    
                    if messages_found > 0:
                        devices_with_messages += 1
                        total_messages_found += messages_found
                        
                except Exception as e:
                    self.logger.warning("Error processing device database", 
                                      db_file=db_file, error=str(e))
            
            self.total_devices_scanned = len(db_files)
            self.total_messages_found += total_messages_found
            
            if total_messages_found > 0:
                self.logger.info("Device database scan completed",
                               devices_scanned=len(db_files),
                               devices_with_messages=devices_with_messages,
                               messages_found=total_messages_found)
                
                print(f"📱 Device DB Scan: Found {total_messages_found} offline messages in {devices_with_messages}/{len(db_files)} devices")
            
        except Exception as e:
            self.logger.error("Device database scan failed", error=str(e))
    
    def _process_device_database(self, db_file, device_id):
        """Process offline messages from a specific device database."""
        try:
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()
            
            # Check if messages table exists
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='messages'
            """)
            
            if not cursor.fetchone():
                conn.close()
                return 0
            
            # Get messages in priority order (same as device logic)
            cursor.execute('''
                SELECT id, topic, payload, priority FROM messages 
                ORDER BY 
                    CASE priority 
                        WHEN 'critical' THEN 1 
                        WHEN 'high' THEN 2 
                        ELSE 3 
                    END,
                    timestamp ASC 
                LIMIT ?
            ''', (self.batch_size,))
            
            messages = cursor.fetchall()
            
            if not messages:
                conn.close()
                return 0
            
            # Process messages through offline pipeline
            processed_ids = []
            
            for msg_id, topic, payload_str, priority in messages:
                try:
                    # Parse payload
                    payload = json.loads(payload_str)
                    
                    # Add collector processing metadata
                    if 'pipeline_metadata' not in payload:
                        payload['pipeline_metadata'] = {}
                    
                    payload['pipeline_metadata'].update({
                        'source': 'device_database',
                        'device_id': device_id,
                        'original_priority': priority,
                        'collector_retrieved_at': time.time()
                    })
                    
                    # Create message data for offline queue
                    message_data = {
                        'payload': json.dumps(payload).encode(),
                        'collector_receive_time': time.time(),
                        'topic': topic
                    }
                    
                    # Add to offline queue for processing
                    try:
                        self.offline_queue.put_nowait(message_data)
                        processed_ids.append(msg_id)
                        self.metrics.record_message_received()  # Count as received message
                        
                    except Full:
                        # Offline queue full - stop processing this device for now
                        self.logger.warning("Offline queue full, stopping device DB processing",
                                          device_id=device_id)
                        break
                        
                except json.JSONDecodeError:
                    self.logger.warning("Invalid JSON in device database",
                                      device_id=device_id, msg_id=msg_id)
                    processed_ids.append(msg_id)  # Remove invalid message
                    
                except Exception as e:
                    self.logger.warning("Error processing device message",
                                      device_id=device_id, msg_id=msg_id, error=str(e))
            
            # Delete successfully processed messages from device database
            if processed_ids:
                placeholders = ','.join('?' * len(processed_ids))
                cursor.execute(f'DELETE FROM messages WHERE id IN ({placeholders})', processed_ids)
                conn.commit()
                
                self.total_messages_processed += len(processed_ids)
                
                self.logger.info("Processed device database messages",
                               device_id=device_id,
                               processed=len(processed_ids),
                               remaining=len(messages) - len(processed_ids))
            
            conn.close()
            return len(messages)
            
        except Exception as e:
            self.logger.error("Error processing device database",
                            device_id=device_id, db_file=db_file, error=str(e))
            return 0
    
    def _monitor_loop(self):
        """Main monitoring loop that scans device databases periodically."""
        self.logger.info("Device database monitor started")
        
        while not self.shutdown_event.is_set():
            try:
                start_time = time.time()
                
                # Scan all device databases
                self._scan_device_databases()
                
                self.last_scan_time = time.time()
                scan_duration = self.last_scan_time - start_time
                
                # Sleep until next scan (accounting for scan duration)
                sleep_time = max(0, self.scan_interval - scan_duration)
                
                if sleep_time > 0:
                    self.shutdown_event.wait(sleep_time)
                    
            except Exception as e:
                self.logger.error("Device database monitor loop error", error=str(e))
                self.shutdown_event.wait(5)  # Wait 5 seconds before retry
        
        self.logger.info("Device database monitor stopped")
    
    def start(self):
        """Start the device database monitoring thread."""
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        self.logger.info("Device database monitor thread started")
    
    def stop(self):
        """Stop the device database monitoring thread."""
        self.shutdown_event.set()
        if hasattr(self, 'monitor_thread'):
            self.monitor_thread.join(timeout=5)
    
    def get_stats(self):
        """Get device database monitoring statistics."""
        return {
            'total_devices_scanned': self.total_devices_scanned,
            'total_messages_found': self.total_messages_found,
            'total_messages_processed': self.total_messages_processed,
            'last_scan_time': self.last_scan_time,
            'scan_interval': self.scan_interval,
            'batch_size': self.batch_size,
            'queue_dir': self.offline_queue_dir
        }


class MQTTCollectorWithMonitoring:
    def __init__(self):
        print("=" * 70)
        print("🚀 Initializing MQTT Collector with Dual-Pipeline Optimization")
        print("=" * 70)
        
        # Initialize monitoring FIRST
        self.metrics = MetricsCollector(
            enable_prometheus=ENABLE_PROMETHEUS,
            prometheus_port=PROMETHEUS_PORT
        )
        
        # Register alert handlers
        self.metrics.register_alert_callback(print_alert_notification)
        self.metrics.register_alert_callback(self._handle_critical_alerts)
        
        # Initialize structured logger
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        self.logger = StructuredLogger('mqtt_collector', log_file=LOG_FILE)
        
        self.logger.info("Initializing MQTT Collector", 
                        mqtt_broker=MQTT_BROKER_HOST,
                        influxdb_url=INFLUXDB_URL,
                        workers=NUM_WORKER_THREADS,
                        live_queue_size=LIVE_QUEUE_MAX_SIZE,
                        offline_queue_size=OFFLINE_QUEUE_MAX_SIZE)
        
        # Initialize InfluxDB
        self._init_influxdb()
        
        # MQTT client setup
        self._init_mqtt()
        
        # DUAL QUEUES - Separate live and offline message processing
        self.live_queue = Queue(maxsize=LIVE_QUEUE_MAX_SIZE)
        self.offline_queue = Queue(maxsize=OFFLINE_QUEUE_MAX_SIZE)
        
        # Deduplication tracking
        self.seen_message_ids = set()
        self.seen_message_ids_lock = threading.Lock()
        self.max_seen_ids = 100000  # Keep last 100k message IDs
        
        # Thread management
        self.shutdown_event = threading.Event()
        self.live_workers = []
        self.offline_workers = []
        
        # Adaptive thread allocator
        self.thread_allocator = AdaptiveThreadAllocator()
        
        # Start workers
        self._start_workers()
        
        # Start stats reporter
        self.stats_thread = threading.Thread(target=self._stats_reporter, daemon=True)
        self.stats_thread.start()
        
        # Start thread rebalancer
        self.rebalance_thread = threading.Thread(target=self._rebalance_loop, daemon=True)
        self.rebalance_thread.start()
        
        # Start heartbeat publisher
        print("   Starting heartbeat publisher...")
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        print(f"   Heartbeat thread started: {self.heartbeat_thread.is_alive()}")
        
        # Device database monitor - DISABLED FOR LIVE DATA TESTING
        # print("   Starting device database monitor...")
        # self.device_db_monitor = DeviceDatabaseMonitor(
        #     offline_queue=self.offline_queue,
        #     metrics=self.metrics,
        #     logger=self.logger
        # )
        # self.device_db_monitor.start()
        # print(f"   Device DB monitor: Scanning every {DEVICE_DB_SCAN_INTERVAL}s for offline messages")
        
        # Start stats HTTP server for dashboard
        self._start_stats_server()
        
        # Record start time for uptime calculation
        self._start_time = time.time()
        
        print("✅ Collector initialized successfully")
        print(f"   Live Queue: {LIVE_QUEUE_MAX_SIZE:,} | Offline Queue: {OFFLINE_QUEUE_MAX_SIZE:,}")
        print(f"   Initial Allocation: {self.thread_allocator.live_threads} live, {self.thread_allocator.offline_threads} offline")
        print(f"   Heartbeat: Every {os.getenv('COLLECTOR_HEARTBEAT_INTERVAL', '1.5')}s on topic 'collector/heartbeat'")
        print("=" * 70)
    
    def _init_influxdb(self):
        """Initialize InfluxDB connections with separate clients for live and offline pipelines."""
        try:
            # LIVE CLIENT - Optimized for ULTRA LOW LATENCY
            self.influx_client_live = InfluxDBClient(
                url=INFLUXDB_URL,
                token=INFLUXDB_TOKEN,
                org=INFLUXDB_ORG,
                timeout=2000,                  # 2 second timeout for fast failure
                enable_gzip=False,             # Disable compression for speed
                connection_pool_maxsize=10,    # More connections for parallelism
                retries=1                      # Minimal retries for speed
            )
            
            # Test live connection
            self.influx_client_live.ping()
            self.logger.info("InfluxDB LIVE client connection successful", connection_pool_size=5)
            
            # OFFLINE CLIENT - Optimized for throughput with reasonable latency
            offline_pool_size = int(os.getenv("INFLUXDB_OFFLINE_CONNECTION_POOL_SIZE", "2"))
            self.influx_client_offline = InfluxDBClient(
                url=INFLUXDB_URL,
                token=INFLUXDB_TOKEN,
                org=INFLUXDB_ORG,
                timeout=5000,                  # 5 second timeout (faster than before)
                enable_gzip=True,              # Keep compression for offline bulk writes
                connection_pool_maxsize=offline_pool_size,
                retries=2                      # Reduced retries
            )
            
            # Test offline connection
            self.influx_client_offline.ping()
            self.logger.info("InfluxDB OFFLINE client connection successful", connection_pool_size=offline_pool_size)
            
            # Verify bucket exists (only need to check once)
            buckets_api = self.influx_client_live.buckets_api()
            bucket = buckets_api.find_bucket_by_name(INFLUXDB_BUCKET)
            if not bucket:
                raise ValueError(f"Bucket '{INFLUXDB_BUCKET}' does not exist")
            self.logger.info("Bucket verified", bucket=INFLUXDB_BUCKET)
            
        except Exception as e:
            self.logger.error("InfluxDB initialization failed", error=str(e))
            raise

        # Create SEPARATE write APIs for live and offline clients
        # Live write API - NO batching (async writer handles it)
        self.write_api_live = self.influx_client_live.write_api()

        
        # Offline write API - NO batching (async writer handles it)
        self.write_api_offline = self.influx_client_offline.write_api()

        
        # Initialize DUAL async writers with OPTIMIZED LOW LATENCY settings
        # Live writer: ULTRA AGGRESSIVE settings for sub-500ms latency
        live_batch_size = int(os.getenv("INFLUXDB_LIVE_BATCH_SIZE", "10"))  # Smaller batches
        live_flush_ms = int(os.getenv("INFLUXDB_LIVE_FLUSH_INTERVAL", "25"))  # 25ms flush
        
        self.live_writer = AsyncInfluxWriter(
            write_api=self.write_api_live,  # Uses LIVE client with 10 connections
            bucket=INFLUXDB_BUCKET,
            max_workers=12,                 # More workers for parallelism
            batch_size=live_batch_size,
            flush_interval=live_flush_ms / 1000.0,
            max_queue_size=1000,            # Smaller queue for faster processing
            success_callback=self._live_write_success_callback,
            error_callback=self._write_error_callback
        )
        
        # Offline writer: EXTREME BULK batching, uses OFFLINE client
        offline_batch_size = int(os.getenv("INFLUXDB_OFFLINE_BATCH_SIZE", "5000"))
        offline_flush_ms = int(os.getenv("INFLUXDB_OFFLINE_FLUSH_INTERVAL", "30000"))
        
        self.offline_writer = AsyncInfluxWriter(
            write_api=self.write_api_offline,  # Uses OFFLINE client with 2 connections
            bucket=INFLUXDB_BUCKET,
            max_workers=4,
            batch_size=offline_batch_size,
            flush_interval=offline_flush_ms / 1000.0,
            max_queue_size=50000,
            success_callback=self._offline_write_success_callback,
            error_callback=self._write_error_callback
        )
        
        self.logger.info("Dual AsyncInfluxWriters initialized with SEPARATE clients")
        self.logger.info("Live writer config",
                        client="live",
                        connection_pool=5,
                        workers=8,
                        batch_size=live_batch_size,
                        flush_interval_ms=live_flush_ms)
        self.logger.info("Offline writer config",
                        client="offline",
                        connection_pool=offline_pool_size,
                        workers=4,
                        batch_size=offline_batch_size,
                        flush_interval_ms=offline_flush_ms)
        
        print(f"   InfluxDB Clients: LIVE (5 conn) + OFFLINE ({offline_pool_size} conn) - ISOLATED")
        print(f"   Live batching: {live_batch_size} msgs every {live_flush_ms}ms")
        print(f"   Offline batching: {offline_batch_size} msgs every {offline_flush_ms}ms ({offline_flush_ms/1000}s)")

    
    def _live_write_success_callback(self, batch_size: int, duration: float, batch_metadata: list):
        """Called when live async write succeeds."""
        # Calculate latencies from metadata
        write_end_time = time.time()
        
        for metadata in batch_metadata:
            if 'publish_timestamps' in metadata:
                for publish_ts in metadata['publish_timestamps']:
                    if publish_ts:
                        full_latency = write_end_time - publish_ts
                        if 0 < full_latency < 60:
                            self.metrics.record_message_processed(full_latency, pipeline='live')
        
        if duration > 0.5:  # Log slow live writes
            self.logger.warning("Slow live batch write", batch_size=batch_size, duration=duration)
    
    def _offline_write_success_callback(self, batch_size: int, duration: float, batch_metadata: list):
        """Called when offline async write succeeds."""
        # Calculate latencies from metadata
        write_end_time = time.time()
        
        for metadata in batch_metadata:
            if 'publish_timestamps' in metadata:
                for publish_ts in metadata['publish_timestamps']:
                    if publish_ts:
                        full_latency = write_end_time - publish_ts
                        if 0 < full_latency < 60:
                            self.metrics.record_message_processed(full_latency, pipeline='offline')
        
        if duration > 5.0:  # Log very slow offline writes (expected to be slower)
            self.logger.warning("Very slow offline batch write", batch_size=batch_size, duration=duration)
    
    def _write_error_callback(self, exception: Exception, batch_size: int, batch_metadata: list):
        """Called when async write fails."""
        # Record errors for all points in failed batch
        for _ in range(batch_size):
            self.metrics.record_error()
        
        self.logger.error("Async write failed", 
                         error=str(exception),
                         batch_size=batch_size)
    
    def _init_mqtt(self):
        """Initialize MQTT client with stability-focused configuration."""
        # Create unique client ID to prevent conflicts
        client_id = f"{MQTT_CLIENT_ID}-{os.getpid()}-{int(time.time())}"
        
        self.mqtt_client = mqtt.Client(
            client_id=client_id,
            clean_session=True  # Use clean session to reduce broker memory pressure
        )
        
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_disconnect = self._on_disconnect
        
        # Configure TLS if enabled
        if MQTT_USE_TLS:
            try:
                if MQTT_CA_CERTS:
                    self.mqtt_client.tls_set(
                        ca_certs=MQTT_CA_CERTS,
                        certfile=MQTT_CERTFILE,
                        keyfile=MQTT_KEYFILE,
                        cert_reqs=ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else ssl.CERT_NONE
                    )
                else:
                    self.mqtt_client.tls_set(
                        certfile=MQTT_CERTFILE,
                        keyfile=MQTT_KEYFILE,
                        cert_reqs=ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else ssl.CERT_NONE
                    )
                
                if MQTT_TLS_INSECURE:
                    self.mqtt_client.tls_insecure_set(True)
                    self.logger.warning("TLS insecure mode enabled")
                
                self.logger.info("TLS encryption enabled")
            except Exception as e:
                self.logger.error("TLS configuration failed", error=str(e))
                raise
        
        # Configure authentication
        if MQTT_USERNAME and MQTT_PASSWORD:
            self.mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            self.logger.info("MQTT authentication configured")
        
        # Configure reconnection with stability settings from environment
        mqtt_keepalive = int(os.getenv("MQTT_KEEPALIVE", "60"))
        reconnect_min = int(os.getenv("MQTT_RECONNECT_MIN_DELAY", "10"))
        reconnect_max = int(os.getenv("MQTT_RECONNECT_MAX_DELAY", "120"))
        socket_timeout = int(os.getenv("MQTT_SOCKET_TIMEOUT", "60"))
        
        self.mqtt_client.reconnect_delay_set(min_delay=reconnect_min, max_delay=reconnect_max)
        self.mqtt_client.socket_timeout = socket_timeout
        self.mqtt_client.socket_keepalive = mqtt_keepalive
    
    def _handle_critical_alerts(self, alert):
        """Handle critical alerts - log and take action."""
        self.logger.critical(
            f"Critical alert: {alert.message}",
            severity=alert.severity,
            metric=alert.metric_name,
            value=alert.value,
            threshold=alert.threshold
        )
        
        if alert.metric_name == 'drop_rate_percent' and alert.value > 5.0:
            self.logger.critical("SEVERE: High drop rate - system overwhelmed!")
    
    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            protocol = "TLS" if MQTT_USE_TLS else "TCP"
            self.logger.info("Connected to MQTT broker",
                           host=MQTT_BROKER_HOST,
                           port=MQTT_BROKER_PORT,
                           protocol=protocol)
            
            # Subscribe to topics with QoS 1
            client.subscribe("device/data/+", qos=1)
            client.subscribe("device/offline/+", qos=1)  # Event-driven offline notifications
            
            self.logger.info("Subscribed to MQTT topics", 
                           topics=["device/data/+", "device/offline/+"],
                           qos=1)
            
            print(f"✅ Connected to MQTT broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        else:
            error_messages = {
                1: "incorrect protocol version",
                2: "invalid client identifier",
                3: "server unavailable",
                4: "bad username or password",
                5: "not authorized"
            }
            error_msg = error_messages.get(rc, f"unknown error ({rc})")
            self.logger.error("MQTT connection failed", 
                            return_code=rc,
                            error=error_msg)
            print(f"❌ MQTT connection failed: {error_msg}")
    
    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnect callback."""
        if rc != 0:
            self.logger.warning("MQTT disconnected unexpectedly", return_code=rc)
            print(f"⚠️  MQTT disconnected (rc={rc}), reconnecting...")
    
    def _on_message(self, client, userdata, msg):
        """MQTT message callback - route to appropriate queue with deduplication."""
        try:
            # EVENT-DRIVEN: Check if this is an offline notification
            if msg.topic.startswith("device/offline/"):
                self._handle_offline_notification(msg)
                return
            
            # Record message received
            self.metrics.record_message_received()
            
            collector_receive_time = time.time()
            
            message_data = {
                'payload': msg.payload,
                'collector_receive_time': collector_receive_time,
                'topic': msg.topic
            }
            
            # Route to appropriate queue
            self._route_message(message_data)
                
        except Exception as e:
            self.metrics.record_error()
            self.logger.error("Error in message callback", error=str(e))
    
    def _handle_offline_notification(self, msg):
        """Handle device offline data notification - EVENT-DRIVEN approach."""
        try:
            # Extract device_id from topic: device/offline/vehicle_001
            device_id = msg.topic.split('/')[-1]
            notification = json.loads(msg.payload.decode())
            queue_size = notification.get('queue_size', 0)
            
            if queue_size == 0:
                return
            
            self.logger.info(f"📬 Offline notification from {device_id}: {queue_size} messages")
            print(f"📬 {device_id} has {queue_size} offline messages - starting processing...")
            
            # Get device database (use devices/offline_queues/)
            offline_queue_dir = os.getenv("OFFLINE_QUEUE_DIR", "devices/offline_queues")
            device_db_path = os.path.join(offline_queue_dir, f"{device_id}.db")
            
            if not os.path.exists(device_db_path):
                self.logger.warning(f"Device DB not found: {device_db_path}")
                return
            
            # Process in background thread to avoid blocking
            import threading
            thread = threading.Thread(
                target=self._process_device_offline_queue,
                args=(device_id, device_db_path, queue_size),
                daemon=True
            )
            thread.start()
            
        except Exception as e:
            self.logger.error(f"Error handling offline notification: {e}")
    
    def _process_device_offline_queue(self, device_id, db_path, total_size):
        """Process offline messages for a specific device in background."""
        try:
            import sqlite3
            import time
            
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            batch_size = int(os.getenv("DEVICE_DB_BATCH_SIZE", "50"))
            processed = 0
            
            while processed < total_size:
                # Fetch batch of messages
                cursor.execute("""
                    SELECT id, topic, payload 
                    FROM messages 
                    ORDER BY id ASC 
                    LIMIT ?
                """, (batch_size,))
                batch = cursor.fetchall()
                
                if not batch:
                    break
                
                successfully_queued_ids = []
                for msg_id, topic, payload_json in batch:
                    try:
                        payload = json.loads(payload_json)
                        
                        # Queue to offline pipeline
                        message_data = {
                            'payload': json.dumps(payload).encode(),
                            'collector_receive_time': time.time(),
                            'topic': topic
                        }
                        
                        # Try to queue (non-blocking)
                        try:
                            self.offline_queue.put_nowait(message_data)
                            successfully_queued_ids.append(msg_id)
                        except Full:
                            # Queue full - log and stop processing this batch
                            self.logger.warning(f"Offline queue full! Processed {processed}/{total_size}, current batch: {len(successfully_queued_ids)}/{len(batch)}")
                            print(f"⚠️ Offline queue full at {processed}/{total_size} messages - will retry later")
                            break
                            
                    except Exception as ex:
                        self.logger.error(f"Error processing offline message: {ex}")
                        successfully_queued_ids.append(msg_id)  # Delete corrupted
                
                # Delete successfully queued messages
                if successfully_queued_ids:
                    placeholders = ','.join('?' * len(successfully_queued_ids))
                    cursor.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", successfully_queued_ids)
                    conn.commit()
                    processed += len(successfully_queued_ids)
                    
                    # Only print at milestones to avoid spam
                    progress_pct = (processed / total_size) * 100
                    if processed == total_size or progress_pct % 25 < (10 / total_size * 100):
                        print(f"🗑️ Deleted {processed}/{total_size} messages from SQLite ({progress_pct:.0f}%)")
                
                time.sleep(0.05)  # Small delay between batches
            
            conn.close()
            print(f"✅ Processed {processed}/{total_size} offline messages from {device_id}")
            
        except Exception as e:
            self.logger.error(f"Error processing {device_id} offline queue: {e}")
    
    def _route_message(self, message_data):
        """Route message to appropriate queue with deduplication."""
        try:
            # Parse payload to check for pipeline metadata
            payload = json.loads(message_data['payload'].decode())
            
            # Generate message ID for deduplication
            device_id = payload.get('device_id', 'unknown')
            timestamp = payload.get('timestamp', time.time())
            msg_id = f"{device_id}_{timestamp}"
            
            # OPTIMIZED DEDUPLICATION - Fast check with minimal lock time
            with self.seen_message_ids_lock:
                if msg_id in self.seen_message_ids:
                    self.metrics.record_duplicate()
                    return
                
                self.seen_message_ids.add(msg_id)
                
                # OPTIMIZED: Only cleanup every 1000 messages to reduce lock contention
                if len(self.seen_message_ids) > self.max_seen_ids and len(self.seen_message_ids) % 1000 == 0:
                    # Quick cleanup - remove 10% of oldest IDs
                    ids_to_remove = list(self.seen_message_ids)[:self.max_seen_ids // 10]
                    for old_id in ids_to_remove:
                        self.seen_message_ids.discard(old_id)
            
            # Determine routing based on pipeline metadata
            pipeline_meta = payload.get('pipeline_metadata', {})
            is_offline_flush = 'queued_at' in pipeline_meta
            
            # Route to appropriate queue
            try:
                if is_offline_flush:
                    self.offline_queue.put_nowait(message_data)
                else:
                    self.live_queue.put_nowait(message_data)
                
                # Update queue depth metrics
                live_depth = self.live_queue.qsize()
                offline_depth = self.offline_queue.qsize()
                total_depth = live_depth + offline_depth
                self.metrics.record_queue_depth(total_depth, live_depth, offline_depth)
                
            except Full:
                # Queue full - record drop
                self.metrics.record_drop()
                
                # Log only every 100 drops to avoid spam
                if self.metrics.dropped_messages % 100 == 0:
                    self.logger.warning("Message queue full - dropping messages",
                                      live_queue=self.live_queue.qsize(),
                                      offline_queue=self.offline_queue.qsize(),
                                      dropped_total=self.metrics.dropped_messages)
                
        except json.JSONDecodeError:
            self.metrics.record_error()
        except Exception as e:
            self.metrics.record_error()
            self.logger.error("Error routing message", error=str(e))
    
    def _process_message(self, message_data):
        """Process message with latency tracking."""
        try:
            payload = json.loads(message_data['payload'].decode())
            collector_receive_time = message_data['collector_receive_time']
            device_id = payload.get('device_id')
            
            if not device_id:
                self.metrics.record_error()
                return None
            
            # Calculate end-to-end latency
            publish_timestamp = payload.get('timestamp')
            latency_seconds = None
            if publish_timestamp:
                latency_seconds = collector_receive_time - float(publish_timestamp)
            
            # Create InfluxDB point
            point = Point("device_data") \
                .tag("device_id", device_id) \
                .tag("collector", "python") \
                .field("collector_receive_time", float(collector_receive_time))
            
            # Add all numeric fields
            field_mappings = {
                'timestamp': 'publish_timestamp',
                'speed': 'speed',
                'cpu_usage': 'cpu_usage',
                'ram_usage': 'ram_usage',
                'memory_total': 'memory_total',
                'memory_used': 'memory_used',
                'memory_available': 'memory_available',
                'memory_percent': 'memory_percent',
                'disk_total': 'disk_total',
                'disk_used': 'disk_used',
                'disk_free': 'disk_free',
                'disk_percent': 'disk_percent',
                'network_bytes_sent': 'network_bytes_sent',
                'network_bytes_recv': 'network_bytes_recv',
                'detection_confidence': 'detection_confidence'
            }
            
            for src, dst in field_mappings.items():
                if src in payload:
                    try:
                        point = point.field(dst, float(payload[src]))
                    except (ValueError, TypeError):
                        pass  # Skip invalid numeric values
            
            # Add latency as field for querying
            if latency_seconds and latency_seconds > 0:
                point = point.field("latency_seconds", latency_seconds)
            
            # Detection label tag
            if "detection_label" in payload:
                point = point.tag("detection_label", str(payload["detection_label"]))
            
            # Return point, MQTT latency, and publish_timestamp for full latency calculation
            publish_ts_float = float(publish_timestamp) if publish_timestamp else None
            return (point, latency_seconds, publish_ts_float)
            
        except json.JSONDecodeError:
            self.metrics.record_error()
            return None
        except Exception as e:
            self.metrics.record_error()
            # Log only every 100 errors
            if self.metrics.error_messages % 100 == 0:
                self.logger.error("Message processing error", 
                                error=str(e),
                                error_count=self.metrics.error_messages)
            return None
    
    def _worker_thread(self):
        """Legacy worker thread - replaced by _live_worker and _offline_worker."""
        pass
    
    def _live_worker(self):
        """Worker thread for live messages - HIGH PRIORITY, LOW LATENCY."""
        thread_id = threading.get_ident()
        self.logger.info("Live worker thread started", thread_id=thread_id)
        
        try:
            while not self.shutdown_event.is_set():
                batch = []
                
                # Try to get first message with timeout
                try:
                    msg = self.live_queue.get(timeout=0.5)
                    batch.append(msg)
                    
                    # Drain additional messages for batching (up to LIVE_BATCH_SIZE)
                    for _ in range(LIVE_BATCH_SIZE - 1):
                        try:
                            msg = self.live_queue.get_nowait()
                            batch.append(msg)
                        except Empty:
                            break
                    
                except Empty:
                    continue
                
                # Process batch
                self._process_batch(batch, pipeline='live', queue=self.live_queue)
                
                # Update queue depth after processing
                live_depth = self.live_queue.qsize()
                offline_depth = self.offline_queue.qsize()
                self.metrics.record_queue_depth(live_depth + offline_depth, live_depth, offline_depth)
        
        except Exception as e:
            self.logger.error("Live worker thread crashed", 
                            thread_id=thread_id,
                            error=str(e))
        finally:
            self.logger.info("Live worker thread stopped", thread_id=thread_id)
    
    def _offline_worker(self):
        """Worker thread for offline messages - LOWER PRIORITY, HIGHER THROUGHPUT."""
        thread_id = threading.get_ident()
        self.logger.info("Offline worker thread started", thread_id=thread_id)
        
        # Get configurable throttling thresholds from environment
        pause_on_live_queue = int(os.getenv("OFFLINE_PAUSE_ON_LIVE_QUEUE_THRESHOLD", "50"))
        pause_on_live_p95_ms = int(os.getenv("OFFLINE_PAUSE_ON_LIVE_P95_MS", "300"))
        
        # Rate limiting configuration
        max_msgs_per_minute = int(os.getenv("OFFLINE_MAX_MESSAGES_PER_MINUTE", "10000"))
        min_batch_delay = float(os.getenv("OFFLINE_MIN_BATCH_DELAY_SECONDS", "5.0"))
        
        # Rate limiting tracking
        messages_processed_this_minute = 0
        minute_start_time = time.time()
        last_batch_time = 0
        
        try:
            while not self.shutdown_event.is_set():
                # RATE LIMIT: Check if we've exceeded messages per minute
                current_time = time.time()
                elapsed_in_minute = current_time - minute_start_time
                
                if elapsed_in_minute >= 60:
                    # Reset counter every minute
                    messages_processed_this_minute = 0
                    minute_start_time = current_time
                    self.logger.info(f"Offline rate limit reset - processed {messages_processed_this_minute} msgs in last minute")
                
                if messages_processed_this_minute >= max_msgs_per_minute:
                    # Hit rate limit - wait until next minute
                    wait_time = 60 - elapsed_in_minute
                    self.logger.info(f"Offline rate limit reached ({messages_processed_this_minute}/{max_msgs_per_minute} msgs/min) - waiting {wait_time:.1f}s")
                    time.sleep(min(wait_time, 5.0))  # Wait max 5s at a time
                    continue
                
                # RATE LIMIT: Enforce minimum delay between batches
                time_since_last_batch = current_time - last_batch_time
                if last_batch_time > 0 and time_since_last_batch < min_batch_delay:
                    sleep_time = min_batch_delay - time_since_last_batch
                    time.sleep(sleep_time)
                
                # LIVE-FIRST SAFEGUARD: Pause offline if live queue is building up
                live_queue_size = self.live_queue.qsize()
                if live_queue_size > pause_on_live_queue:
                    self.logger.info(f"Pausing offline processing - live queue has {live_queue_size} messages")
                    time.sleep(1.0)
                    continue
                
                # LIVE-FIRST SAFEGUARD: Pause offline if live latency is high
                try:
                    stats = self.metrics.get_stats()
                    live_latency = stats.get('latency', {}).get('live', {})
                    live_p95 = live_latency.get('p95')
                    
                    if live_p95 and live_p95 > pause_on_live_p95_ms:
                        self.logger.info(f"Pausing offline processing - live P95 latency is {live_p95:.0f}ms (threshold: {pause_on_live_p95_ms}ms)")
                        time.sleep(2.0)
                        continue
                except Exception:
                    pass  # Continue if stats unavailable
                
                batch = []
                
                # Try to get first message with timeout
                try:
                    msg = self.offline_queue.get(timeout=0.5)
                    batch.append(msg)
                    
                    # Drain additional messages for batching (up to OFFLINE_BATCH_SIZE)
                    # But respect rate limit - don't batch more than we can process this minute
                    remaining_quota = max_msgs_per_minute - messages_processed_this_minute
                    max_batch = min(OFFLINE_BATCH_SIZE - 1, remaining_quota - 1)
                    
                    for _ in range(max_batch):
                        try:
                            msg = self.offline_queue.get_nowait()
                            batch.append(msg)
                        except Empty:
                            break
                    
                except Empty:
                    continue
                
                # Process batch
                batch_size = len(batch)
                self._process_batch(batch, pipeline='offline', queue=self.offline_queue)
                
                # Update rate limiting counters
                messages_processed_this_minute += batch_size
                last_batch_time = time.time()
                
                # Log rate limiting status periodically
                if messages_processed_this_minute % 1000 == 0 or messages_processed_this_minute >= max_msgs_per_minute * 0.8:
                    self.logger.info(f"Offline processing: {messages_processed_this_minute}/{max_msgs_per_minute} msgs this minute ({messages_processed_this_minute/max_msgs_per_minute*100:.1f}%)")
                
                # Rate limit offline processing to prevent overwhelming InfluxDB
                time.sleep(OFFLINE_BATCH_DELAY)
                
                # Update queue depth after processing
                live_depth = self.live_queue.qsize()
                offline_depth = self.offline_queue.qsize()
                self.metrics.record_queue_depth(live_depth + offline_depth, live_depth, offline_depth)
        
        except Exception as e:
            self.logger.error("Offline worker thread crashed", 
                            thread_id=thread_id,
                            error=str(e))
        finally:
            self.logger.info("Offline worker thread stopped", thread_id=thread_id)
    
    def _process_batch(self, batch, pipeline='live', queue=None):
        """Process batch with pipeline-specific settings using async writer."""
        points = []
        publish_timestamps = []
        
        for message_data in batch:
            result = self._process_message(message_data)
            if result:
                point, mqtt_latency, publish_ts = result
                
                # Tag with pipeline
                point = point.tag("pipeline", pipeline)
                points.append(point)
                
                if publish_ts:
                    publish_timestamps.append(publish_ts)
            
            # Mark as done in appropriate queue
            if queue:
                queue.task_done()
        
        # Write batch asynchronously (non-blocking!)
        if points:
            metadata = {
                'pipeline': pipeline,
                'publish_timestamps': publish_timestamps,
                'batch_size': len(points)
            }
            
            # DUAL WRITER: Route to appropriate writer based on pipeline
            if pipeline == 'offline':
                success = self.offline_writer.write_async(points, metadata)
            else:
                success = self.live_writer.write_async(points, metadata)
            
            if not success:
                # Queue full - record as errors
                for _ in points:
                    self.metrics.record_error()
                
                self.logger.warning("Async write queue full",
                                  pipeline=pipeline,
                                  batch_size=len(points))
    
    def _start_workers(self):
        """Start initial worker threads (all live workers)."""
        # Start with all threads as live workers
        for i in range(NUM_WORKER_THREADS):
            worker = threading.Thread(target=self._live_worker, daemon=True)
            worker.start()
            self.live_workers.append(worker)
        
        self.logger.info("Worker threads started", 
                        live_workers=len(self.live_workers),
                        offline_workers=len(self.offline_workers))
        print(f"   Started {len(self.live_workers)} live workers, {len(self.offline_workers)} offline workers")
    
    def _rebalance_loop(self):
        """Background thread that periodically rebalances thread allocation."""
        self.logger.info("Thread rebalancer started")
        
        # Get safeguard thresholds
        pause_on_live_queue = int(os.getenv("OFFLINE_PAUSE_ON_LIVE_QUEUE_THRESHOLD", "50"))
        pause_on_live_p95_ms = int(os.getenv("OFFLINE_PAUSE_ON_LIVE_P95_MS", "500"))
        
        while not self.shutdown_event.is_set():
            try:
                time.sleep(THREAD_REBALANCE_INTERVAL)
                
                # SAFEGUARD CHECK: Force 8L/0O if live is struggling
                live_queue_size = self.live_queue.qsize()
                live_needs_help = False
                
                # Check if live queue is building up
                if live_queue_size > pause_on_live_queue:
                    live_needs_help = True
                    self.logger.warning(f"Live safeguard triggered! Queue={live_queue_size}, forcing 8L/0O")
                
                # Check if live latency is high
                try:
                    stats = self.metrics.get_stats()
                    live_p95 = stats.get('latency', {}).get('live', {}).get('p95')
                    if live_p95 and live_p95 > pause_on_live_p95_ms:
                        live_needs_help = True
                        self.logger.warning(f"Live safeguard triggered! P95={live_p95:.0f}ms, forcing 8L/0O")
                except Exception:
                    pass
                
                # Determine target allocation
                if live_needs_help:
                    # SAFEGUARD: Give all threads to live immediately
                    target_live, target_offline = 8, 0
                else:
                    # Normal allocation based on offline queue size
                    offline_size = self.offline_queue.qsize()
                    target_live, target_offline = self.thread_allocator.get_allocation(offline_size)
                
                # Count active workers
                current_live = len([w for w in self.live_workers if w.is_alive()])
                current_offline = len([w for w in self.offline_workers if w.is_alive()])
                
                # Adjust if needed
                if target_live != current_live or target_offline != current_offline:
                    self._adjust_workers(target_live, target_offline, current_live, current_offline)
                    
                    # Update metrics
                    self.metrics.record_thread_allocation(target_live, target_offline)
                
            except Exception as e:
                self.logger.error("Rebalance loop error", error=str(e))
    
    def _adjust_workers(self, target_live, target_offline, current_live, current_offline):
        """Adjust worker thread counts to match target allocation."""
        self.logger.info("Adjusting worker allocation",
                        current_live=current_live,
                        current_offline=current_offline,
                        target_live=target_live,
                        target_offline=target_offline)
        
        # Calculate how many workers to convert
        live_to_offline = current_live - target_live
        offline_to_live = current_offline - target_offline
        
        if live_to_offline > 0:
            # Convert live workers to offline workers
            # Only start new offline workers if we don't have enough
            current_offline_alive = len([w for w in self.offline_workers if w.is_alive()])
            needed_offline = target_offline - current_offline_alive
            
            if needed_offline > 0:
                for i in range(needed_offline):
                    worker = threading.Thread(target=self._offline_worker, daemon=True)
                    worker.start()
                    self.offline_workers.append(worker)
                
                print(f"   Rebalanced: Started {needed_offline} offline workers (now {target_live}L/{target_offline}O)")
            
            # Clean up dead threads from lists
            self.live_workers = [w for w in self.live_workers if w.is_alive()]
            self.offline_workers = [w for w in self.offline_workers if w.is_alive()]
        
        elif offline_to_live > 0:
            # Convert offline workers to live workers
            # Only start new live workers if we don't have enough
            current_live_alive = len([w for w in self.live_workers if w.is_alive()])
            needed_live = target_live - current_live_alive
            
            if needed_live > 0:
                for i in range(needed_live):
                    worker = threading.Thread(target=self._live_worker, daemon=True)
                    worker.start()
                    self.live_workers.append(worker)
                
                print(f"   Rebalanced: Started {needed_live} live workers (now {target_live}L/{target_offline}O)")
            
            # Clean up dead threads from lists
            self.live_workers = [w for w in self.live_workers if w.is_alive()]
            self.offline_workers = [w for w in self.offline_workers if w.is_alive()]
    
    def _start_stats_server(self):
        """Start a simple HTTP server to expose MetricsCollector stats."""
        stats_app = Flask(__name__)
        
        @stats_app.route('/stats')
        def get_stats():
            """Get current metrics stats from MetricsCollector."""
            try:
                stats = self.metrics.get_stats()
                
                # Device database monitor stats - DISABLED
                # if hasattr(self, 'device_db_monitor'):
                #     stats['device_database_monitor'] = self.device_db_monitor.get_stats()
                
                return jsonify(stats)
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        
        def run_server():
            stats_app.run(host='0.0.0.0', port=STATS_SERVER_PORT, debug=False, use_reloader=False)
        
        stats_thread = Thread(target=run_server, daemon=True)
        stats_thread.start()
        self.logger.info(f"Stats server started on port {STATS_SERVER_PORT}")
        print(f"   📊 Stats API: http://localhost:{STATS_SERVER_PORT}/stats")
    
    def _heartbeat_loop(self):
        """Publish collector heartbeat every 1-2 seconds to keep connection alive."""
        heartbeat_interval = float(os.getenv("COLLECTOR_HEARTBEAT_INTERVAL", "1.5"))  # 1.5 seconds default
        
        self.logger.info("Heartbeat publisher started", interval_seconds=heartbeat_interval)
        
        while not self.shutdown_event.is_set():
            try:
                # Create heartbeat payload
                current_time = time.time()
                heartbeat_payload = {
                    "collector_id": "mqtt-collector-python",
                    "timestamp": current_time,
                    "status": "alive",
                    "uptime_seconds": current_time - getattr(self, '_start_time', current_time),
                    "connection_status": "connected" if hasattr(self, 'mqtt_client') else "disconnected",
                    "stats": {
                        "total_messages": self.metrics.total_messages,
                        "processed_messages": self.metrics.processed_messages,
                        "error_messages": self.metrics.error_messages,
                        "live_queue_depth": self.live_queue.qsize(),
                        "offline_queue_depth": self.offline_queue.qsize(),
                        "live_threads": self.thread_allocator.live_threads,
                        "offline_threads": self.thread_allocator.offline_threads
                    }
                }
                
                # Publish heartbeat to dedicated topic
                heartbeat_topic = "collector/heartbeat"
                
                try:
                    if hasattr(self, 'mqtt_client'):
                        result = self.mqtt_client.publish(
                            heartbeat_topic, 
                            json.dumps(heartbeat_payload), 
                            qos=0,  # Use QoS 0 for heartbeat to reduce overhead
                            retain=True  # Retain last heartbeat for monitoring
                        )
                        
                        # Only log successful heartbeats occasionally to reduce spam
                        if self.metrics.total_messages % 100 == 0:  # Every 100 messages
                            self.logger.debug("Heartbeat published", 
                                            topic=heartbeat_topic,
                                            uptime=heartbeat_payload["uptime_seconds"])
                
                except Exception as e:
                    # Don't spam logs with heartbeat errors, just count them
                    if not hasattr(self, '_heartbeat_errors'):
                        self._heartbeat_errors = 0
                    self._heartbeat_errors += 1
                    
                    # Log only every 10th error to reduce spam
                    if self._heartbeat_errors % 10 == 0:
                        self.logger.warning("Heartbeat publish failed", 
                                          error=str(e),
                                          error_count=self._heartbeat_errors)
                
                # Sleep until next heartbeat
                time.sleep(heartbeat_interval)
                
            except Exception as e:
                self.logger.error("Heartbeat loop error", error=str(e))
                time.sleep(heartbeat_interval)
        
        self.logger.info("Heartbeat publisher stopped")

    def _stats_reporter(self):
        """Periodic stats reporting with pipeline-specific metrics."""
        while not self.shutdown_event.is_set():
            time.sleep(30)  # Report every 30 seconds
            
            try:
                stats = self.metrics.get_stats()
                
                # Get thread allocation
                alloc = self.thread_allocator.get_stats()
                
                # Device database monitor stats - DISABLED
                # db_monitor_stats = self.device_db_monitor.get_stats()
                
                # Log comprehensive stats
                self.logger.info("Periodic statistics report",
                               throughput=stats['rates']['throughput_msg_per_sec'],
                               live_queue=self.live_queue.qsize(),
                               offline_queue=self.offline_queue.qsize(),
                               error_rate=stats['rates']['error_rate_percent'],
                               drop_rate=stats['rates']['drop_rate_percent'],
                               total_messages=stats['counters']['total_messages'],
                               live_processed=stats['counters']['live_messages_processed'],
                               offline_processed=stats['counters']['offline_messages_processed'],
                               live_threads=alloc['live_threads'],
                               offline_threads=alloc['offline_threads'])
                
                # Console summary
                lat = stats['latency']
                live_lat = stats.get('live_latency')
                offline_lat = stats.get('offline_latency')
                
                print(f"\n📊 Stats ({datetime.now().strftime('%H:%M:%S')})")
                print(f"   Total: {stats['counters']['total_messages']:,} | Processed: {stats['counters']['processed_messages']:,}")
                print(f"   Live: {stats['counters']['live_messages_processed']:,} | Offline: {stats['counters']['offline_messages_processed']:,} | Duplicates: {stats['counters']['duplicate_messages']:,}")
                print(f"   Rate: {stats['rates']['throughput_msg_per_sec']:.1f} msg/s")
                print(f"   Queues: Live={self.live_queue.qsize():,} Offline={self.offline_queue.qsize():,}")
                print(f"   Threads: {alloc['live_threads']}L / {alloc['offline_threads']}O")
                print(f"   Errors: {stats['rates']['error_rate_percent']:.2f}% | Drops: {stats['rates']['drop_rate_percent']:.2f}%")
                
                # Device database monitor stats - DISABLED
                # if db_monitor_stats['total_messages_found'] > 0:
                #     print(f"   📱 Device DBs: {db_monitor_stats['total_messages_processed']:,} processed from {db_monitor_stats['total_devices_scanned']} devices")
                
                if lat:
                    print(f"   Overall Latency: P50={lat['p50']:.0f}ms P95={lat['p95']:.0f}ms P99={lat['p99']:.0f}ms")
                
                if live_lat:
                    print(f"   Live Latency: P50={live_lat['p50']:.0f}ms P95={live_lat['p95']:.0f}ms P99={live_lat['p99']:.0f}ms")
                
                if offline_lat:
                    print(f"   Offline Latency: P50={offline_lat['p50']:.0f}ms P95={offline_lat['p95']:.0f}ms P99={offline_lat['p99']:.0f}ms")
                
            except Exception as e:
                self.logger.error("Stats reporter error", error=str(e))
    
    def start(self):
        """Start the collector."""
        try:
            self.logger.info("Starting MQTT Collector",
                           mqtt_broker=f"{MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}",
                           influxdb_url=INFLUXDB_URL,
                           bucket=INFLUXDB_BUCKET,
                           prometheus_port=PROMETHEUS_PORT if ENABLE_PROMETHEUS else None)
            
            print("\n🚀 Starting MQTT Collector with Dual-Pipeline Optimization")
            print(f"   MQTT: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT} ({'TLS' if MQTT_USE_TLS else 'TCP'})")
            print(f"   InfluxDB: {INFLUXDB_URL} / {INFLUXDB_BUCKET}")
            print(f"   Workers: {NUM_WORKER_THREADS} total (adaptive allocation)")
            print(f"   Live Queue: {LIVE_QUEUE_MAX_SIZE:,} | Offline Queue: {OFFLINE_QUEUE_MAX_SIZE:,}")
            print(f"   Live Batch: {LIVE_BATCH_SIZE} | Offline Batch: {OFFLINE_BATCH_SIZE}")
            if ENABLE_PROMETHEUS:
                print(f"   📊 Prometheus: http://localhost:{PROMETHEUS_PORT}/metrics")
            print(f"   📝 Logs: {LOG_FILE}")
            print("\n⏳ Connecting to MQTT broker...")
            
            self.mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
            self.mqtt_client.loop_forever()
            
        except KeyboardInterrupt:
            print("\n🛑 Stopping collector...")
            self._shutdown()
        except Exception as e:
            self.logger.error("Collector startup failed", error=str(e))
            print(f"❌ Collector startup failed: {e}")
            self._shutdown()
    
    def _shutdown(self):
        """Graceful shutdown with final stats."""
        self.logger.info("Initiating shutdown")
        self.shutdown_event.set()
        
        # Stop device database monitor - DISABLED
        # if hasattr(self, 'device_db_monitor'):
        #     self.device_db_monitor.stop()
        #     self.logger.info("Device database monitor stopped")
        
        # Stop MQTT
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        
        # Drain queues
        live_size = self.live_queue.qsize()
        offline_size = self.offline_queue.qsize()
        total_size = live_size + offline_size
        
        if total_size > 0:
            self.logger.info("Draining queues", 
                           live_messages=live_size,
                           offline_messages=offline_size)
            print(f"⏳ Draining {total_size:,} messages (Live: {live_size:,}, Offline: {offline_size:,})...")
            
            for _ in range(30):
                if self.live_queue.empty() and self.offline_queue.empty():
                    break
                time.sleep(1)
        
        # Wait for workers
        all_workers = self.live_workers + self.offline_workers
        for worker in all_workers:
            worker.join(timeout=5)
        
        # Flush InfluxDB
        self.logger.info("Flushing InfluxDB batches")
        try:
            # Shutdown both async writers (flushes remaining batches)
            if hasattr(self, 'live_writer'):
                self.live_writer.shutdown(timeout=30)
            if hasattr(self, 'offline_writer'):
                self.offline_writer.shutdown(timeout=30)
            self.write_api.close()
            self.influx_client.close()
        except:
            pass
        
        # Final stats
        final_stats = self.metrics.get_stats()
        alloc = self.thread_allocator.get_stats()
        
        self.logger.info("Collector shutdown complete",
                        total_messages=final_stats['counters']['total_messages'],
                        processed=final_stats['counters']['processed_messages'],
                        live_processed=final_stats['counters']['live_messages_processed'],
                        offline_processed=final_stats['counters']['offline_messages_processed'],
                        errors=final_stats['counters']['error_messages'],
                        drops=final_stats['counters']['dropped_messages'],
                        duplicates=final_stats['counters']['duplicate_messages'])
        
        print("\n✅ Collector shutdown complete")
        print(f"   Total: {final_stats['counters']['total_messages']:,}")
        print(f"   Processed: {final_stats['counters']['processed_messages']:,}")
        print(f"   Live: {final_stats['counters']['live_messages_processed']:,} | Offline: {final_stats['counters']['offline_messages_processed']:,}")
        print(f"   Errors: {final_stats['counters']['error_messages']:,}")
        print(f"   Drops: {final_stats['counters']['dropped_messages']:,}")
        print(f"   Duplicates: {final_stats['counters']['duplicate_messages']:,}")


if __name__ == "__main__":
    collector = MQTTCollectorWithMonitoring()
    collector.start()