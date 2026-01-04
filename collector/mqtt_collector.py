"""
MQTT Collector - Subscribes to MQTT messages and writes to InfluxDB
Multi-threaded implementation for high-throughput message processing
"""
import json
import time
import os
import threading
from queue import Queue, Full, Empty
from concurrent.futures import ThreadPoolExecutor
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import WriteOptions
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# MQTT Configuration
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "mqtt-collector-python")

# MQTT Security Configuration
MQTT_USE_TLS = os.getenv("MQTT_USE_TLS", "false").lower() == "true"
MQTT_TLS_INSECURE = os.getenv("MQTT_TLS_INSECURE", "false").lower() == "true"  # For self-signed certs
MQTT_CA_CERTS = os.getenv("MQTT_CA_CERTS", None)  # Path to CA certificate
MQTT_CERTFILE = os.getenv("MQTT_CERTFILE", None)  # Path to client certificate (optional)
MQTT_KEYFILE = os.getenv("MQTT_KEYFILE", None)  # Path to client private key (optional)
MQTT_USERNAME = os.getenv("MQTT_USERNAME", None)
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", None)

# InfluxDB Configuration
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "my-org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "vehicle-data")

# Threading Configuration
NUM_WORKER_THREADS = int(os.getenv("COLLECTOR_WORKER_THREADS", "4"))  # Number of worker threads
MAX_QUEUE_SIZE = int(os.getenv("COLLECTOR_MAX_QUEUE_SIZE", "10000"))  # Maximum queue size

