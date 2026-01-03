#!/usr/bin/env python3
"""
Check InfluxDB performance metrics and write patterns during a specific time period
"""

import os
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient
from dotenv import load_dotenv
import statistics

# Load .env from parent directory
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

def analyze_write_patterns(start_time, end_time, description=""):
    """Analyze InfluxDB write patterns and performance."""
    client = InfluxDBClient(
        url=os.getenv('INFLUXDB_URL', 'http://localhost:8086'),
        token=os.getenv('INFLUXDB_TOKEN'),
        org=os.getenv('INFLUXDB_ORG', 'my-org')
    )
    query_api = client.query_api()
    bucket = os.getenv('INFLUXDB_BUCKET', 'vehicle-data')
    
    start_str = start_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    end_str = end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    print(f"\n{'='*70}")
    print(f"📊 InfluxDB Write Pattern Analysis: {description}")
    print(f"   Time Range: {start_time.strftime('%H:%M:%S')} to {end_time.strftime('%H:%M:%S')}")
    print(f"{'='*70}\n")
    
    # Query: Get all write timestamps grouped by second
    query = f'''
    from(bucket: "{bucket}")
      |> range(start: {start_str}, stop: {end_str})
      |> filter(fn: (r) => r["_measurement"] == "device_data")
      |> filter(fn: (r) => r["_field"] == "speed")
      |> group(columns: ["_time"])
      |> aggregateWindow(every: 1s, fn: count, createEmpty: false)
      |> sort(columns: ["_time"])
    '''
    
    result = query_api.query(query)
    
    writes_per_second = []
    timestamps = []
    
    for table in result:
        for record in table.records:
            count = record.get_value()
            writes_per_second.append(count)
            timestamps.append(record.get_time())
    
    if not writes_per_second:
        print("❌ No write data found")
        client.close()
        return None
    
    print(f"📈 Write Rate Analysis:")
    print(f"   Total Writes: {sum(writes_per_second)}")
    print(f"   Avg Writes/sec: {statistics.mean(writes_per_second):.1f}")
    print(f"   Min Writes/sec: {min(writes_per_second)}")
    print(f"   Max Writes/sec: {max(writes_per_second)}")
    print(f"   Median Writes/sec: {statistics.median(writes_per_second):.1f}")
    print(f"   P95 Writes/sec: {sorted(writes_per_second)[int(len(writes_per_second) * 0.95)]:.1f}\n")
    
    # Identify periods with unusual write patterns
    avg_writes = statistics.mean(writes_per_second)
    std_writes = statistics.stdev(writes_per_second) if len(writes_per_second) > 1 else 0
    
    high_write_periods = []
    low_write_periods = []
    
    for i, (ts, count) in enumerate(zip(timestamps, writes_per_second)):
        if count > avg_writes + 2 * std_writes:
            high_write_periods.append((ts, count))
        elif count < avg_writes - 2 * std_writes:
            low_write_periods.append((ts, count))
    
    if high_write_periods:
        print(f"⚠️  High Write Rate Periods (>2σ above average):")
        for ts, count in high_write_periods[:10]:  # Show first 10
            print(f"   {ts.strftime('%H:%M:%S')}: {count:.0f} writes/sec")
        print()
    
    if low_write_periods:
        print(f"⚠️  Low Write Rate Periods (<2σ below average):")
        for ts, count in low_write_periods[:10]:  # Show first 10
            print(f"   {ts.strftime('%H:%M:%S')}: {count:.0f} writes/sec")
        print()
    
    # Analyze write timing distribution
    query_timing = f'''
    from(bucket: "{bucket}")
      |> range(start: {start_str}, stop: {end_str})
      |> filter(fn: (r) => r["_measurement"] == "device_data")
      |> filter(fn: (r) => r["_field"] == "speed")
      |> keep(columns: ["_time"])
      |> sort(columns: ["_time"])
    '''
    
    result_timing = query_api.query(query_timing)
    write_times = []
    
    for table in result_timing:
        for record in table.records:
            write_times.append(record.get_time().timestamp())
    
    if len(write_times) > 1:
        # Calculate intervals between writes
        intervals = []
        for i in range(1, len(write_times)):
            interval = write_times[i] - write_times[i-1]
            if 0 < interval < 10:  # Filter out unreasonable intervals
                intervals.append(interval * 1000)  # Convert to ms
        
        if intervals:
            print(f"⏱️  Write Interval Analysis:")
            print(f"   Avg Interval: {statistics.mean(intervals):.2f} ms")
            print(f"   Median Interval: {statistics.median(intervals):.2f} ms")
            print(f"   Min Interval: {min(intervals):.2f} ms")
            print(f"   Max Interval: {max(intervals):.2f} ms")
            print(f"   P95 Interval: {sorted(intervals)[int(len(intervals) * 0.95)]:.2f} ms\n")
            
            # Find long gaps (potential write delays)
            long_gaps = [i for i in intervals if i > 1000]  # > 1 second
            if long_gaps:
                print(f"⚠️  Long Write Gaps (>1s): {len(long_gaps)} occurrences")
                print(f"   Avg Gap: {statistics.mean(long_gaps):.1f} ms")
                print(f"   Max Gap: {max(long_gaps):.1f} ms\n")
    
    client.close()
    
    return {
        'writes_per_second': writes_per_second,
        'avg_writes': avg_writes,
        'max_writes': max(writes_per_second),
        'high_write_periods': high_write_periods
    }

