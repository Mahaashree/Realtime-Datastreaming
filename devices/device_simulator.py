"""
Device Simulator for Vehicle Speed Data
Simulates a vehicle device that publishes speed data via MQTT with offline queue support.
"""

import paho.mqtt.client as mqtt
import json
import time
import sqlite3
import os
import random
import logging
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv
import psutil

# Load environment variables
load_dotenv()

# MQTT Security Configuration
MQTT_USE_TLS = os.getenv("MQTT_USE_TLS", "false").lower() == "true"
MQTT_TLS_INSECURE = os.getenv("MQTT_TLS_INSECURE", "false").lower() == "true"  # For self-signed certs
MQTT_CA_CERTS = os.getenv("MQTT_CA_CERTS", None)  # Path to CA certificate
MQTT_CERTFILE = os.getenv("MQTT_CERTFILE", None)  # Path to client certificate (optional)
MQTT_KEYFILE = os.getenv("MQTT_KEYFILE", None)  # Path to client private key (optional)
MQTT_USERNAME = os.getenv("MQTT_USERNAME", None)
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", None)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s   - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OfflineQueue:
    """SQLite-based offline queue for storing messages when MQTT is disconnected."""
    
    def __init__(self, device_id: str, queue_dir: str = "devices/queues"):
        self.device_id = device_id
        self.queue_dir = queue_dir
        os.makedirs(queue_dir, exist_ok=True)
        self.db_path = os.path.join(queue_dir, f"{device_id}_queue.db")
        self.max_queue_size = 10000  # Limit to prevent disk overflow
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database for queue storage."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                payload TEXT NOT NULL,
                qos INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()
    
    def add_message(self, topic: str, payload: str, qos: int = 1):
        """Add a message to the queue."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check queue size
        cursor.execute('SELECT COUNT(*) FROM messages')
        count = cursor.fetchone()[0]
        
        if count >= self.max_queue_size:
            logger.warning(f"Queue full for device {self.device_id}, dropping oldest message")
            cursor.execute('DELETE FROM messages WHERE id = (SELECT MIN(id) FROM messages)')
        
        cursor.execute('''
            INSERT INTO messages (topic, payload, qos, timestamp, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (topic, payload, qos, time.time(), datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
    
    def get_all_messages(self):
        """Retrieve all queued messages in order."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT topic, payload, qos FROM messages ORDER BY id ASC')
        messages = cursor.fetchall()
        conn.close()
        return messages
    
    def clear_queue(self):
        """Clear all messages from the queue."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM messages')
        conn.commit()
        conn.close()
    
    def get_queue_size(self):
        """Get the current queue size."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM messages')
        count = cursor.fetchone()[0]
        conn.close()
        return count


class VehicleSpeedSimulator:
    """Simulates realistic vehicle speed with acceleration/deceleration patterns."""
    
    def __init__(self, min_speed: float = 0.0, max_speed: float = 120.0):
        self.min_speed = min_speed
        self.max_speed = max_speed
        self.current_speed = random.uniform(20.0, 60.0)  # Start at random speed
        self.target_speed = random.uniform(30.0, 100.0)
        self.acceleration_rate = random.uniform(0.5, 2.0)  # km/h per second
    
    def get_next_speed(self) -> float:
        """Generate next speed value with realistic acceleration/deceleration."""
        # Randomly change target speed occasionally
        if random.random() < 0.05:  # 5% chance to change target
            self.target_speed = random.uniform(self.min_speed, self.max_speed)
        
        # Gradually move towards target speed
        speed_diff = self.target_speed - self.current_speed
        if abs(speed_diff) > 0.1:
            # Accelerate or decelerate
            change = min(abs(speed_diff), self.acceleration_rate) * (1 if speed_diff > 0 else -1)
            self.current_speed += change
        else:
            # Add small random variations when at target
            self.current_speed += random.uniform(-1.0, 1.0)
        
        # Add realistic noise
        noise = random.gauss(0, 0.5)  # Small Gaussian noise
        self.current_speed += noise
        
        # Clamp to valid range
        self.current_speed = max(self.min_speed, min(self.max_speed, self.current_speed))
        
        return round(self.current_speed, 2)


