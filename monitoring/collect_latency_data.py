#!/usr/bin/env python3
"""
Collect latency performance data over time and store it for reporting
"""

import os
import json
import time
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

def collect_sample():
    """Collect a single latency sample."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Collecting latency data...")
    
    stats = get_latency_stats('5m')
    
    if not stats:
        print("   ⚠️  No data available")
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
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == '--continuous':
        # Continuous collection mode
        interval = int(os.getenv('LATENCY_COLLECT_INTERVAL', '60'))  # Default 60 seconds
        retention_str = RETENTION_DURATION
        print(f"🔄 Starting continuous latency data collection")
        print(f"   Collection interval: {interval}s")
        print(f"   Retention duration: {retention_str}")
        print(f"   Data file: {DATA_FILE}")
        print(f"   Press Ctrl+C to stop\n")
        
        try:
            while True:
                collect_sample()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n\n✅ Collection stopped")
    else:
        # Single collection
        collect_sample()