def check_influxdb_health():
    """Check InfluxDB health and connection."""
    client = InfluxDBClient(
        url=os.getenv('INFLUXDB_URL', 'http://localhost:8086'),
        token=os.getenv('INFLUXDB_TOKEN'),
        org=os.getenv('INFLUXDB_ORG', 'my-org')
    )
    
    try:
        # Ping InfluxDB
        health = client.ping()
        print(f"✅ InfluxDB Health Check: {health}")
        
        # Get version info
        try:
            version = client.version()
            print(f"   Version: {version}")
        except:
            pass
        
        client.close()
        return True
    except Exception as e:
        print(f"❌ InfluxDB Health Check Failed: {e}")
        client.close()
        return False

if __name__ == '__main__':
    import sys
    
    print("="*70)
    print("🔍 INFLUXDB PERFORMANCE ANALYSIS")
    print("="*70)
    
    # Health check
    print("\n1️⃣ InfluxDB Health Check:")
    check_influxdb_health()
    
    if len(sys.argv) >= 3:
        # Parse time
        try:
            if ' ' in sys.argv[1]:
                start_time = datetime.strptime(sys.argv[1], '%Y-%m-%d %H:%M:%S')
                end_time = datetime.strptime(sys.argv[2], '%Y-%m-%d %H:%M:%S')
            else:
                today = datetime.now().date()
                start_time = datetime.combine(today, datetime.strptime(sys.argv[1], '%H:%M:%S').time())
                end_time = datetime.combine(today, datetime.strptime(sys.argv[2], '%H:%M:%S').time())
        except:
            print("❌ Invalid time format. Use 'HH:MM:SS' or 'YYYY-MM-DD HH:MM:SS'")
            sys.exit(1)
        
        # Analyze spike period
        print("\n2️⃣ Write Pattern Analysis (Spike Period):")
        spike_data = analyze_write_patterns(start_time, end_time, "Spike Period")
        
        # Analyze baseline before
        baseline_start = start_time - timedelta(seconds=300)
        print("\n3️⃣ Write Pattern Analysis (Baseline Before):")
        baseline_data = analyze_write_patterns(baseline_start, start_time, "Baseline Before")
        
        # Compare
        if spike_data and baseline_data:
            print("\n" + "="*70)
            print("📊 COMPARISON")
            print("="*70)
            print(f"\nWrite Rate:")
            print(f"   Baseline Avg: {baseline_data['avg_writes']:.1f} writes/sec")
            print(f"   Spike Avg:    {spike_data['avg_writes']:.1f} writes/sec")
            print(f"   Change:       {spike_data['avg_writes'] - baseline_data['avg_writes']:.1f} writes/sec")
            print(f"\nPeak Write Rate:")
            print(f"   Baseline Max: {baseline_data['max_writes']:.0f} writes/sec")
            print(f"   Spike Max:    {spike_data['max_writes']:.0f} writes/sec")
            
            if spike_data['max_writes'] > baseline_data['max_writes'] * 1.5:
                print("\n⚠️  Peak write rate significantly higher during spike")
                print("   → Possible cause: Write burst overwhelmed InfluxDB")
    else:
        print("\nUsage: python check_influxdb_performance.py <start_time> <end_time>")
        print("Example: python check_influxdb_performance.py 17:23:00 17:25:00")


