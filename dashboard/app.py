"""
Flask Dashboard Application
Provides REST API endpoints and WebSocket support for real-time vehicle data visualization.
"""

import os
import time
import logging
import requests
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

# Prometheus configuration
PROMETHEUS_PORT = int(os.getenv("PROMETHEUS_PORT", "9090"))
PROMETHEUS_URL = f"http://localhost:{PROMETHEUS_PORT}"

# Collector stats server (for accurate rolling window metrics)
STATS_SERVER_PORT = int(os.getenv("STATS_SERVER_PORT", "9091"))
STATS_SERVER_URL = f"http://localhost:{STATS_SERVER_PORT}"

# Store previous bucket values for rolling window calculation (Prometheus fallback)
_previous_buckets = {}
_previous_timestamp = None

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
                timeout=30000  # 30 seconds timeout (in milliseconds)
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


@app.route('/monitoring')
def monitoring():
    """Serve the monitoring page."""
    return render_template('monitoring.html')


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
        # Support all measurement names: device_data (Python collector - primary), vehicle_speed (legacy/Telegraf), mqtt_consumer (Telegraf)
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -1h)
          |> filter(fn: (r) => r["_measurement"] == "device_data" or r["_measurement"] == "vehicle_speed" or r["_measurement"] == "mqtt_consumer")
          |> filter(fn: (r) => r["_field"] == "speed")
          |> group(columns: ["device_id"])
          |> last()
          |> keep(columns: ["device_id", "_time"])
        '''
        
        try:
            result = query_api.query(query=query)
        except Exception as e:
            if "context canceled" in str(e).lower():
                logger.warning("Query canceled for device status, may be timeout")
                return jsonify({"error": "Query timeout", "message": "InfluxDB query timed out"}), 504
            raise
        
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
        # Query latest speed - supports device_data (primary), vehicle_speed (legacy), mqtt_consumer (Telegraf)
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -1h)
          |> filter(fn: (r) => (r["_measurement"] == "device_data" or r["_measurement"] == "vehicle_speed" or r["_measurement"] == "mqtt_consumer") and r["device_id"] == "{device_id}" and r["_field"] == "speed")
          |> last()
        '''
        
        try:
            result = query_api.query(query=query)
        except Exception as e:
            if "context canceled" in str(e).lower():
                logger.warning(f"Query canceled for device {device_id}, may be timeout")
                return jsonify({"error": "Query timeout"}), 504
            raise
        
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


