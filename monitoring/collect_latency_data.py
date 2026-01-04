#!/usr/bin/env python3
"""
Collect latency performance data over time and store it for reporting
"""

import os
import json
import time
import argparse
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient
from dotenv import load_dotenv
import statistics

# Load .env from parent directory
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# Configuration
MONITORING_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(MONITORING_DIR, "latency_data.json")

# Data retention: default 10 minutes, configurable via env var
# Format: "10m" (minutes) or "1h" (hours) or "30s" (seconds)
RETENTION_DURATION = os.getenv('LATENCY_RETENTION_DURATION', '10m')

def parse_duration(duration_str):
    """Parse duration string like '10m', '1h', '30s' to seconds."""
    duration_str = duration_str.lower().strip()
    if duration_str.endswith('s'):
        return int(duration_str[:-1])
    elif duration_str.endswith('m'):
        return int(duration_str[:-1]) * 60
    elif duration_str.endswith('h'):
        return int(duration_str[:-1]) * 3600
    else:
        # Default to minutes if no suffix
        return int(duration_str) * 60

def get_max_samples(collection_interval=60):
    """Calculate max samples based on retention duration and collection interval."""
    retention_seconds = parse_duration(RETENTION_DURATION)
    max_samples = int(retention_seconds / collection_interval) + 10  # Add buffer
    return max_samples

def get_latency_stats(duration='5m'):
    """Calculate end-to-end latency statistics."""
    client = InfluxDBClient(
        url=os.getenv('INFLUXDB_URL', 'http://localhost:8086'),
        token=os.getenv('INFLUXDB_TOKEN'),
        org=os.getenv('INFLUXDB_ORG', 'my-org')
    )
    query_api = client.query_api()
    bucket = os.getenv('INFLUXDB_BUCKET', 'vehicle-data')
    
    # Query to get publish_timestamp and _time (InfluxDB write time)
    query = f'''
    from(bucket: "{bucket}")
      |> range(start: -{duration})
      |> filter(fn: (r) => r["_measurement"] == "device_data")
      |> filter(fn: (r) => r["_field"] == "publish_timestamp")
      |> keep(columns: ["_time", "_value", "device_id"])
    '''
    
    result = query_api.query(query)
    
    latencies = []
    for table in result:
        for record in table.records:
            write_time = record.get_time().timestamp()
            publish_time = record.get_value()
            latency_seconds = write_time - publish_time
            
            if 0 < latency_seconds < 60:
                latencies.append(latency_seconds * 1000)  # Convert to ms
    
    client.close()
    
    if not latencies:
        return None
    
    latencies_sorted = sorted(latencies)
    n = len(latencies)
    
    return {
        'timestamp': datetime.now().isoformat(),
        'unix_timestamp': time.time(),
        'count': n,
        'min_ms': min(latencies),
        'max_ms': max(latencies),
        'avg_ms': statistics.mean(latencies),
        'median_ms': statistics.median(latencies),
        'p95_ms': latencies_sorted[int(n * 0.95)] if n > 0 else 0,
        'p99_ms': latencies_sorted[int(n * 0.99)] if n > 0 else 0,
        'p50_ms': latencies_sorted[int(n * 0.50)] if n > 0 else 0,
        'target_met': latencies_sorted[int(n * 0.95)] < 2000 if n > 0 else False
    }

def load_data():
    """Load existing latency data from file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_data(data):
    """Save latency data to file."""
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def prune_old_data(data, retention_seconds):
    """Remove data older than retention duration."""
    if not data:
        return data
    
    current_time = time.time()
    cutoff_time = current_time - retention_seconds
    
    pruned = []
    for entry in data:
        entry_time = entry.get('unix_timestamp', 0)
        if entry_time >= cutoff_time:
            pruned.append(entry)
    
    return pruned

def collect_sample(duration='5m'):
    """Collect a single latency sample.
    
    Args:
        duration: Time window to query (e.g., '5m', '1h', '30s'). Default: '5m'
    """
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Collecting latency data (window: {duration})...")
    
    stats = get_latency_stats(duration)
    
    if not stats:
        # Try with longer time windows to see if data exists
        print("   ⚠️  No data in specified time window, checking longer windows...")
        fallback_windows = ['15m', '1h', '6h', '24h']
        for fallback_duration in fallback_windows:
            if fallback_duration == duration:
                continue  # Skip if already checked
            stats = get_latency_stats(fallback_duration)
            if stats:
                print(f"   ℹ️  Found data in last {fallback_duration} (but not in last {duration})")
                print(f"   → Devices may have stopped. Check device status.")
                print(f"   → Try running with: --duration {fallback_duration}")
                # Don't save this - we only want data from the specified window
                return None
        
        print("   ⚠️  No data available in any time window checked")
        print("   → Check if devices are running: ps aux | grep device_simulator")
        print("   → Check if collector is running: ps aux | grep mqtt_collector")
        print("   → Check InfluxDB connection: python check_influxdb_connection.py")
        return None
    
    data = load_data()
    data.append(stats)
    
    # Prune old data based on retention duration
    retention_seconds = parse_duration(RETENTION_DURATION)
    data = prune_old_data(data, retention_seconds)
    
    save_data(data)
    
    retention_minutes = retention_seconds / 60
    print(f"   ✅ Collected: P95={stats['p95_ms']:.1f}ms, Avg={stats['avg_ms']:.1f}ms, Count={stats['count']}")
    print(f"   📊 Total samples: {len(data)} (retention: {retention_minutes:.1f} min)")
    
    return stats

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Collect latency performance data over time and store it for reporting',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Single collection with default 5-minute window
  python monitoring/collect_latency_data.py
  
  # Single collection with 1-hour window
  python monitoring/collect_latency_data.py --duration 1h
  
  # Continuous collection with 15-minute window
  python monitoring/collect_latency_data.py --continuous --duration 15m
  
  # Continuous collection with custom interval and duration
  python monitoring/collect_latency_data.py --continuous --duration 30m --interval 120

Duration format: Use 's' for seconds, 'm' for minutes, 'h' for hours
  Examples: '30s', '5m', '1h', '24h'
        '''
    )
    parser.add_argument(
        '--continuous', '-c',
        action='store_true',
        help='Run in continuous collection mode (collects data at regular intervals)'
    )
    parser.add_argument(
        '--duration', '-d',
        type=str,
        default='5m',
        help='Time window to query from InfluxDB (default: 5m). Format: 30s, 5m, 1h, etc.'
    )
    parser.add_argument(
        '--interval', '-i',
        type=int,
        default=None,
        help='Collection interval in seconds for continuous mode (default: from LATENCY_COLLECT_INTERVAL env var or 60)'
    )
    
    args = parser.parse_args()
    
    # Validate duration format
    try:
        parse_duration(args.duration)
    except (ValueError, AttributeError):
        print(f"❌ Invalid duration format: {args.duration}")
        print("   Use format like: 30s, 5m, 1h, 24h")
        exit(1)
    
    if args.continuous:
        # Continuous collection mode
        interval = args.interval if args.interval is not None else int(os.getenv('LATENCY_COLLECT_INTERVAL', '60'))
        retention_str = RETENTION_DURATION
        print(f"🔄 Starting continuous latency data collection")
        print(f"   Collection interval: {interval}s")
        print(f"   Query window: {args.duration}")
        print(f"   Retention duration: {retention_str}")
        print(f"   Data file: {DATA_FILE}")
        print(f"   Press Ctrl+C to stop\n")
        
        try:
            while True:
                collect_sample(args.duration)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n\n✅ Collection stopped")
    else:
        # Single collection
        collect_sample(args.duration)


