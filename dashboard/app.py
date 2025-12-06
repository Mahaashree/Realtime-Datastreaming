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

# InfluxDB configuration - Support both local and remote
# Default to localhost for local development
INFLUXDB_URL_PRIMARY = os.getenv("INFLUXDB_URL", "http://localhost:8086")
# Only use fallback if explicitly set (don't default to domain for local dev)
INFLUXDB_URL_FALLBACK = os.getenv("INFLUXDB_URL_FALLBACK", None)
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "my-org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "vehicle-data")

# Initialize InfluxDB client
influx_client = None
query_api = None
influxdb_connected = False
current_influxdb_url = None

def init_influxdb():
    """Initialize InfluxDB connection with automatic fallback between local and remote."""
    global influx_client, query_api, influxdb_connected, current_influxdb_url
    
    # List of URLs to try (always try localhost first for local development)
    urls_to_try = []
    
    # Always prioritize localhost if it's not already in the list
    localhost_url = "http://localhost:8086"
    domain_url = "http://influxdb.secruin.cloud:8086"
    
    # Add primary URL if set
    if INFLUXDB_URL_PRIMARY:
        urls_to_try.append(INFLUXDB_URL_PRIMARY)
    
    # Add fallback URL if different from primary
    if INFLUXDB_URL_FALLBACK and INFLUXDB_URL_FALLBACK != INFLUXDB_URL_PRIMARY:
        urls_to_try.append(INFLUXDB_URL_FALLBACK)
    
    # If no URLs configured, use defaults (localhost first)
    if not urls_to_try:
        urls_to_try = [localhost_url]
        # Only add domain fallback if explicitly configured
        if INFLUXDB_URL_FALLBACK:
            urls_to_try.append(domain_url)
    else:
        # Reorder to try localhost first if it's in the list
        if localhost_url in urls_to_try:
            urls_to_try.remove(localhost_url)
            urls_to_try.insert(0, localhost_url)
    
    logger.info(f"InfluxDB connection URLs to try: {', '.join(urls_to_try)}")
    
    # Try each URL until one works
    for url in urls_to_try:
        try:
            logger.info(f"Attempting to connect to InfluxDB at {url}")
            test_client = InfluxDBClient(
                url=url,
                token=INFLUXDB_TOKEN,
                org=INFLUXDB_ORG,
                timeout=5  # Shorter timeout for faster fallback
            )
            
            # Test connection
            test_client.ping()
            
            # Connection successful - use this URL
            influx_client = test_client
            query_api = influx_client.query_api()
            influxdb_connected = True
            current_influxdb_url = url
            logger.info(f"✓ Successfully connected to InfluxDB at {url}")
            return
            
        except Exception as e:
            logger.warning(f"✗ Failed to connect to {url}: {e}")
            if influx_client:
                try:
                    influx_client.close()
                except:
                    pass
            continue
    
    # All URLs failed
    influxdb_connected = False
    logger.error("Failed to connect to InfluxDB at any configured URL")
    logger.error(f"Tried URLs: {', '.join(urls_to_try)}")
    logger.error("Make sure InfluxDB is running and accessible.")
    logger.info("Tip: Set INFLUXDB_URL for primary, INFLUXDB_URL_FALLBACK for secondary")

# Initialize on startup
init_influxdb()


@app.route('/')
def index():
    """Serve the dashboard page."""
    return render_template('dashboard.html')


@app.route('/api/health')
def health_check():
    """Health check endpoint showing InfluxDB connection status."""
    return jsonify({
        "status": "healthy" if influxdb_connected else "degraded",
        "influxdb_connected": influxdb_connected,
        "influxdb_url": current_influxdb_url or "none",
        "primary_url": INFLUXDB_URL_PRIMARY,
        "fallback_url": INFLUXDB_URL_FALLBACK if INFLUXDB_URL_FALLBACK != INFLUXDB_URL_PRIMARY else None
    })


@app.route('/api/devices/status')
def get_devices_status():
    """Get status of all devices."""
    global influxdb_connected
    
    if not influxdb_connected:
        # Try to reconnect
        logger.info("InfluxDB not connected, attempting to reconnect...")
        init_influxdb()
        if not influxdb_connected:
            return jsonify({
                "error": "InfluxDB connection failed",
                "message": f"Cannot connect to InfluxDB. Tried: {INFLUXDB_URL_PRIMARY}, {INFLUXDB_URL_FALLBACK}",
                "current_url": current_influxdb_url or "none",
                "tip": "Check that InfluxDB is running. For local dev, use: INFLUXDB_URL=http://localhost:8086"
            }), 503
    
    try:
        # Query last seen timestamp for each device
        # Try both measurement names (Python collector uses "vehicle_speed", Telegraf uses "mqtt_consumer")
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -1h)
          |> filter(fn: (r) => r["_measurement"] == "vehicle_speed" or r["_measurement"] == "mqtt_consumer")
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
        logger.error(f"Error getting device status: {e}")
        influxdb_connected = False  # Mark as disconnected
        return jsonify({
            "error": str(e),
            "message": "Failed to query InfluxDB. Check connection and try again."
        }), 500


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
        global influxdb_connected
        consecutive_errors = 0
        max_errors = 5
        
        while True:
            try:
                # Check connection status and verify it's still working
                if not influxdb_connected:
                    logger.info("InfluxDB disconnected, attempting to reconnect...")
                    init_influxdb()
                    if not influxdb_connected:
                        consecutive_errors += 1
                        if consecutive_errors >= max_errors:
                            logger.warning(f"InfluxDB still disconnected after {max_errors} attempts. Retrying in 30s...")
                            time.sleep(30)
                            consecutive_errors = 0
                        else:
                            time.sleep(5)
                        continue
                else:
                    # Verify connection is still alive
                    try:
                        influx_client.ping()
                    except Exception as e:
                        logger.warning(f"InfluxDB connection lost: {e}. Reconnecting...")
                        influxdb_connected = False
                        init_influxdb()
                        if not influxdb_connected:
                            consecutive_errors += 1
                            time.sleep(5)
                            continue
                
                # Query latest data for all devices
                # Support both measurement names (Python collector and Telegraf)
                query = f'''
                from(bucket: "{INFLUXDB_BUCKET}")
                  |> range(start: -10s)
                  |> filter(fn: (r) => r["_measurement"] == "vehicle_speed" or r["_measurement"] == "mqtt_consumer")
                  |> filter(fn: (r) => r["_field"] == "speed")
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
                    consecutive_errors = 0  # Reset error counter on success
                
                time.sleep(1)  # Broadcast every second
            except Exception as e:
                consecutive_errors += 1
                influxdb_connected = False
                logger.error(f"Error broadcasting data: {e}")
                # Try to reconnect
                init_influxdb()
                if consecutive_errors >= max_errors:
                    logger.warning(f"Multiple broadcast errors. Waiting 10s before retry...")
                    time.sleep(10)
                    consecutive_errors = 0
                else:
                    time.sleep(2)
    
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