class DeviceSimulator:
    """Main device simulator that publishes vehicle speed data via MQTT."""
    
    def __init__(self, device_id: str, broker_host: str = "localhost", broker_port: int = 1883,
                 publish_interval: float = 1.0):
        self.device_id = device_id
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.publish_interval = publish_interval
        self.detection_simulator = DetectionLabelSimulator()
        self.topic = f"device/data/{device_id}"
        
        # Initialize components
        self.offline_queue = OfflineQueue(device_id)
        self.speed_simulator = VehicleSpeedSimulator()
        
        # MQTT client setup with persistent session (best practice for reliability)
        self.client = mqtt.Client(
            client_id=f"device_{device_id}",
            clean_session=False  # Persistent session for message retention
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        
        # Configure TLS if enabled
        if MQTT_USE_TLS:
            try:
                # TLS configuration
                if MQTT_CA_CERTS:
                    # Use custom CA certificate
                    self.client.tls_set(
                        ca_certs=MQTT_CA_CERTS,
                        certfile=MQTT_CERTFILE,
                        keyfile=MQTT_KEYFILE,
                        cert_reqs=mqtt.ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else mqtt.ssl.CERT_NONE
                    )
                else:
                    # Use system CA certificates
                    self.client.tls_set(
                        certfile=MQTT_CERTFILE,
                        keyfile=MQTT_KEYFILE,
                        cert_reqs=mqtt.ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else mqtt.ssl.CERT_NONE
                    )
                
                # Allow self-signed certificates in development
                if MQTT_TLS_INSECURE:
                    self.client.tls_insecure_set(True)
                else:
                    self.client.tls_insecure_set(False)
            except Exception as e:
                logger.error(f"Device {self.device_id} TLS configuration error: {e}")
                raise
        
        # Configure authentication if provided
        if MQTT_USERNAME and MQTT_PASSWORD:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        
        # Enable automatic reconnection with exponential backoff (best practice)
        self.client.reconnect_delay_set(min_delay=1, max_delay=120)
        
        self.connected = False
        self.running = False
        self.last_connection_attempt = 0
        self.reconnect_interval = 1  # Start with 1 second
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when MQTT client connects."""
        if rc == 0:
            self.connected = True
            self.reconnect_interval = 1  # Reset reconnect interval on successful connection
            protocol = "TLS" if MQTT_USE_TLS else "TCP"
            logger.info(f"Device {self.device_id} connected to MQTT broker ({protocol})")
            # Flush offline queue
            self._flush_queue()
        else:
            error_messages = {
                1: "incorrect protocol version",
                2: "invalid client identifier",
                3: "server unavailable",
                4: "bad username or password",
                5: "not authorized"
            }
            error_msg = error_messages.get(rc, f"unknown error ({rc})")
            logger.error(f"Device {self.device_id} failed to connect: {error_msg}")
            self.connected = False
            # Exponential backoff for reconnection
            self.reconnect_interval = min(self.reconnect_interval * 2, 120)
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when MQTT client disconnects."""
        self.connected = False
        if rc != 0:
            logger.warning(f"Device {self.device_id} unexpectedly disconnected (rc={rc}). Will auto-reconnect...")
            # loop_start() with reconnect_delay_set will automatically reconnect
        else:
            logger.info(f"Device {self.device_id} disconnected")
    
    def _on_publish(self, client, userdata, mid):
        """Callback when message is published."""
        pass  # Can be used for acknowledgment tracking if needed
    
    def _flush_queue(self):
        """Publish all queued messages when connection is restored."""
        messages = self.offline_queue.get_all_messages()
        if messages:
            logger.info(f"Device {self.device_id} flushing {len(messages)} queued messages")
            flushed_count = 0
            failed_count = 0
            
            # Publish in batches to avoid overwhelming broker
            batch_size = 100
            for i, (topic, payload, qos) in enumerate(messages):
                # Check connection is still alive
                if not self.connected:
                    logger.warning(f"Device {self.device_id} connection lost during queue flush")
                    break
                
                try:
                    # Publish with QoS 1 (at least once delivery - best practice)
                    result = self.client.publish(topic, payload, qos=qos)
                    if result.rc == mqtt.MQTT_ERR_SUCCESS:
                        flushed_count += 1
                        # Small delay every 10 messages to avoid overwhelming broker
                        if (i + 1) % 10 == 0:
                            time.sleep(0.01)
                    else:
                        logger.warning(f"Failed to publish queued message: {result.rc}")
                        failed_count += 1
                        # Don't break - continue trying to flush more messages
                except Exception as e:
                    logger.error(f"Error publishing queued message: {e}")
                    failed_count += 1
                    # Continue with next message
            
            # Clear successfully flushed messages
            if flushed_count > 0 and self.connected:
                # Remove only the messages that were successfully published
                # For simplicity, clear all if most were flushed
                if failed_count == 0 or (flushed_count / len(messages)) > 0.9:
                    self.offline_queue.clear_queue()
                    logger.info(f"Device {self.device_id} queue cleared ({flushed_count} messages flushed)")
                else:
                    logger.warning(f"Device {self.device_id} partially flushed queue: {flushed_count} flushed, {failed_count} failed")
    
    def _publish_device_data(self, speed: float):
        """Publish complete device data including telemetry and detections (flat JSON structure)."""
        
        # Collect telemetry
        telemetry = DeviceTelemetry()
        memory = telemetry.get_memory_info()
        disk = telemetry.get_disk_usage()
        network = psutil.net_io_counters()
        
        # Get detection label
        detection = self.detection_simulator.get_next_label()
        
        # Build flat payload (no nested objects for better Telegraf performance)
        payload = {
            "device_id": self.device_id,
            "timestamp": time.time(),
            "datetime": datetime.now().isoformat(),
            
            # Speed data
            "speed": speed,
            
            # Device telemetry (flattened)
            "cpu_usage": telemetry.get_cpu_usage(),
            "ram_usage": telemetry.get_ram_usage(),
            
            # Memory (flattened)
            "memory_total": memory["total"],
            "memory_used": memory["used"],
            "memory_available": memory["available"],
            "memory_percent": memory["percent"],
            
            # Disk (flattened)
            "disk_total": disk["total"],
            "disk_used": disk["used"],
            "disk_free": disk["free"],
            "disk_percent": disk["percent"],
            
            # Network (flattened)
            "network_bytes_sent": network.bytes_sent,
            "network_bytes_recv": network.bytes_recv,
            
            # Detection labels (flattened)
            "detection_label": detection["label"],
            "detection_confidence": detection["confidence"],
            "detection_timestamp": detection["timestamp"]
        }
        
        message = json.dumps(payload)
        
        # Check if connected (flag + verify by attempting publish)
        if self.connected:
            try:
                # Publish with QoS 1 (at least once delivery - best practice)
                result = self.client.publish(self.topic, message, qos=1)
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    logger.debug(f"Device {self.device_id} published data")
                elif result.rc == mqtt.MQTT_ERR_NO_CONN:
                    # Not connected - update flag and queue
                    logger.warning(f"Device {self.device_id} not connected, queueing message")
                    self.connected = False
                    self.offline_queue.add_message(self.topic, message, qos=1)
                else:
                    logger.warning(f"Device {self.device_id} publish failed (rc={result.rc}), queueing message")
                    self.offline_queue.add_message(self.topic, message, qos=1)
            except Exception as e:
                logger.error(f"Device {self.device_id} error publishing: {e}")
                self.offline_queue.add_message(self.topic, message, qos=1)
                self.connected = False
        else:
            # Not connected - queue message
            self.offline_queue.add_message(self.topic, message, qos=1)
            logger.debug(f"Device {self.device_id} queued message (disconnected)")
    
    def connect(self):
        """Connect to MQTT broker with robust error handling."""
        try:
            # Don't reconnect too frequently
            current_time = time.time()
            if current_time - self.last_connection_attempt < self.reconnect_interval:
                return  # Too soon to retry
            
            self.last_connection_attempt = current_time
            protocol = "TLS" if MQTT_USE_TLS else "TCP"
            logger.info(f"Device {self.device_id} connecting to {self.broker_host}:{self.broker_port} ({protocol})")
            
            # Connect with keepalive (60 seconds - best practice)
            # Broker will disconnect if no keepalive for 1.5x keepalive time (90 seconds)
            self.client.connect(self.broker_host, self.broker_port, keepalive=60)
            
            # Start network loop (handles keepalive and auto-reconnect)
            self.client.loop_start()
            
            # Wait a moment for connection to establish
            time.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Device {self.device_id} connection error: {e}")
            self.connected = False
            # Exponential backoff
            self.reconnect_interval = min(self.reconnect_interval * 2, 120)
    
    def disconnect(self):
        """Disconnect from MQTT broker."""
        self.running = False
        if self.connected:
            self.client.loop_stop()
            self.client.disconnect()
        
        logger.info(f"Device {self.device_id} stopped")
    
    def run(self):
        """Main loop to generate and publish speed data with robust reconnection."""
        self.running = True
        logger.info(f"Device {self.device_id} started, publishing every {self.publish_interval}s")
        
        while self.running:
            try:
                # Ensure connection is maintained - reconnect if needed
                if not self.connected:
                    # Try to reconnect if not connected
                    self.connect()
                    # Give it a moment to connect
                    time.sleep(1)
                
                speed = self.speed_simulator.get_next_speed()
                self._publish_device_data(speed)
                
                # Log queue size periodically (only if queue is growing)
                queue_size = self.offline_queue.get_queue_size()
                if queue_size > 0:
                    if queue_size % 1000 == 0 or queue_size > 5000:  # Log every 1000 or if > 5000
                        logger.info(f"Device {self.device_id} queue size: {queue_size}")
                else:
                    logger.info(f"Device {self.device_id} queue size: {queue_size}")
                time.sleep(self.publish_interval)
            except KeyboardInterrupt:
                logger.info(f"Device {self.device_id} interrupted by user")
                break
            except Exception as e:
                logger.error(f"Device {self.device_id} error in main loop: {e}")
                time.sleep(self.publish_interval)
        
        self.disconnect()

    

class DeviceTelemetry:

    @staticmethod
    def get_cpu_usage():
        #return psutil.cpu_percent(interval=1)
        return psutil.cpu_percent(interval = None) #reduces blockage

    @staticmethod
    def get_ram_usage():
        memory = psutil.virtual_memory()
        return memory.percent

    @staticmethod
    def get_memory_info():
        memory = psutil.virtual_memory()
        return {
            "total": memory.total,
            "available": memory.available,
            "used": memory.used,
            "percent": memory.percent,
        }

    @staticmethod
    def get_disk_usage():
        disk = psutil.disk_usage('/')
        return {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        }

class DetectionLabelSimulator:
    def __init__(self):
        self.labels = [
            "eyes_closed",
            "distracted",
            "smoking",
            "phone_usage",
            "yawning",
            "drowsy",
            "normal"
        ]

        self.current_label = "normal"
        self.label_duration = 10

    def get_next_label(self):
        #90% chance to stay normal
        if random.random() < 0.9:
            if self.current_label != "normal":
                self.label_duration += 1
                # Return to normal after 3-5 seconds
                if self.label_duration > random.randint(3, 5):
                    self.current_label = "normal"
                    self.label_duration = 0
        else:
            # Randomly detect an issue
            self.current_label = random.choice([
                "eyes_closed",
                "distracted", 
                "smoking",
                "phone_usage"
            ])
            self.label_duration = 0
        
        return {
            "label": self.current_label,
            "confidence": random.uniform(0.75, 0.99) if self.current_label != "normal" else 1.0,
            "timestamp": time.time()
        }


def main():
    """Main entry point for device simulator."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python device_simulator.py <device_id> [broker_host] [broker_port]")
        sys.exit(1)
    
    device_id = sys.argv[1]
    broker_host = sys.argv[2] if len(sys.argv) > 2 else os.getenv("MQTT_BROKER_HOST", "localhost")
    broker_port = int(sys.argv[3]) if len(sys.argv) > 3 else int(os.getenv("MQTT_BROKER_PORT", "1883"))
    
    simulator = DeviceSimulator(device_id, broker_host, broker_port)
    simulator.connect()
    
    # Wait a bit for connection
    time.sleep(2)
    
    try:
        simulator.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        simulator.disconnect()


if __name__ == "__main__":
    main()

