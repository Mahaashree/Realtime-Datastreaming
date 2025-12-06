"""
Flask Dashboard Application
Provides REST API endpoints and WebSocket support for real-time vehicle data visualization.
"""

import os
import time
import logging
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient
from influxdb_client.client.query_api import QueryApi

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
socketio = SocketIO(app, cors_allowed_origins="*")

# InfluxDB configuration
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "my-org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "vehicle-data")

# Initialize InfluxDB client
influx_client = None
query_api = None

try:
    influx_client = InfluxDBClient(
        url=INFLUXDB_URL,
        token=INFLUXDB_TOKEN,
        org=INFLUXDB_ORG
    )
    query_api = influx_client.query_api()
    logger.info("Connected to InfluxDB")
except Exception as e:
    logger.error(f"Failed to initialize InfluxDB: {e}")


@app.route('/')
def index():
    """Serve the dashboard page."""
    return render_template('dashboard.html')


@app.route('/api/devices/status')
def get_devices_status():
    """Get status of all devices."""
    try:
        # Query last seen timestamp for each device
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -1h)
          |> filter(fn: (r) => r["_measurement"] == "vehicle_speed")
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
        logger.error(f"Error getting device status: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/devices/<device_id>/latest')
def get_device_latest(device_id):
    """Get latest speed data for a specific device."""
    try:
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -1h)
          |> filter(fn: (r) => r["_measurement"] == "vehicle_speed" and r["device_id"] == "{device_id}")
          |> last()
        '''
        
        result = query_api.query(query)
        
        for table in result:
            for record in table.records:
                return jsonify({
                    "device_id": device_id,
                    "speed": record.get_value(),
                    "timestamp": record.get_time().timestamp()
                })
        
        return jsonify({"error": "No data found"}), 404
    except Exception as e:
        logger.error(f"Error getting device latest: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/devices/<device_id>/history')
def get_device_history(device_id):
    """Get historical speed data for a specific device."""
    try:
        duration = request.args.get('duration', '5m')  # Default 5 minutes
        
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -{duration})
          |> filter(fn: (r) => r["_measurement"] == "vehicle_speed" and r["device_id"] == "{device_id}")
          |> aggregateWindow(every: 1s, fn: mean, createEmpty: false)
          |> yield(name: "mean")
        '''
        
        result = query_api.query(query)
        
        data_points = []
        for table in result:
            for record in table.records:
                data_points.append({
                    "timestamp": record.get_time().timestamp(),
                    "speed": record.get_value()
                })
        
        return jsonify({
            "device_id": device_id,
            "data_points": data_points
        })
    except Exception as e:
        logger.error(f"Error getting device history: {e}")
        return jsonify({"error": str(e)}), 500


@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection."""
    logger.info('Client connected')
    emit('connected', {'data': 'Connected to dashboard'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection."""
    logger.info('Client disconnected')


def broadcast_latest_data():
    """Periodically broadcast latest data to all connected clients."""
    import threading
    
    def broadcast_loop():
        while True:
            try:
                # Query latest data for all devices
                query = f'''
                from(bucket: "{INFLUXDB_BUCKET}")
                  |> range(start: -10s)
                  |> filter(fn: (r) => r["_measurement"] == "vehicle_speed")
                  |> group(columns: ["device_id"])
                  |> last()
                '''
                
                result = query_api.query(query)
                
                latest_data = {}
                for table in result:
                    for record in table.records:
                        device_id = record.values.get("device_id")
                        latest_data[device_id] = {
                            "speed": record.get_value(),
                            "timestamp": record.get_time().timestamp()
                        }
                
                if latest_data:
                    socketio.emit('latest_data', latest_data)
                
                time.sleep(1)  # Broadcast every second
            except Exception as e:
                logger.error(f"Error broadcasting data: {e}")
                time.sleep(5)
    
    # Start broadcast thread
    thread = threading.Thread(target=broadcast_loop, daemon=True)
    thread.start()


if __name__ == '__main__':
    # Start background thread for real-time updates
    broadcast_latest_data()
    
    # Run Flask app
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    
    logger.info(f"Starting Flask dashboard on {host}:{port}")
    socketio.run(app, host=host, port=port, debug=True)

