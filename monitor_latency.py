#!/usr/bin/env python3
"""
Measure end-to-end latency: device publish → InfluxDB write
Target: <2s at p95
"""

import os
import time
from datetime import datetime
from influxdb_client import InfluxDBClient
from dotenv import load_dotenv
import statistics

load_dotenv()

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
            # _time is when InfluxDB wrote the data (after batching)
            # _value is the publish_timestamp from device
            write_time = record.get_time().timestamp()
            publish_time = record.get_value()
            
            # End-to-end latency: time from publish to InfluxDB write
            latency_seconds = write_time - publish_time
            
            # Only include valid latencies (positive, reasonable)
            if 0 < latency_seconds < 60:  # Filter out negative or unreasonable values
                latencies.append(latency_seconds * 1000)  # Convert to ms
    
    client.close()
    
    if not latencies:
        return None
    
    # Calculate statistics
    latencies_sorted = sorted(latencies)
    n = len(latencies)
    
    return {
        'count': n,
        'min_ms': min(latencies),
        'max_ms': max(latencies),
        'avg_ms': statistics.mean(latencies),
        'median_ms': statistics.median(latencies),
        'p95_ms': latencies_sorted[int(n * 0.95)] if n > 0 else 0,
        'p99_ms': latencies_sorted[int(n * 0.99)] if n > 0 else 0,
        'target_met': latencies_sorted[int(n * 0.95)] < 2000 if n > 0 else False
    }

def print_latency_report():
    """Print latency report."""
    print("=" * 70)
    print("⏱️  END-TO-END LATENCY REPORT")
    print("=" * 70)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    stats = get_latency_stats('5m')
    
    if not stats:
        print("❌ No data found. Make sure:")
        print("   - Devices are publishing")
        print("   - Collector is running")
        print("   - Data has been written in last 5 minutes")
        print("   - Collector has been updated to store publish_timestamp")
        return
    
    print(f"📊 Sample Size: {stats['count']} messages (last 5 minutes)\n")
    print("📈 Latency Statistics:")
    print(f"   Min:    {stats['min_ms']:>8.1f} ms")
    print(f"   Avg:    {stats['avg_ms']:>8.1f} ms")
    print(f"   Median: {stats['median_ms']:>8.1f} ms")
    print(f"   P95:    {stats['p95_ms']:>8.1f} ms")
    print(f"   P99:    {stats['p99_ms']:>8.1f} ms")
    print(f"   Max:    {stats['max_ms']:>8.1f} ms\n")
    
    print("🎯 Target: P95 < 2000 ms")
    if stats['target_met']:
        print(f"   ✅ TARGET MET: P95 = {stats['p95_ms']:.1f} ms < 2000 ms")
    else:
        print(f"   ❌ TARGET MISSED: P95 = {stats['p95_ms']:.1f} ms >= 2000 ms")
        print("   → Consider:")
        print("     - Reducing batch flush interval (currently 1s)")
        print("     - Increasing batch size (currently 500)")
        print("     - Checking network latency")
        print("     - Using Telegraf collector (faster)")
    
    print("=" * 70)

if __name__ == '__main__':
    print_latency_report()