class MQTTCollector:
    def __init__(self):
        # Initialize InfluxDB client
        self.influx_client = InfluxDBClient(
            url=INFLUXDB_URL,
            token=INFLUXDB_TOKEN,
            org=INFLUXDB_ORG
        )
        
        # Test connection
        try:
            self.influx_client.ping()
        except Exception as e:
            print(f"⚠️  InfluxDB connection failed: {e}")
            raise
        
        # Verify bucket exists
        try:
            buckets_api = self.influx_client.buckets_api()
            bucket = buckets_api.find_bucket_by_name(INFLUXDB_BUCKET)
            if not bucket:
                print(f"⚠️  WARNING: Bucket '{INFLUXDB_BUCKET}' not found!")
                raise ValueError(f"Bucket '{INFLUXDB_BUCKET}' does not exist")
        except Exception as e:
            if "WARNING" not in str(e):
                print(f"⚠️  Could not verify bucket: {e}")
                raise

        # Create batched write API
        self.write_api = self.influx_client.write_api(
            write_options = WriteOptions(
                batch_size = 250,
                flush_interval = 500,  # Flush every 0.5 second
                jitter_interval = 0,
                retry_interval = 5000,
                max_retries = 3,
                max_retry_delay = 30000  # 30 seconds
            )
        )
        # Initialize MQTT client with persistent session (best practice)
        self.mqtt_client = mqtt.Client(
            client_id=MQTT_CLIENT_ID,
            clean_session=False  # Persistent session for message retention
        )
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_disconnect = self._on_disconnect
        
        # Configure TLS if enabled
        if MQTT_USE_TLS:
            try:
                # TLS configuration
                if MQTT_CA_CERTS:
                    # Use custom CA certificate
                    self.mqtt_client.tls_set(
                        ca_certs=MQTT_CA_CERTS,
                        certfile=MQTT_CERTFILE,
                        keyfile=MQTT_KEYFILE,
                        cert_reqs=mqtt.ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else mqtt.ssl.CERT_NONE
                    )
                else:
                    # Use system CA certificates
                    self.mqtt_client.tls_set(
                        certfile=MQTT_CERTFILE,
                        keyfile=MQTT_KEYFILE,
                        cert_reqs=mqtt.ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else mqtt.ssl.CERT_NONE
                    )
                
                # Allow self-signed certificates in development
                if MQTT_TLS_INSECURE:
                    self.mqtt_client.tls_insecure_set(True)
                    print("⚠️  TLS insecure mode enabled (for self-signed certificates)")
                else:
                    self.mqtt_client.tls_insecure_set(False)
                
                print("🔒 TLS encryption enabled")
            except Exception as e:
                print(f"⚠️  TLS configuration error: {e}")
                raise
        
        # Configure authentication if provided
        if MQTT_USERNAME and MQTT_PASSWORD:
            self.mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            print("🔐 MQTT authentication enabled")
        
        # Enable automatic reconnection with exponential backoff (best practice)
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)
        
        # Threading components for multi-threaded processing
        self.message_queue = Queue(maxsize=MAX_QUEUE_SIZE)
        self.executor = ThreadPoolExecutor(max_workers=NUM_WORKER_THREADS, thread_name_prefix="collector-worker")
        self.shutdown_event = threading.Event()
        
        # Thread-safe counters
        self.message_count = 0
        self.error_count = 0
        self.processed_count = 0
        self.count_lock = threading.Lock()
        
        # Start worker threads
        self._start_workers()
        
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when MQTT client connects."""
        if rc == 0:
            protocol = "TLS" if MQTT_USE_TLS else "TCP"
            print(f"✅ Connected to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT} ({protocol})")
            
            # Subscribe to device data topics with QoS 1 (at least once delivery - best practice)
            client.subscribe("device/data/+", qos=1)
            client.subscribe("vehicle/speed/+", qos=1)  # Legacy topic
            print("📡 Subscribed to topics: device/data/+, vehicle/speed/+ (QoS 1)")
        else:
            error_messages = {
                1: "incorrect protocol version",
                2: "invalid client identifier",
                3: "server unavailable",
                4: "bad username or password",
                5: "not authorized"
            }
            error_msg = error_messages.get(rc, f"unknown error ({rc})")
            print(f"❌ Failed to connect to MQTT broker: {error_msg}")
    
    def _on_message(self, client, userdata, msg):
        """Callback when MQTT message is received - enqueues message for processing."""
        try:
            # Record when collector received the message (for latency tracking)
            collector_receive_time = time.time()
            
            # Enqueue message for processing by worker threads
            # This keeps the callback lightweight and non-blocking
            try:
                self.message_queue.put_nowait({
                    'payload': msg.payload,
                    'collector_receive_time': collector_receive_time,
                    'topic': msg.topic
                })
            except Full:
                # Queue is full - increment error count
                with self.count_lock:
                    self.error_count += 1
                print(f"⚠️  Warning: Message queue full, dropping message. Queue size: {self.message_queue.qsize()}")
                
        except Exception as e:
            print(f"❌ Error in message callback: {e}")
            with self.count_lock:
                self.error_count += 1
    
    def _process_message(self, message_data):
        """Process a single message - called by worker threads."""
        try:
            payload = json.loads(message_data['payload'].decode())
            collector_receive_time = message_data['collector_receive_time']
            device_id = payload.get('device_id')
            
            if not device_id:
                print("⚠️  Warning: Message missing device_id")
                return False
            
            # Create InfluxDB point
            point = Point("device_data") \
                .tag("device_id", device_id) \
                .tag("collector", "python")
            
            # Store collector receive time as a field (for latency calculation)
            point = point.field("collector_receive_time", float(collector_receive_time))
            
            # Store publish timestamp from payload (for end-to-end latency calculation)
            if "timestamp" in payload:
                point = point.field("publish_timestamp", float(payload["timestamp"]))
            
            # Add speed field if present
            if "speed" in payload:
                point = point.field("speed", float(payload["speed"]))
            
            # Handle both flat JSON (new) and nested JSON (legacy compatibility)
            if "telemetry" in payload:
                # Legacy nested format - extract and flatten
                telemetry = payload["telemetry"]
                if "cpu_usage" in telemetry:
                    point = point.field("cpu_usage", float(telemetry["cpu_usage"]))
                if "ram_usage" in telemetry:
                    point = point.field("ram_usage", float(telemetry["ram_usage"]))
                if "memory" in telemetry:
                    memory = telemetry["memory"]
                    point = point.field("memory_total", int(memory.get("total", 0))) \
                            .field("memory_used", int(memory.get("used", 0))) \
                            .field("memory_available", int(memory.get("available", 0))) \
                            .field("memory_percent", float(memory.get("percent", 0)))
                if "disk" in telemetry:
                    disk = telemetry["disk"]
                    point = point.field("disk_total", int(disk.get("total", 0))) \
                            .field("disk_used", int(disk.get("used", 0))) \
                            .field("disk_free", int(disk.get("free", 0))) \
                            .field("disk_percent", float(disk.get("percent", 0)))
                if "network" in telemetry:
                    network = telemetry["network"]
                    point = point.field("network_bytes_sent", int(network.get("bytes_sent", 0))) \
                            .field("network_bytes_recv", int(network.get("bytes_recv", 0)))
            else:
                # New flat format - read directly from payload root
                if "cpu_usage" in payload:
                    point = point.field("cpu_usage", float(payload["cpu_usage"]))
                if "ram_usage" in payload:
                    point = point.field("ram_usage", float(payload["ram_usage"]))
                
                # Memory fields (flat)
                if "memory_total" in payload:
                    point = point.field("memory_total", int(payload["memory_total"]))
                if "memory_used" in payload:
                    point = point.field("memory_used", int(payload["memory_used"]))
                if "memory_available" in payload:
                    point = point.field("memory_available", int(payload["memory_available"]))
                if "memory_percent" in payload:
                    point = point.field("memory_percent", float(payload["memory_percent"]))
                
                # Disk fields (flat)
                if "disk_total" in payload:
                    point = point.field("disk_total", int(payload["disk_total"]))
                if "disk_used" in payload:
                    point = point.field("disk_used", int(payload["disk_used"]))
                if "disk_free" in payload:
                    point = point.field("disk_free", int(payload["disk_free"]))
                if "disk_percent" in payload:
                    point = point.field("disk_percent", float(payload["disk_percent"]))
                
                # Network fields (flat)
                if "network_bytes_sent" in payload:
                    point = point.field("network_bytes_sent", int(payload["network_bytes_sent"]))
                if "network_bytes_recv" in payload:
                    point = point.field("network_bytes_recv", int(payload["network_bytes_recv"]))
            
            # Handle detection fields (both formats)
            if "detection" in payload:
                # Legacy nested format
                detection = payload["detection"]
                point = point.tag("detection_label", detection.get("label", "unknown")) \
                          .field("detection_confidence", float(detection.get("confidence", 0.0)))
            elif "detection_label" in payload:
                # New flat format
                point = point.tag("detection_label", str(payload["detection_label"]))
                if "detection_confidence" in payload:
                    point = point.field("detection_confidence", float(payload["detection_confidence"]))
            
            # Write to InfluxDB (batched - will flush every 0.5 second or when batch_size=250)
            self.write_api.write(bucket=INFLUXDB_BUCKET, record=point)
            
            # Update counters (thread-safe)
            with self.count_lock:
                self.processed_count += 1
                self.message_count += 1
                current_count = self.message_count
                
                # Log progress every 100 messages
                if current_count % 100 == 0:
                    queue_size = self.message_queue.qsize()
                    print(f"📊 Processed {current_count} messages | Queue: {queue_size} | Workers: {NUM_WORKER_THREADS}")
            
            return True
            
        except json.JSONDecodeError as e:
            print(f"❌ Error decoding JSON: {e}")
            with self.count_lock:
                self.error_count += 1
            return False
        except Exception as e:
            print(f"❌ Error processing message: {e}")
            with self.count_lock:
                self.error_count += 1
            import traceback
            traceback.print_exc()
            return False
    
    def _worker_thread(self):
        """Worker thread that processes messages from the queue."""
        while not self.shutdown_event.is_set():
            try:
                # Get message from queue with timeout to allow checking shutdown event
                try:
                    message_data = self.message_queue.get(timeout=1.0)
                except Empty:
                    # Timeout - check shutdown event again
                    continue
                
                # Process the message
                self._process_message(message_data)
                
                # Mark task as done
                self.message_queue.task_done()
                
            except Exception as e:
                print(f"❌ Error in worker thread: {e}")
                import traceback
                traceback.print_exc()
                # Mark task as done even on error to prevent queue blocking
                try:
                    self.message_queue.task_done()
                except:
                    pass
    
    def _start_workers(self):
        """Start worker threads for message processing."""
        for i in range(NUM_WORKER_THREADS):
            self.executor.submit(self._worker_thread)
        print(f"🔧 Started {NUM_WORKER_THREADS} worker threads for message processing")
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when MQTT client disconnects."""
        if rc != 0:
            print(f"⚠️  Unexpected MQTT disconnection (rc={rc}). Auto-reconnecting...")
        # loop_forever() will automatically reconnect
    
    def start(self):
        """Start the collector."""
        try:
            print("🚀 Starting MQTT Collector (Multi-threaded)...")
            protocol = "TLS" if MQTT_USE_TLS else "TCP"
            print(f"   MQTT Broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT} ({protocol})")
            print(f"   InfluxDB: {INFLUXDB_URL}")
            print(f"   Bucket: {INFLUXDB_BUCKET}")
            print(f"   Worker Threads: {NUM_WORKER_THREADS}")
            print(f"   Max Queue Size: {MAX_QUEUE_SIZE}")
            
            # Connect to MQTT broker with keepalive (60 seconds - best practice)
            # Keepalive ensures connection stays alive and detects dead connections
            self.mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
            
            # Start the loop (blocks forever, auto-reconnects on disconnect)
            # loop_forever() automatically handles reconnection with exponential backoff
            self.mqtt_client.loop_forever()
            
        except KeyboardInterrupt:
            print("\n🛑 Stopping collector...")
            self._shutdown()
        except Exception as e:
            print(f"❌ Error starting collector: {e}")
            import traceback
            traceback.print_exc()
            self._shutdown()
    
    def _shutdown(self):
        """Gracefully shutdown the collector and all worker threads."""
        print("🔄 Shutting down worker threads...")
        
        # Signal shutdown to worker threads
        self.shutdown_event.set()
        
        # Stop MQTT client
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        
        # Wait for queue to be processed (with timeout)
        print(f"⏳ Waiting for {self.message_queue.qsize()} queued messages to be processed...")
        try:
            # Wait up to 30 seconds for queue to empty
            import queue
            for _ in range(30):
                if self.message_queue.empty():
                    break
                time.sleep(1)
        except:
            pass
        
        # Shutdown executor and wait for threads to finish
        print("🔄 Shutting down thread pool...")
        self.executor.shutdown(wait=True, timeout=10)
        
        # Flush remaining batches and close InfluxDB connections
        print("🔄 Flushing InfluxDB batches...")
        try:
            self.write_api.close()  # Flush out remaining batches
        except:
            pass
        
        try:
            self.influx_client.close()
        except:
            pass
        
        # Print final statistics
        with self.count_lock:
            print(f"✅ Collector shutdown complete")
            print(f"   Total messages processed: {self.processed_count}")
            print(f"   Total messages received: {self.message_count}")
            print(f"   Errors encountered: {self.error_count}")
            if self.message_count > 0:
                success_rate = (self.processed_count / self.message_count) * 100
                print(f"   Success rate: {success_rate:.2f}%")

if __name__ == "__main__":
    collector = MQTTCollector()
    collector.start()