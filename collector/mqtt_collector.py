"""
MQTT Collector - Subscribes to vehicle speed topics and stores data in InfluxDB.
"""

import paho.mqtt.client as mqtt
import json
import time
import logging
import os
from datetime import datetime
from typing import Dict, Optional
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DeviceStatusTracker:
    """Tracks device connection status and last seen timestamps."""
    
    def __init__(self):
        self.device_status: Dict[str, Dict] = {}
        self.status_timeout = 10  # Consider device offline if no message for 10 seconds
    
    def update_device(self, device_id: str):
        """Update device last seen timestamp."""
        current_time = time.time()
        if device_id not in self.device_status:
            self.device_status[device_id] = {
                "last_seen": current_time,
                "status": "online",
                "message_count": 0
            }
            logger.info(f"New device detected: {device_id}")
        else:
            self.device_status[device_id]["last_seen"] = current_time
            self.device_status[device_id]["status"] = "online"
        
        self.device_status[device_id]["message_count"] += 1
    
    def get_device_status(self, device_id: str) -> Dict:
        """Get device status information."""
        if device_id not in self.device_status:
            return {"status": "unknown", "last_seen": None, "message_count": 0}
        
        device_info = self.device_status[device_id].copy()
        current_time = time.time()
        
        # Check if device is offline
        if current_time - device_info["last_seen"] > self.status_timeout:
            device_info["status"] = "offline"
        
        return device_info
    
    def get_all_devices_status(self) -> Dict[str, Dict]:
        """Get status for all devices."""
        current_time = time.time()
        result = {}
        
        for device_id, info in self.device_status.items():
            device_info = info.copy()
            if current_time - info["last_seen"] > self.status_timeout:
                device_info["status"] = "offline"
            result[device_id] = device_info
        
        return result


class MQTTCollector:
    """Collects MQTT messages and stores them in InfluxDB."""
    
    def __init__(self, broker_host: str = "localhost", broker_port: int = 1883,
                 influxdb_url: str = None, influxdb_token: str = None,
                 influxdb_org: str = None, influxdb_bucket: str = None):
        
        self.broker_host = broker_host
        self.broker_port = broker_port
        
        # InfluxDB configuration
        self.influxdb_url = influxdb_url or os.getenv("INFLUXDB_URL", "http://localhost:8086")
        self.influxdb_token = influxdb_token or os.getenv("INFLUXDB_TOKEN")
        self.influxdb_org = influxdb_org or os.getenv("INFLUXDB_ORG", "my-org")
        self.influxdb_bucket = influxdb_bucket or os.getenv("INFLUXDB_BUCKET", "vehicle-data")
        
        # Initialize InfluxDB client
        self.influx_client = None
        self.write_api = None
        self._init_influxdb()
        
        # Device status tracker
        self.status_tracker = DeviceStatusTracker()
        
        # MQTT client setup
        self.client = mqtt.Client(client_id="mqtt_collector", clean_session=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        
        self.connected = False
        self.message_count = 0
    
    def _init_influxdb(self):
        """Initialize InfluxDB client."""
        try:
            self.influx_client = InfluxDBClient(
                url=self.influxdb_url,
                token=self.influxdb_token,
                org=self.influxdb_org
            )
            self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
            logger.info(f"Connected to InfluxDB at {self.influxdb_url}")
        except Exception as e:
            logger.error(f"Failed to initialize InfluxDB: {e}")
            raise
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when MQTT client connects."""
        if rc == 0:
            self.connected = True
            logger.info("MQTT Collector connected to broker")
            # Subscribe to all vehicle speed topics
            topic = "vehicle/speed/+"
            client.subscribe(topic, qos=1)
            logger.info(f"Subscribed to topic: {topic}")
        else:
            logger.error(f"Failed to connect to MQTT broker, return code {rc}")
            self.connected = False
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when MQTT client disconnects."""
        self.connected = False
        if rc != 0:
            logger.warning("MQTT Collector unexpectedly disconnected")
        else:
            logger.info("MQTT Collector disconnected")
    
    def _on_message(self, client, userdata, msg):
        """Callback when a message is received."""
        try:
            # Parse JSON payload
            payload = json.loads(msg.payload.decode())
            device_id = payload.get("device_id")
            speed = payload.get("speed")
            timestamp = payload.get("timestamp")
            
            if not all([device_id, speed is not None, timestamp]):
                logger.warning(f"Invalid message format: {payload}")
                return
            
            # Update device status
            self.status_tracker.update_device(device_id)
            
            # Write to InfluxDB
            point = Point("vehicle_speed") \
                .tag("device_id", device_id) \
                .field("speed", float(speed)) \
                .time(int(timestamp * 1e9), WritePrecision.NS)
            
            self.write_api.write(bucket=self.influxdb_bucket, record=point)
            
            self.message_count += 1
            if self.message_count % 100 == 0:
                logger.info(f"Processed {self.message_count} messages")
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON message: {e}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def connect(self):
        """Connect to MQTT broker."""
        try:
            logger.info(f"Connecting to MQTT broker at {self.broker_host}:{self.broker_port}")
            self.client.connect(self.broker_host, self.broker_port, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            logger.error(f"Connection error: {e}")
            self.connected = False
    
    def disconnect(self):
        """Disconnect from MQTT broker and close InfluxDB connection."""
        if self.connected:
            self.client.loop_stop()
            self.client.disconnect()
        
        if self.write_api:
            self.write_api.close()
        
        if self.influx_client:
            self.influx_client.close()
        
        logger.info("MQTT Collector stopped")
    
    def get_device_status(self, device_id: str = None) -> Dict:
        """Get device status information."""
        if device_id:
            return self.status_tracker.get_device_status(device_id)
        else:
            return self.status_tracker.get_all_devices_status()
    
    def run(self):
        """Run the collector (blocking)."""
        self.connect()
        
        # Wait a bit for connection
        time.sleep(2)
        
        if not self.connected:
            logger.error("Failed to connect to MQTT broker. Exiting.")
            return
        
        try:
            logger.info("MQTT Collector running. Press Ctrl+C to stop.")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self.disconnect()


def main():
    """Main entry point for MQTT collector."""
    import os
    
    broker_host = os.getenv("MQTT_BROKER_HOST", "localhost")
    broker_port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
    
    collector = MQTTCollector(broker_host=broker_host, broker_port=broker_port)
    
    try:
        collector.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        collector.disconnect()


if __name__ == "__main__":
    main()

