"""
MQTT Collector with Comprehensive Monitoring - PRODUCTION READY
Optimized for 100-200 device POC with proper error handling
"""
import json
import time
import os
import threading
import ssl
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

# Monitoring configuration
ENABLE_PROMETHEUS = os.getenv("ENABLE_PROMETHEUS", "true").lower() == "true"
PROMETHEUS_PORT = int(os.getenv("PROMETHEUS_PORT", "9090"))
LOG_FILE = os.getenv("LOG_FILE", "logs/collector.log")
STATS_SERVER_PORT = int(os.getenv("STATS_SERVER_PORT", "9091"))


class MQTTCollectorWithMonitoring:
    def __init__(self):
        print("=" * 70)
        print("🚀 Initializing MQTT Collector with Monitoring")
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
                        queue_size=MAX_QUEUE_SIZE)
        
        # Initialize InfluxDB
        self._init_influxdb()
        
        # MQTT client setup
        self._init_mqtt()
        
        # Queue and threading
        self.message_queue = Queue(maxsize=MAX_QUEUE_SIZE)
        self.shutdown_event = threading.Event()
        self.workers = []
        
        # Start workers
        self._start_workers()
        
        # Start stats reporter
        self.stats_thread = threading.Thread(target=self._stats_reporter, daemon=True)
        self.stats_thread.start()
        
        # Start stats HTTP server for dashboard
        self._start_stats_server()
        
        print("✅ Collector initialized successfully")
        print("=" * 70)
    
    def _init_influxdb(self):
        """Initialize InfluxDB connection."""
        try:
            self.influx_client = InfluxDBClient(
                url=INFLUXDB_URL,
                token=INFLUXDB_TOKEN,
                org=INFLUXDB_ORG,
                timeout=30000  # 30 second timeout
            )
            
            # Test connection
            self.influx_client.ping()
            self.logger.info("InfluxDB connection successful")
            
            # Verify bucket exists
            buckets_api = self.influx_client.buckets_api()
            bucket = buckets_api.find_bucket_by_name(INFLUXDB_BUCKET)
            if not bucket:
                raise ValueError(f"Bucket '{INFLUXDB_BUCKET}' does not exist")
            self.logger.info("Bucket verified", bucket=INFLUXDB_BUCKET)
            
        except Exception as e:
            self.logger.error("InfluxDB initialization failed", error=str(e))
            raise

        # Configure write API with optimized settings for POC
        self.write_api = self.influx_client.write_api(
            write_options=WriteOptions(
                batch_size=500,        # Good for 100-200 devices
                flush_interval=1000,    # 1 seconds
                jitter_interval=100,
                retry_interval=5000,
                max_retries=3,
                max_retry_delay=30000
            )
        )
        self.logger.info("InfluxDB write API initialized", 
                        batch_size=1000, 
                        flush_interval_ms=2000)
    
    def _init_mqtt(self):
        """Initialize MQTT client."""
        self.mqtt_client = mqtt.Client(
            client_id=MQTT_CLIENT_ID,
            clean_session=False  # Persistent session
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
        
        # Configure reconnection
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)
    
    def _handle_critical_alerts(self, alert):
        """Handle critical alerts - log and take action."""
        self.logger.critical("Critical alert triggered",
                           severity=alert.severity,
                           metric=alert.metric_name,
                           value=alert.value,
                           threshold=alert.threshold,
                           message=alert.message)
        
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
            self.logger.info("Subscribed to MQTT topics", 
                           topics=["device/data/+"],
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
        """MQTT message callback - with monitoring."""
        try:
            # Record message received
            self.metrics.record_message_received()
            
            collector_receive_time = time.time()
            
            try:
                self.message_queue.put_nowait({
                    'payload': msg.payload,
                    'collector_receive_time': collector_receive_time,
                    'topic': msg.topic
                })
                
                # Update queue depth metric
                self.metrics.record_queue_depth(self.message_queue.qsize())
                
            except Full:
                # Queue full - record drop
                self.metrics.record_drop()
                
                # Log only every 100 drops to avoid spam
                if self.metrics.dropped_messages % 100 == 0:
                    self.logger.warning("Message queue full - dropping messages",
                                      queue_size=self.message_queue.qsize(),
                                      dropped_total=self.metrics.dropped_messages)
                
        except Exception as e:
            self.metrics.record_error()
            self.logger.error("Error in message callback", error=str(e))
    
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
        """Worker thread with batch processing and monitoring."""
        thread_id = threading.get_ident()
        self.logger.info("Worker thread started", thread_id=thread_id)
        
        try:
            while not self.shutdown_event.is_set():
                batch = []
                
                # Try to get first message with timeout
                try:
                    msg = self.message_queue.get(timeout=0.5)
                    batch.append(msg)
                    
                    # Drain additional messages for batching (up to BATCH_DRAIN_SIZE)
                    for _ in range(BATCH_DRAIN_SIZE - 1):
                        try:
                            msg = self.message_queue.get_nowait()
                            batch.append(msg)
                        except Empty:
                            break
                    
                except Empty:
                    continue
                
                # Process batch
                points = []
                publish_timestamps = []  # Store publish timestamps for full latency calculation
                
                for message_data in batch:
                    result = self._process_message(message_data)
                    if result:
                        point, mqtt_latency, publish_ts = result
                        points.append(point)
                        if publish_ts:  # Only track messages with valid publish timestamp
                            publish_timestamps.append(publish_ts)
                    self.message_queue.task_done()
                
                # Write batch to InfluxDB
                if points:
                    try:
                        # Measure time before and after write to calculate full latency
                        write_start_time = time.time()
                        self.write_api.write(bucket=INFLUXDB_BUCKET, record=points)
                        write_end_time = time.time()
                        
                        # Calculate full latency: time to InfluxDB queue - publish timestamp
                        # This includes: MQTT network + queue wait + processing + InfluxDB queue time
                        full_latencies = []
                        for publish_ts in publish_timestamps:
                            if publish_ts:
                                full_latency = write_end_time - publish_ts
                                # Only record valid latencies (positive and reasonable)
                                if 0 < full_latency < 60:  # Less than 60 seconds
                                    full_latencies.append(full_latency)
                        
                        # Record full latencies (end-to-end from device publish to InfluxDB write)
                        for full_latency in full_latencies:
                            self.metrics.record_message_processed(full_latency)
                        
                        # For messages without valid publish timestamp, record without latency
                        for _ in range(len(points) - len(full_latencies)):
                            self.metrics.record_message_processed()
                            
                    except Exception as e:
                        # Write failed - record errors
                        for _ in points:
                            self.metrics.record_error()
                        
                        # Log only every 100 errors
                        if self.metrics.error_messages % 100 == 0:
                            self.logger.error("InfluxDB write error",
                                            error=str(e),
                                            batch_size=len(points))
                
                # Update queue depth after processing
                self.metrics.record_queue_depth(self.message_queue.qsize())
        
        except Exception as e:
            self.logger.error("Worker thread crashed", 
                            thread_id=thread_id,
                            error=str(e))
        finally:
            self.logger.info("Worker thread stopped", thread_id=thread_id)
    
    def _start_workers(self):
        """Start worker threads."""
        for i in range(NUM_WORKER_THREADS):
            worker = threading.Thread(target=self._worker_thread, daemon=True)
            worker.start()
            self.workers.append(worker)
        self.logger.info("Worker threads started", count=NUM_WORKER_THREADS)
    
    def _start_stats_server(self):
        """Start a simple HTTP server to expose MetricsCollector stats."""
        stats_app = Flask(__name__)
        
        @stats_app.route('/stats')
        def get_stats():
            """Get current metrics stats from MetricsCollector."""
            try:
                stats = self.metrics.get_stats()
                return jsonify(stats)
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        
        def run_server():
            stats_app.run(host='0.0.0.0', port=STATS_SERVER_PORT, debug=False, use_reloader=False)
        
        stats_thread = Thread(target=run_server, daemon=True)
        stats_thread.start()
        self.logger.info(f"Stats server started on port {STATS_SERVER_PORT}")
        print(f"   📊 Stats API: http://localhost:{STATS_SERVER_PORT}/stats")
    
    def _stats_reporter(self):
        """Periodic stats reporting."""
        while not self.shutdown_event.is_set():
            time.sleep(30)  # Report every 30 seconds
            
            try:
                stats = self.metrics.get_stats()
                
                # Log comprehensive stats
                self.logger.info("Periodic statistics report",
                               throughput=stats['rates']['throughput_msg_per_sec'],
                               queue_depth=stats['queue']['current_depth'],
                               error_rate=stats['rates']['error_rate_percent'],
                               drop_rate=stats['rates']['drop_rate_percent'],
                               total_messages=stats['counters']['total_messages'],
                               latency_p95=stats['latency']['p95'] if stats['latency'] else None,
                               latency_p99=stats['latency']['p99'] if stats['latency'] else None)
                
                # Console summary
                lat = stats['latency']
                print(f"\n📊 Stats ({datetime.now().strftime('%H:%M:%S')})")
                print(f"   Total: {stats['counters']['total_messages']:,} | Processed: {stats['counters']['processed_messages']:,}")
                print(f"   Rate: {stats['rates']['throughput_msg_per_sec']:.1f} msg/s")
                print(f"   Queue: {stats['queue']['current_depth']:,} (avg: {stats['queue']['avg_depth']:.0f}, max: {stats['queue']['max_depth']:,})")
                print(f"   Errors: {stats['rates']['error_rate_percent']:.2f}% | Drops: {stats['rates']['drop_rate_percent']:.2f}%")
                if lat:
                    print(f"   Latency: P50={lat['p50']:.0f}ms P95={lat['p95']:.0f}ms P99={lat['p99']:.0f}ms")
                
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
            
            print("\n🚀 Starting MQTT Collector with Monitoring")
            print(f"   MQTT: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT} ({'TLS' if MQTT_USE_TLS else 'TCP'})")
            print(f"   InfluxDB: {INFLUXDB_URL} / {INFLUXDB_BUCKET}")
            print(f"   Workers: {NUM_WORKER_THREADS} | Queue: {MAX_QUEUE_SIZE:,}")
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
        
        # Stop MQTT
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        
        # Drain queue
        queue_size = self.message_queue.qsize()
        if queue_size > 0:
            self.logger.info("Draining queue", messages=queue_size)
            print(f"⏳ Draining {queue_size:,} messages...")
            
            for _ in range(30):
                if self.message_queue.empty():
                    break
                time.sleep(1)
        
        # Wait for workers
        for worker in self.workers:
            worker.join(timeout=5)
        
        # Flush InfluxDB
        self.logger.info("Flushing InfluxDB batches")
        try:
            self.write_api.close()
            self.influx_client.close()
        except:
            pass
        
        # Final stats
        final_stats = self.metrics.get_stats()
        self.logger.info("Collector shutdown complete",
                        total_messages=final_stats['counters']['total_messages'],
                        processed=final_stats['counters']['processed_messages'],
                        errors=final_stats['counters']['error_messages'],
                        drops=final_stats['counters']['dropped_messages'])
        
        print("\n✅ Collector shutdown complete")
        print(f"   Total: {final_stats['counters']['total_messages']:,}")
        print(f"   Processed: {final_stats['counters']['processed_messages']:,}")
        print(f"   Errors: {final_stats['counters']['error_messages']:,}")
        print(f"   Drops: {final_stats['counters']['dropped_messages']:,}")


if __name__ == "__main__":
    collector = MQTTCollectorWithMonitoring()
    collector.start()