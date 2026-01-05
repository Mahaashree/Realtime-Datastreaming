"""
Vehicle Device Simulator - PRODUCTION READY
Simulates vehicle telemetry with drowsiness detection
Includes offline queue with SQLite persistence
"""
import json
import time
import random
import os
import sqlite3
import ssl
from datetime import datetime
from threading import Thread, Event
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# Configuration
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

MQTT_USE_TLS = os.getenv("MQTT_USE_TLS", "false").lower() == "true"
MQTT_TLS_INSECURE = os.getenv("MQTT_TLS_INSECURE", "false").lower() == "true"
MQTT_CA_CERTS = os.getenv("MQTT_CA_CERTS", None)
MQTT_CERTFILE = os.getenv("MQTT_CERTFILE", None)
MQTT_KEYFILE = os.getenv("MQTT_KEYFILE", None)
MQTT_USERNAME = os.getenv("MQTT_USERNAME", None)
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", None)

PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL", "1.0"))  # seconds
OFFLINE_QUEUE_DIR = os.getenv("OFFLINE_QUEUE_DIR", "offline_queues")

# Drowsiness detection labels with typical confidence distribution
DETECTION_LABELS = [
    ("alert", 0.85),       # Most common - driver is alert
    ("drowsy", 0.10),      # Occasionally drowsy
    ("yawning", 0.03),     # Rare
    ("eyes_closed", 0.01), # Very rare - critical
    ("head_nodding", 0.005),
    ("distracted", 0.005)
]


