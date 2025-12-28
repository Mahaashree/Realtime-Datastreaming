"""
MQTT Collector - Subscribes to MQTT messages and writes to InfluxDB
"""
import json
import time
import os
from datetime import datetime
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# MQTT Configuration
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_CLIENT_ID = "mqtt-collector-python"

# InfluxDB Configuration
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "my-org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "vehicle-data")

class MQTTCollector:
    def __init__(self):
        # Initialize InfluxDB client
        self.influx_client = InfluxDBClient(
            url=INFLUXDB_URL,
            token=INFLUXDB_TOKEN,
            org=INFLUXDB_ORG
        )
        self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
        
        # Initialize MQTT client
        self.mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_disconnect = self._on_disconnect
        
        self.message_count = 0
        
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when MQTT client connects."""
        if rc == 0:
            print(f"✅ Connected to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
            # Subscribe to device data topics
            client.subscribe("device/data/+", qos=1)
            client.subscribe("vehicle/speed/+", qos=1)  # Legacy topic
            print("📡 Subscribed to topics: device/data/+, vehicle/speed/+")
        else:
            print(f"❌ Failed to connect to MQTT broker. Return code: {rc}")
    
    def _on_message(self, client, userdata, msg):
        """Callback when MQTT message is received."""
        try:
            payload = json.loads(msg.payload.decode())
            device_id = payload.get("device_id")
            
            if not device_id:
                print("⚠️  Warning: Message missing device_id")
                return
            
            # Create InfluxDB point
            point = Point("device_data") \
                .tag("device_id", device_id) \
                .tag("collector", "python")
            
            # Add speed field if present
            if "speed" in payload:
                point = point.field("speed", float(payload["speed"]))
            
            # Add telemetry fields if present
            if "telemetry" in payload:
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
            
            # Add detection fields if present
            if "detection" in payload:
                detection = payload["detection"]
                point = point.tag("detection_label", detection.get("label", "unknown")) \
                          .field("detection_confidence", float(detection.get("confidence", 0.0)))
            
            # Write to InfluxDB
            self.write_api.write(bucket=INFLUXDB_BUCKET, record=point)
            self.message_count += 1
            
            if self.message_count % 100 == 0:
                print(f"📊 Processed {self.message_count} messages")
                
        except json.JSONDecodeError as e:
            print(f"❌ Error decoding JSON: {e}")
        except Exception as e:
            print(f"❌ Error processing message: {e}")
            import traceback
            traceback.print_exc()
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when MQTT client disconnects."""
        if rc != 0:
            print(f"⚠️  Unexpected MQTT disconnection. Reconnecting...")
    
    def start(self):
        """Start the collector."""
        try:
            print("🚀 Starting MQTT Collector...")
            print(f"   MQTT Broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
            print(f"   InfluxDB: {INFLUXDB_URL}")
            print(f"   Bucket: {INFLUXDB_BUCKET}")
            
            # Connect to MQTT broker
            self.mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
            
            # Start the loop (blocks forever)
            self.mqtt_client.loop_forever()
            
        except KeyboardInterrupt:
            print("\n🛑 Stopping collector...")
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self.influx_client.close()
            print(f"✅ Processed {self.message_count} messages total")
        except Exception as e:
            print(f"❌ Error starting collector: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    collector = MQTTCollector()
    collector.start()