@app.route('/api/devices/<device_id>/telemetry')
def get_device_telemetry(device_id):
    """Get latest telemetry data for a device."""
    try:
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -1h)
          |> filter(fn: (r) => r["_measurement"] == "device_data" and r["device_id"] == "{device_id}")
          |> filter(fn: (r) => r["_field"] == "cpu_usage" or r["_field"] == "ram_usage" or r["_field"] == "memory_percent")
          |> last()
        '''
        
        result = query_api.query(query=query)
        
        telemetry = {}
        for table in result:
            for record in table.records:
                field = record.get_field()
                value = record.get_value()
                telemetry[field] = value
        
        return jsonify({
            "device_id": device_id,
            "telemetry": telemetry
        })
    except Exception as e:
        logger.error(f"Error getting telemetry: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/devices/<device_id>/detections')
def get_device_detections(device_id):
    """Get recent detection labels for a device."""
    try:
        duration = request.args.get('duration', '5m')
        
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -{duration})
          |> filter(fn: (r) => r["_measurement"] == "device_data" and r["device_id"] == "{device_id}")
          |> filter(fn: (r) => r["_field"] == "detection_confidence")
          |> keep(columns: ["_time", "detection_label", "detection_confidence"])
        '''
        
        result = query_api.query(query=query)
        
        detections = []
        for table in result:
            for record in table.records:
                detections.append({
                    "timestamp": record.get_time().timestamp(),
                    "label": record.values.get("detection_label"),
                    "confidence": record.get_value()
                })
        
        return jsonify({
            "device_id": device_id,
            "detections": detections
        })
    except Exception as e:
        logger.error(f"Error getting detections: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/metrics')
def get_metrics():
    """Fetch metrics - prefer collector stats server for accurate rolling window, fallback to Prometheus."""
    try:
        # First, try to get stats from collector's stats server (most accurate - rolling window)
        try:
            stats_response = requests.get(f"{STATS_SERVER_URL}/stats", timeout=2)
            if stats_response.status_code == 200:
                stats_data = stats_response.json()
                # Convert MetricsCollector stats format to dashboard format
                parsed_metrics = {
                    "timestamp": stats_data.get("timestamp", time.time()),
                    "prometheus_available": True,  # Still available, just not used for latency
                    "counters": stats_data.get("counters", {}),
                    "gauges": {
                        "queue_depth": stats_data.get("queue", {}).get("current_depth", 0),
                        "throughput": stats_data.get("rates", {}).get("throughput_msg_per_sec", 0)
                    },
                    "latency": stats_data.get("latency"),  # Already in correct format from MetricsCollector
                    "rates": stats_data.get("rates", {}),
                    "source": "stats_server"  # Indicate we're using stats server
                }
                logger.info(f"Using collector stats server - latency: {parsed_metrics.get('latency')}")
                return jsonify(parsed_metrics)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Stats server not available at {STATS_SERVER_URL}/stats, falling back to Prometheus: {e}")
            logger.warning("⚠️  Note: Prometheus shows cumulative latency (all-time), not rolling window. Restart collector to enable stats server.")
        
        # Fallback: Fetch raw Prometheus metrics
        response = requests.get(f"{PROMETHEUS_URL}/metrics", timeout=5)
        if response.status_code != 200:
            return jsonify({
                "error": "Failed to fetch Prometheus metrics",
                "status_code": response.status_code
            }), 503
        
        # Parse Prometheus metrics format
        metrics_data = {}
        lines = response.text.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith('#'):
                continue
            
            # Parse metric line: metric_name{labels} value
            if '{' in line and '}' in line:
                # Metric with labels
                parts = line.split('}')
                if len(parts) == 2:
                    metric_part = parts[0] + '}'
                    value = parts[1].strip()
                    
                    # Extract metric name and labels
                    if '{' in metric_part:
                        name = metric_part.split('{')[0]
                        labels = metric_part.split('{')[1].rstrip('}')
                        
                        if name not in metrics_data:
                            metrics_data[name] = []
                        
                        # Parse labels
                        label_dict = {}
                        if labels:
                            for label_pair in labels.split(','):
                                if '=' in label_pair:
                                    key, val = label_pair.split('=', 1)
                                    label_dict[key.strip()] = val.strip('"')
                        
                        try:
                            metrics_data[name].append({
                                "labels": label_dict,
                                "value": float(value)
                            })
                        except ValueError:
                            pass
            else:
                # Simple metric without labels: metric_name value
                parts = line.split()
                if len(parts) == 2:
                    name = parts[0]
                    try:
                        value = float(parts[1])
                        if name not in metrics_data:
                            metrics_data[name] = []
                        metrics_data[name].append({
                            "labels": {},
                            "value": value
                        })
                    except ValueError:
                        pass
        
        # Extract key metrics for the dashboard
        parsed_metrics = {
            "timestamp": time.time(),
            "prometheus_available": True,
            "counters": {},
            "gauges": {},
            "histograms": {},
            "summaries": {}
        }
        
        # Extract specific metrics we care about
        metric_mappings = {
            "mqtt_messages_total": ("counters", "total_messages"),
            "mqtt_messages_processed_total": ("counters", "processed_messages"),
            "mqtt_messages_errors_total": ("counters", "error_messages"),
            "mqtt_messages_dropped_total": ("counters", "dropped_messages"),
            "mqtt_queue_depth": ("gauges", "queue_depth"),
            "mqtt_throughput_messages_per_second": ("gauges", "throughput")
        }
        
        for metric_name, (category, key) in metric_mappings.items():
            if metric_name in metrics_data:
                # Sum all values for counters, take last for gauges
                if category == "counters":
                    total = sum(item["value"] for item in metrics_data[metric_name])
                    parsed_metrics[category][key] = total
                else:
                    # For gauges, take the last value
                    if metrics_data[metric_name]:
                        parsed_metrics[category][key] = metrics_data[metric_name][-1]["value"]
        
        # Extract latency data from Histogram buckets
        # Since Prometheus histograms are cumulative, we need to calculate deltas
        # to approximate a rolling window (similar to MetricsCollector's 10k sample window)
        global _previous_buckets, _previous_timestamp
        
        latency_p50 = None
        latency_p95 = None
        latency_p99 = None
        latency_count = 0
        latency_sum = 0
        
        current_time = time.time()
        
        # Calculate percentiles from histogram buckets using delta approach
        if "mqtt_message_latency_seconds_bucket" in metrics_data:
            # Parse current histogram buckets
            current_buckets = {}
            for item in metrics_data["mqtt_message_latency_seconds_bucket"]:
                le = item.get("labels", {}).get("le")
                if le and le != "+Inf":  # Skip infinity bucket
                    try:
                        bucket_value = float(le)
                        count = int(item["value"])
                        current_buckets[bucket_value] = count
                    except (ValueError, TypeError):
                        pass
            
            # Calculate delta from previous buckets (approximates rolling window)
            if _previous_buckets and _previous_timestamp and (current_time - _previous_timestamp) < 60:
                # Calculate delta buckets (new samples since last check)
                delta_buckets = []
                for bucket_val in sorted(current_buckets.keys()):
                    current_count = current_buckets[bucket_val]
                    prev_count = _previous_buckets.get(bucket_val, 0)
                    delta_count = current_count - prev_count
                    if delta_count > 0:
                        delta_buckets.append((bucket_val, delta_count))
                
                # Calculate total delta count
                total_delta = sum(count for _, count in delta_buckets)
                
                if total_delta > 0 and len(delta_buckets) > 0:
                    # Calculate percentiles from delta buckets (rolling window approximation)
                    def get_percentile_from_delta_buckets(p):
                        """Calculate percentile from delta buckets."""
                        target_count = total_delta * p
                        cumulative = 0
                        prev_val = 0.0
                        prev_cumulative = 0
                        
                        for bucket_val, count in delta_buckets:
                            cumulative += count
                            if cumulative >= target_count:
                                # Interpolate within this bucket
                                if prev_cumulative < target_count:
                                    ratio = (target_count - prev_cumulative) / (cumulative - prev_cumulative) if (cumulative - prev_cumulative) > 0 else 1.0
                                    interpolated = prev_val + (bucket_val - prev_val) * ratio
                                else:
                                    interpolated = bucket_val
                                return interpolated * 1000  # Convert to ms
                            prev_val = bucket_val
                            prev_cumulative = cumulative
                        
                        # Fallback to max bucket
                        return delta_buckets[-1][0] * 1000 if delta_buckets else 0
                    
                    latency_p50 = get_percentile_from_delta_buckets(0.50)
                    latency_p95 = get_percentile_from_delta_buckets(0.95)
                    latency_p99 = get_percentile_from_delta_buckets(0.99)
                    latency_count = total_delta
                    
                    # Calculate average from sum delta
                    if "mqtt_message_latency_seconds_sum" in metrics_data:
                        current_sum = sum(item["value"] for item in metrics_data["mqtt_message_latency_seconds_sum"])
                        prev_sum = _previous_buckets.get("_sum", 0)
                        delta_sum = (current_sum - prev_sum) * 1000  # Convert to ms
                        latency_sum = delta_sum
                        _previous_buckets["_sum"] = current_sum
                    
                    logger.debug(f"Calculated latency from delta buckets (rolling window): p50={latency_p50:.2f}ms, p95={latency_p95:.2f}ms, p99={latency_p99:.2f}ms, count={latency_count}")
            
            # Store current buckets for next calculation
            _previous_buckets = current_buckets.copy()
            _previous_timestamp = current_time
            
            # If no previous data, use current cumulative (first time)
            if not _previous_buckets or _previous_timestamp is None:
                sorted_buckets = sorted(current_buckets.items())
                total_count = max(current_buckets.values()) if current_buckets else 0
                
                if total_count > 0:
                    def get_percentile_from_buckets(p):
                        target_count = total_count * p
                        prev_val = 0.0
                        prev_count = 0
                        
                        for bucket_val, count in sorted_buckets:
                            if count >= target_count:
                                if prev_count == 0:
                                    return bucket_val * 1000
                                if count > prev_count:
                                    ratio = (target_count - prev_count) / (count - prev_count)
                                    interpolated = prev_val + (bucket_val - prev_val) * ratio
                                else:
                                    interpolated = bucket_val
                                return interpolated * 1000
                            prev_val = bucket_val
                            prev_count = count
                        return sorted_buckets[-1][0] * 1000 if sorted_buckets else 0
                    
                    latency_p50 = get_percentile_from_buckets(0.50)
                    latency_p95 = get_percentile_from_buckets(0.95)
                    latency_p99 = get_percentile_from_buckets(0.99)
                    latency_count = total_count
                    
                    if "mqtt_message_latency_seconds_sum" in metrics_data:
                        latency_sum = sum(item["value"] for item in metrics_data["mqtt_message_latency_seconds_sum"]) * 1000
                        _previous_buckets["_sum"] = sum(item["value"] for item in metrics_data["mqtt_message_latency_seconds_sum"])
        
        # Build latency metrics if we have data
        if latency_count > 0 and (latency_p50 is not None or latency_p95 is not None or latency_p99 is not None):
            latency_avg = (latency_sum / latency_count) if latency_count > 0 else 0
            
            parsed_metrics["latency"] = {
                "p50": latency_p50 if latency_p50 is not None else 0,
                "p95": latency_p95 if latency_p95 is not None else 0,
                "p99": latency_p99 if latency_p99 is not None else 0,
                "avg": latency_avg,
                "count": latency_count
            }
            logger.debug(f"Latency metrics: {parsed_metrics['latency']}")
        else:
            logger.debug("No latency data found in Prometheus metrics")
        
        # Calculate rates
        total = parsed_metrics["counters"].get("total_messages", 0)
        processed = parsed_metrics["counters"].get("processed_messages", 0)
        errors = parsed_metrics["counters"].get("error_messages", 0)
        drops = parsed_metrics["counters"].get("dropped_messages", 0)
        
        parsed_metrics["rates"] = {
            "success_rate_percent": (processed / total * 100) if total > 0 else 0,
            "error_rate_percent": (errors / total * 100) if total > 0 else 0,
            "drop_rate_percent": (drops / total * 100) if total > 0 else 0,
            "throughput_msg_per_sec": parsed_metrics["gauges"].get("throughput", 0)
        }
        
        return jsonify(parsed_metrics)
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching Prometheus metrics: {e}")
        return jsonify({
            "error": "Prometheus metrics unavailable",
            "message": str(e),
            "prometheus_available": False,
            "prometheus_url": PROMETHEUS_URL
        }), 503
    except Exception as e:
        logger.error(f"Error parsing Prometheus metrics: {e}")
        return jsonify({
            "error": "Failed to parse metrics",
            "message": str(e)
        }), 500


@app.route('/api/devices/<device_id>/history')
def get_device_history(device_id):
    """Get historical speed data for a specific device."""
    try:
        duration = request.args.get('duration', '5m')  # Default 5 minutes
        
        # Query supports device_data (primary), vehicle_speed (legacy), mqtt_consumer (Telegraf)
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -{duration})
          |> filter(fn: (r) => (r["_measurement"] == "device_data" or r["_measurement"] == "vehicle_speed" or r["_measurement"] == "mqtt_consumer") and r["device_id"] == "{device_id}" and r["_field"] == "speed")
          |> aggregateWindow(every: 1s, fn: mean, createEmpty: false)
          |> yield(name: "mean")
        '''
        
        try:
            result = query_api.query(query=query)
        except Exception as e:
            if "context canceled" in str(e).lower():
                logger.warning(f"Query canceled for device {device_id} history, may be timeout")
                return jsonify({"error": "Query timeout"}), 504
            raise
        
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
                # Optimized: combined filters, shorter time range
                query = f'''
                from(bucket: "{INFLUXDB_BUCKET}")
                  |> range(start: -30s)
                  |> filter(fn: (r) => (r["_measurement"] == "device_data"))
                  |> filter(fn: (r) => r["collector"] == "telegraf" or r["collector"] == "python")
                  |> group(columns: ["device_id", "_field"])
                  |> last()
                '''

                try:
                    result = query_api.query(query=query)
                except Exception as query_error:
                    # Handle query cancellation/timeout gracefully
                    error_str = str(query_error).lower()
                    if "context canceled" in error_str or "timeout" in error_str:
                        logger.debug(f"Query timeout/canceled (may be normal): {query_error}")
                        consecutive_errors += 1
                        if consecutive_errors >= max_errors:
                            logger.warning("Multiple query timeouts, waiting before retry...")
                            time.sleep(10)
                            consecutive_errors = 0
                        else:
                            time.sleep(2)
                        continue
                    else:
                        raise
                
                #processing results to group by device
                latest_data = {}
                for table in result:
                    for record in table.records:
                        device_id = record.values.get("device_id")
                        field = record.values.get("_field")
                        value = record.get_value()

                        if device_id not in latest_data:
                            latest_data[device_id] = {}

                        latest_data[device_id][field] = value
                        latest_data[device_id]["detection_label"] = record.values.get("detection_label", "normal")
                        latest_data[device_id]["timestamp"] = record.get_time().timestamp() 
                
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
