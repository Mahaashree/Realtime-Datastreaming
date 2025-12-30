"""
Device Status API - Lightweight service for device status tracking
Use this if you want to keep device status functionality when using Telegraf
for data collection instead of the full Python collector.
"""

from flask import Flask, jsonify
from influxdb_client import InfluxDBClient
from influxdb_client.client.query_api import QueryApi
import os
import time
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# InfluxDB configuration
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "my-org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "vehicle-data")

# Initialize InfluxDB client
influx_client = InfluxDBClient(
    url=INFLUXDB_URL,
    token=INFLUXDB_TOKEN,
    org=INFLUXDB_ORG
)
query_api = influx_client.query_api()


@app.route('/api/devices/status')
def get_devices_status():
    """Get status of all devices based on InfluxDB data."""
    try:
        # Query last seen timestamp for each device
        # Support all measurement names: device_data (Python collector), vehicle_speed (legacy), mqtt_consumer (Telegraf)
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -1h)
          |> filter(fn: (r) => r["_measurement"] == "device_data" or r["_measurement"] == "vehicle_speed" or r["_measurement"] == "mqtt_consumer")
          |> filter(fn: (r) => r["_field"] == "speed")
          |> group(columns: ["device_id"])
          |> last()
          |> keep(columns: ["device_id", "_time"])
        '''
        
        result = query_api.query(query)
        
        devices_status = {}
        current_time = time.time()
        timeout = 10  # seconds
        
        for table in result:
            for record in table.records:
                device_id = record.values.get("device_id")
                last_seen_time = record.get_time().timestamp()
                
                time_diff = current_time - last_seen_time
                status = "online" if time_diff < timeout else "offline"
                
                devices_status[device_id] = {
                    "status": status,
                    "last_seen": last_seen_time,
                    "time_since_last_seen": time_diff
                }
        
        return jsonify(devices_status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy"})


if __name__ == '__main__':
    port = int(os.getenv("DEVICE_STATUS_PORT", "5001"))
    app.run(host='0.0.0.0', port=port, debug=False)

