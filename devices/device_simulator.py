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
        
        # MQTT client setup
        self.client = mqtt.Client(client_id=f"device_{device_id}", clean_session=False)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        
        self.connected = False
        self.running = False
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when MQTT client connects."""
        if rc == 0:
            self.connected = True
            logger.info(f"Device {self.device_id} connected to MQTT broker")
            # Flush offline queue
            self._flush_queue()
        else:
            logger.error(f"Device {self.device_id} failed to connect, return code {rc}")
            self.connected = False
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when MQTT client disconnects."""
        self.connected = False
        if rc != 0:
            logger.warning(f"Device {self.device_id} unexpectedly disconnected")
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
            for topic, payload, qos in messages:
                try:
                    result = self.client.publish(topic, payload, qos=qos)
                    if result.rc == mqtt.MQTT_ERR_SUCCESS:
                        time.sleep(0.01)  # Small delay to avoid overwhelming broker
                    else:
                        logger.error(f"Failed to publish queued message: {result.rc}")
                        break  # Stop if publish fails
                except Exception as e:
                    logger.error(f"Error publishing queued message: {e}")
                    break
            
            # Clear queue only if all messages were sent
            if self.connected:
                self.offline_queue.clear_queue()
                logger.info(f"Device {self.device_id} queue cleared")
    
    def _publish_device_data(self, speed: float):
        """Publish complete device data including telemetry and detections."""
        
        # Collect telemetry
        telemetry = DeviceTelemetry()
        
        # Get detection label
        detection = self.detection_simulator.get_next_label()
        
        # Build complete payload
        payload = {
            "device_id": self.device_id,
            "timestamp": time.time(),
            "datetime": datetime.now().isoformat(),
            
            # Speed data
            "speed": speed,
            
            # Device telemetry
            "telemetry": {
                "cpu_usage": telemetry.get_cpu_usage(),
                "ram_usage": telemetry.get_ram_usage(),
                "memory": telemetry.get_memory_info(),
                "disk": telemetry.get_disk_usage(),
            },
            
            # Detection labels
            "detection": detection
        }
        
        message = json.dumps(payload)
        
        if self.connected:
            try:
                result = self.client.publish(self.topic, message, qos=1)
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    logger.debug(f"Device {self.device_id} published data")
                else:
                    logger.warning(f"Device {self.device_id} publish failed, queueing message")
                    self.offline_queue.add_message(self.topic, message, qos=1)
            except Exception as e:
                logger.error(f"Device {self.device_id} error publishing: {e}")
                self.offline_queue.add_message(self.topic, message, qos=1)
        else:
            self.offline_queue.add_message(self.topic, message, qos=1)
            logger.debug(f"Device {self.device_id} queued message (disconnected)")
    
    def connect(self):
        """Connect to MQTT broker."""
        try:
            logger.info(f"Device {self.device_id} connecting to {self.broker_host}:{self.broker_port}")
            self.client.connect(self.broker_host, self.broker_port, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            logger.error(f"Device {self.device_id} connection error: {e}")
            self.connected = False
    
    def disconnect(self):
        """Disconnect from MQTT broker."""
        self.running = False
        if self.connected:
            self.client.loop_stop()
            self.client.disconnect()
        logger.info(f"Device {self.device_id} stopped")
    
    def run(self):
        """Main loop to generate and publish speed data."""
        self.running = True
        logger.info(f"Device {self.device_id} started, publishing every {self.publish_interval}s")
        
        while self.running:
            try:
                speed = self.speed_simulator.get_next_speed()
                self._publish_device_data(speed)
                
                # Log queue size periodically
                queue_size = self.offline_queue.get_queue_size()
                if queue_size > 0:
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
        return psutil.cpu_percent(interval=1)

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