class OfflineQueue:
    """SQLite-based persistent queue for offline message storage."""
    
    def __init__(self, device_id: str, queue_dir: str = OFFLINE_QUEUE_DIR):
        os.makedirs(queue_dir, exist_ok=True)
        self.db_path = os.path.join(queue_dir, f"{device_id}.db")
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                payload TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        ''')
        conn.commit()
        conn.close()
    
    def enqueue(self, topic: str, payload: dict):
        """Add message to offline queue."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO messages (topic, payload, timestamp) VALUES (?, ?, ?)',
                (topic, json.dumps(payload), time.time())
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"⚠️  [{payload.get('device_id')}] Offline queue error: {e}")
            return False
    
    def dequeue_batch(self, batch_size: int = 50):
        """Retrieve and remove a batch of messages."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get oldest messages
            cursor.execute(
                'SELECT id, topic, payload FROM messages ORDER BY timestamp ASC LIMIT ?',
                (batch_size,)
            )
            messages = cursor.fetchall()
            
            if messages:
                # Delete retrieved messages
                ids = [msg[0] for msg in messages]
                placeholders = ','.join('?' * len(ids))
                cursor.execute(f'DELETE FROM messages WHERE id IN ({placeholders})', ids)
                conn.commit()
            
            conn.close()
            
            # Return as list of (topic, payload_dict)
            return [(msg[1], json.loads(msg[2])) for msg in messages]
            
        except Exception as e:
            print(f"⚠️  Offline queue dequeue error: {e}")
            return []
    
    def get_size(self):
        """Get current queue size."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM messages')
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except:
            return 0


class VehicleDevice:
    """Simulates a vehicle device with telemetry and drowsiness detection."""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.is_connected = False
        self.shutdown_event = Event()
        
        # Offline queue
        self.offline_queue = OfflineQueue(device_id)
        
        # Initialize MQTT client
        client_id = f"{device_id}-{int(time.time())}"
        self.client = mqtt.Client(
            client_id=client_id,
            clean_session=False  # Persistent session
        )
        
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        
        # Configure TLS
        if MQTT_USE_TLS:
            try:
                if MQTT_CA_CERTS:
                    self.client.tls_set(
                        ca_certs=MQTT_CA_CERTS,
                        certfile=MQTT_CERTFILE,
                        keyfile=MQTT_KEYFILE,
                        cert_reqs=ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else ssl.CERT_NONE
                    )
                else:
                    self.client.tls_set(
                        certfile=MQTT_CERTFILE,
                        keyfile=MQTT_KEYFILE,
                        cert_reqs=ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else ssl.CERT_NONE
                    )
                
                if MQTT_TLS_INSECURE:
                    self.client.tls_insecure_set(True)
            except Exception as e:
                print(f"⚠️  [{device_id}] TLS configuration failed: {e}")
        
        # Configure authentication
        if MQTT_USERNAME and MQTT_PASSWORD:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        
        # Configure reconnection
        self.client.reconnect_delay_set(min_delay=1, max_delay=120)
        
        # Vehicle state
        self.speed = 0.0
        self.cpu_usage = random.uniform(20, 40)
        self.ram_usage = random.uniform(30, 50)
        
        # Statistics
        self.messages_published = 0
        self.messages_queued = 0
        self.publish_errors = 0
    
    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            self.is_connected = True
            queue_size = self.offline_queue.get_size()
            
            if queue_size > 0:
                print(f"✅ [{self.device_id}] Connected - flushing {queue_size} offline messages")
                self._flush_offline_queue()
            else:
                print(f"✅ [{self.device_id}] Connected")
        else:
            self.is_connected = False
            error_messages = {
                1: "incorrect protocol version",
                2: "invalid client identifier",
                3: "server unavailable",
                4: "bad username or password",
                5: "not authorized"
            }
            error_msg = error_messages.get(rc, f"unknown error ({rc})")
            print(f"❌ [{self.device_id}] Connection failed: {error_msg}")
    
    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnect callback."""
        self.is_connected = False
        if rc != 0:
            print(f"⚠️  [{self.device_id}] Disconnected (rc={rc}), will auto-reconnect")
    
    def _on_publish(self, client, userdata, mid):
        """MQTT publish callback."""
        pass  # Successfully published
    
    def _flush_offline_queue(self):
        """Flush offline queue when reconnected."""
        try:
            while True:
                batch = self.offline_queue.dequeue_batch(batch_size=50)
                if not batch:
                    break
                
                for topic, payload in batch:
                    try:
                        self.client.publish(topic, json.dumps(payload), qos=1)
                        self.messages_published += 1
                    except Exception as e:
                        # Re-queue if publish fails
                        self.offline_queue.enqueue(topic, payload)
                        print(f"⚠️  [{self.device_id}] Re-queue failed publish")
                        break
                
                time.sleep(0.1)  # Don't overwhelm broker
            
            remaining = self.offline_queue.get_size()
            if remaining == 0:
                print(f"✅ [{self.device_id}] Offline queue flushed")
            else:
                print(f"⚠️  [{self.device_id}] {remaining} messages remain in offline queue")
                
        except Exception as e:
            print(f"⚠️  [{self.device_id}] Flush error: {e}")
    
    def _generate_detection_label(self):
        """Generate drowsiness detection label with realistic distribution."""
        rand = random.random()
        cumulative = 0.0
        
        for label, prob in DETECTION_LABELS:
            cumulative += prob
            if rand < cumulative:
                # Confidence varies based on label
                if label == "alert":
                    confidence = random.uniform(0.85, 0.99)
                elif label == "drowsy":
                    confidence = random.uniform(0.60, 0.85)
                else:
                    confidence = random.uniform(0.70, 0.95)
                
                return label, confidence
        
        # Fallback (should rarely happen)
        return "alert", 0.90
    
    def _generate_telemetry(self):
        """Generate realistic vehicle telemetry data."""
        # Simulate speed variations (0-120 km/h)
        if random.random() < 0.05:  # 5% chance of speed change
            self.speed = max(0, min(120, self.speed + random.uniform(-20, 20)))
        
        # Simulate CPU/RAM variations
        self.cpu_usage += random.uniform(-5, 5)
        self.cpu_usage = max(10, min(95, self.cpu_usage))
        
        self.ram_usage += random.uniform(-3, 3)
        self.ram_usage = max(20, min(90, self.ram_usage))
        
        # Generate detection
        detection_label, detection_confidence = self._generate_detection_label()
        
        # Build payload
        payload = {
            "device_id": self.device_id,
            "timestamp": time.time(),
            "speed": round(self.speed, 2),
            "cpu_usage": round(self.cpu_usage, 2),
            "ram_usage": round(self.ram_usage, 2),
            "memory_total": 8192,  # MB
            "memory_used": round(8192 * self.ram_usage / 100, 2),
            "memory_available": round(8192 * (100 - self.ram_usage) / 100, 2),
            "memory_percent": round(self.ram_usage, 2),
            "disk_total": 256000,  # MB
            "disk_used": round(256000 * random.uniform(0.4, 0.7), 2),
            "disk_free": round(256000 * random.uniform(0.3, 0.6), 2),
            "disk_percent": round(random.uniform(40, 70), 2),
            "network_bytes_sent": random.randint(1000000, 5000000),
            "network_bytes_recv": random.randint(2000000, 10000000),
            "detection_label": detection_label,
            "detection_confidence": round(detection_confidence, 3)
        }
        
        return payload
    
    def _publish_loop(self):
        """Main publishing loop."""
        while not self.shutdown_event.is_set():
            try:
                # Generate telemetry
                payload = self._generate_telemetry()
                
                # Publish to MQTT
                if self.is_connected:
                    try:
                        topic_data = f"device/data/{self.device_id}"
                        
                        result_data = self.client.publish(topic_data, json.dumps(payload), qos=1)
                        
                        if result_data.rc == mqtt.MQTT_ERR_SUCCESS:
                            self.messages_published += 1
                        else:
                            # Failed to publish - queue offline
                            self.offline_queue.enqueue(topic_data, payload)
                            self.messages_queued += 1
                            
                    except Exception as e:
                        # Network error - queue offline
                        self.offline_queue.enqueue(topic_data, payload)
                        self.messages_queued += 1
                        self.publish_errors += 1
                        
                        if self.publish_errors % 10 == 0:
                            print(f"⚠️  [{self.device_id}] Publish errors: {self.publish_errors}, queued: {self.messages_queued}")
                else:
                    # Not connected - queue offline
                    topic_data = f"device/data/{self.device_id}"
                    
                    self.offline_queue.enqueue(topic_data, payload)
                    self.messages_queued += 1
                
                # Sleep until next publish
                time.sleep(PUBLISH_INTERVAL)
                
            except Exception as e:
                print(f"⚠️  [{self.device_id}] Publish loop error: {e}")
                time.sleep(1)
    
    def start(self):
        """Start the device simulator."""
        try:
            # Connect to MQTT broker
            print(f"🔌 [{self.device_id}] Connecting to {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}...")
            self.client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
            
            # Start MQTT loop in background
            self.client.loop_start()
            
            # Start publishing loop
            self.publish_thread = Thread(target=self._publish_loop, daemon=True)
            self.publish_thread.start()
            
        except Exception as e:
            print(f"❌ [{self.device_id}] Start failed: {e}")
    
    def stop(self):
        """Stop the device simulator."""
        self.shutdown_event.set()
        
        # Stop MQTT
        self.client.loop_stop()
        self.client.disconnect()
        
        # Wait for publish thread
        if hasattr(self, 'publish_thread'):
            self.publish_thread.join(timeout=2)
        
        # Print stats
        queue_size = self.offline_queue.get_size()
        print(f"🛑 [{self.device_id}] Stopped - Published: {self.messages_published}, Queued: {queue_size}")
    
    def get_stats(self):
        """Get device statistics."""
        return {
            "device_id": self.device_id,
            "is_connected": self.is_connected,
            "messages_published": self.messages_published,
            "messages_queued": self.messages_queued,
            "offline_queue_size": self.offline_queue.get_size(),
            "publish_errors": self.publish_errors
        }


def start_devices(num_devices: int = 100):
    """Start multiple device simulators."""
    devices = []
    
    print("=" * 70)
    print(f"🚀 Starting {num_devices} Vehicle Device Simulators")
    print("=" * 70)
    print(f"   MQTT Broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
    print(f"   Protocol: {'TLS' if MQTT_USE_TLS else 'TCP'}")
    print(f"   Publish Interval: {PUBLISH_INTERVAL}s")
    print(f"   Offline Queue: {OFFLINE_QUEUE_DIR}")
    print("=" * 70)
    
    # Start devices with staggered initialization
    for i in range(1, num_devices + 1):
        device_id = f"vehicle_{i:03d}"
        
        try:
            device = VehicleDevice(device_id)
            device.start()
            devices.append(device)
            
            print(f"  [{i:3d}/{num_devices}] Starting {device_id}... ✓ Started")
            
            # Stagger connections to avoid overwhelming broker
            if i % 10 == 0:
                time.sleep(0.5)
            else:
                time.sleep(0.05)
                
        except Exception as e:
            print(f"  [{i:3d}/{num_devices}] Starting {device_id}... ✗ Failed: {e}")
    
    print("=" * 70)
    print("All devices started. Monitoring status...")
    print("=" * 70)
    
    # Monitor devices
    try:
        while True:
            time.sleep(10)
            
            # Count connected devices
            connected = sum(1 for d in devices if d.is_connected)
            total_published = sum(d.messages_published for d in devices)
            total_queued = sum(d.offline_queue.get_size() for d in devices)
            
            print(f"📊 Status: {connected}/{len(devices)} devices connected | "
                  f"Published: {total_published:,} | Queued: {total_queued:,}")
            
    except KeyboardInterrupt:
        print("\n🛑 Stopping all devices...")
        for device in devices:
            device.stop()
        print("✅ All devices stopped")


if __name__ == "__main__":
    import sys
    
    # Get number of devices from command line or use default
    num_devices = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    
    # Ensure num_devices is reasonable
    if num_devices < 1 or num_devices > 1000:
        print("❌ Number of devices must be between 1 and 1000")
        sys.exit(1)
    
    start_devices(num_devices)