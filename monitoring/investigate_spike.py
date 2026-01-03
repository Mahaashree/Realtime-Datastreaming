#!/usr/bin/env python3
"""
Investigate latency spikes by analyzing InfluxDB data during specific time periods
"""

import os
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient
from dotenv import load_dotenv
import statistics
import numpy as np

# Load .env from parent directory
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

def analyze_time_period(start_time, end_time, description=""):
    """Analyze latency data for a specific time period."""
    client = InfluxDBClient(
        url=os.getenv('INFLUXDB_URL', 'http://localhost:8086'),
        token=os.getenv('INFLUXDB_TOKEN'),
        org=os.getenv('INFLUXDB_ORG', 'my-org')
    )
    query_api = client.query_api()
    bucket = os.getenv('INFLUXDB_BUCKET', 'vehicle-data')
    
    # Convert datetime to RFC3339 format for InfluxDB
    start_str = start_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    end_str = end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    print(f"\n{'='*70}")
    print(f"📊 Analyzing: {description}")
    print(f"   Time Range: {start_time.strftime('%H:%M:%S')} to {end_time.strftime('%H:%M:%S')}")
    print(f"   Duration: {(end_time - start_time).total_seconds():.1f} seconds")
    print(f"{'='*70}\n")
    
    # Query 1: Get all message latencies in this period
    query_latencies = f'''
    from(bucket: "{bucket}")
      |> range(start: {start_str}, stop: {end_str})
      |> filter(fn: (r) => r["_measurement"] == "device_data")
      |> filter(fn: (r) => r["_field"] == "publish_timestamp")
      |> keep(columns: ["_time", "_value", "device_id"])
      |> sort(columns: ["_time"])
    '''
    
    result = query_api.query(query_latencies)
    
    latencies = []
    timestamps = []
    write_times = []
    publish_times = []
    device_ids = []
    
    for table in result:
        for record in table.records:
            write_time = record.get_time().timestamp()
            publish_time = record.get_value()
            latency_seconds = write_time - publish_time
            
            if 0 < latency_seconds < 60:
                latencies.append(latency_seconds * 1000)  # Convert to ms
                timestamps.append(record.get_time())
                write_times.append(write_time)
                publish_times.append(publish_time)
                device_ids.append(record.values.get('device_id'))
    
    if not latencies:
        print("❌ No data found in this time period")
        client.close()
        return None
    
    # Query 2: Get collector receive times
    query_collector = f'''
    from(bucket: "{bucket}")
      |> range(start: {start_str}, stop: {end_str})
      |> filter(fn: (r) => r["_measurement"] == "device_data")
      |> filter(fn: (r) => r["_field"] == "collector_receive_time")
      |> keep(columns: ["_time", "_value"])
      |> sort(columns: ["_time"])
    '''
    
    result_collector = query_api.query(query_collector)
    collector_receive_times = {}
    
    for table in result_collector:
        for record in table.records:
            write_time = record.get_time().timestamp()
            collector_time = record.get_value()
            collector_receive_times[write_time] = collector_time
    
    # Calculate breakdown
    mqtt_latencies = []  # publish -> collector receive
    batch_delays = []    # collector receive -> InfluxDB write
    
    for i, write_time in enumerate(write_times):
        if write_time in collector_receive_times:
            collector_time = collector_receive_times[write_time]
            publish_time = publish_times[i]
            
            mqtt_lat = (collector_time - publish_time) * 1000
            batch_delay = (write_time - collector_time) * 1000
            
            if 0 < mqtt_lat < 1000 and 0 < batch_delay < 10000:
                mqtt_latencies.append(mqtt_lat)
                batch_delays.append(batch_delay)
    
    # Statistics
    print(f"📈 Message Statistics:")
    print(f"   Total Messages: {len(latencies)}")
    print(f"   Messages/second: {len(latencies) / (end_time - start_time).total_seconds():.1f}")
    print(f"   Unique Devices: {len(set(device_ids))}\n")
    
    print(f"⏱️  End-to-End Latency:")
    print(f"   Min:    {min(latencies):>8.1f} ms")
    print(f"   Avg:    {statistics.mean(latencies):>8.1f} ms")
    print(f"   Median: {statistics.median(latencies):>8.1f} ms")
    print(f"   P95:    {np.percentile(latencies, 95):>8.1f} ms")
    print(f"   P99:    {np.percentile(latencies, 99):>8.1f} ms")
    print(f"   Max:    {max(latencies):>8.1f} ms\n")
    
    if mqtt_latencies:
        print(f"📡 MQTT Latency (Publish → Collector Receive):")
        print(f"   Avg:    {statistics.mean(mqtt_latencies):>8.1f} ms")
        print(f"   Median: {statistics.median(mqtt_latencies):>8.1f} ms")
        print(f"   P95:    {np.percentile(mqtt_latencies, 95):>8.1f} ms")
        print(f"   Max:    {max(mqtt_latencies):>8.1f} ms\n")
        
        print(f"💾 Batch Delay (Collector Receive → InfluxDB Write):")
        print(f"   Avg:    {statistics.mean(batch_delays):>8.1f} ms")
        print(f"   Median: {statistics.median(batch_delays):>8.1f} ms")
        print(f"   P95:    {np.percentile(batch_delays, 95):>8.1f} ms")
        print(f"   Max:    {max(batch_delays):>8.1f} ms\n")
    
    # Analyze batch flush patterns
    if write_times:
        write_times_sorted = sorted(write_times)
        batch_groups = []
        current_batch = [write_times_sorted[0]]
        
        for i in range(1, len(write_times_sorted)):
            # If writes are within 100ms, consider them same batch
            if write_times_sorted[i] - write_times_sorted[i-1] < 0.1:
                current_batch.append(write_times_sorted[i])
            else:
                batch_groups.append(current_batch)
                current_batch = [write_times_sorted[i]]
        batch_groups.append(current_batch)
        
        batch_sizes = [len(batch) for batch in batch_groups]
        batch_intervals = []
        for i in range(1, len(batch_groups)):
            interval = batch_groups[i][0] - batch_groups[i-1][-1]
            batch_intervals.append(interval)
        
        print(f"📦 Batch Analysis:")
        print(f"   Total Batches: {len(batch_groups)}")
        print(f"   Avg Batch Size: {statistics.mean(batch_sizes):.1f} messages")
        print(f"   Max Batch Size: {max(batch_sizes)} messages")
        if batch_intervals:
            print(f"   Avg Batch Interval: {statistics.mean(batch_intervals)*1000:.1f} ms")
            print(f"   Max Batch Interval: {max(batch_intervals)*1000:.1f} ms")
        print()
    
    # Identify high latency messages
    high_latency_threshold = 2000  # ms
    high_latency_count = sum(1 for l in latencies if l > high_latency_threshold)
    if high_latency_count > 0:
        print(f"⚠️  High Latency Messages (>2000ms):")
        print(f"   Count: {high_latency_count} ({high_latency_count/len(latencies)*100:.1f}%)")
        high_latency_values = [l for l in latencies if l > high_latency_threshold]
        print(f"   Avg: {statistics.mean(high_latency_values):.1f} ms")
        print(f"   Max: {max(high_latency_values):.1f} ms\n")
    
    client.close()
    
    return {
        'latencies': latencies,
        'mqtt_latencies': mqtt_latencies,
        'batch_delays': batch_delays,
        'batch_sizes': batch_sizes if 'batch_sizes' in locals() else [],
        'message_rate': len(latencies) / (end_time - start_time).total_seconds()
    }

def investigate_spike(spike_start_str, spike_end_str, baseline_before=300, baseline_after=300):
    """Investigate a latency spike by comparing it to baseline periods."""
    
    # Parse spike time (format: "17:23:00" or "2026-01-02 17:23:00")
    try:
        if ' ' in spike_start_str:
            spike_start = datetime.strptime(spike_start_str, '%Y-%m-%d %H:%M:%S')
            spike_end = datetime.strptime(spike_end_str, '%Y-%m-%d %H:%M:%S')
        else:
            # Assume today's date
            today = datetime.now().date()
            spike_start = datetime.combine(today, datetime.strptime(spike_start_str, '%H:%M:%S').time())
            spike_end = datetime.combine(today, datetime.strptime(spike_end_str, '%H:%M:%S').time())
    except:
        print("❌ Invalid time format. Use 'HH:MM:SS' or 'YYYY-MM-DD HH:MM:SS'")
        return
    
    # Calculate baseline periods
    baseline_before_start = spike_start - timedelta(seconds=baseline_before)
    baseline_after_end = spike_end + timedelta(seconds=baseline_after)
    
    print("="*70)
    print("🔍 LATENCY SPIKE INVESTIGATION")
    print("="*70)
    print(f"Spike Period: {spike_start.strftime('%H:%M:%S')} to {spike_end.strftime('%H:%M:%S')}")
    print()
    
    # Analyze baseline before
    baseline_before_data = analyze_time_period(
        baseline_before_start, 
        spike_start, 
        "Baseline (Before Spike)"
    )
    
    # Analyze spike period
    spike_data = analyze_time_period(
        spike_start, 
        spike_end, 
        "SPIKE PERIOD"
    )
    
    # Analyze baseline after
    baseline_after_data = analyze_time_period(
        spike_end, 
        baseline_after_end, 
        "Baseline (After Spike)"
    )
    
    # Compare
    if spike_data and baseline_before_data:
        print("\n" + "="*70)
        print("📊 COMPARISON: Spike vs Baseline")
        print("="*70)
        
        spike_p95 = np.percentile(spike_data['latencies'], 95)
        baseline_p95 = np.percentile(baseline_before_data['latencies'], 95)
        
        print(f"\nP95 Latency:")
        print(f"   Baseline: {baseline_p95:.1f} ms")
        print(f"   Spike:    {spike_p95:.1f} ms")
        print(f"   Increase: {spike_p95 - baseline_p95:.1f} ms ({(spike_p95/baseline_p95 - 1)*100:.1f}%)\n")
        
        print(f"Message Rate:")
        print(f"   Baseline: {baseline_before_data['message_rate']:.1f} msg/s")
        print(f"   Spike:    {spike_data['message_rate']:.1f} msg/s")
        print(f"   Change:   {spike_data['message_rate'] - baseline_before_data['message_rate']:.1f} msg/s\n")
        
        if spike_data['batch_delays'] and baseline_before_data['batch_delays']:
            spike_batch_p95 = np.percentile(spike_data['batch_delays'], 95)
            baseline_batch_p95 = np.percentile(baseline_before_data['batch_delays'], 95)
            
            print(f"Batch Delay (P95):")
            print(f"   Baseline: {baseline_batch_p95:.1f} ms")
            print(f"   Spike:    {spike_batch_p95:.1f} ms")
            print(f"   Increase: {spike_batch_p95 - baseline_batch_p95:.1f} ms\n")
        
        # Root cause analysis
        print("🔍 ROOT CAUSE ANALYSIS:")
        if spike_data['message_rate'] > baseline_before_data['message_rate'] * 1.2:
            print("   ⚠️  Message rate increased significantly during spike")
            print("      → Possible cause: Sudden burst of messages")
        if spike_data['batch_delays']:
            spike_batch_avg = statistics.mean(spike_data['batch_delays'])
            baseline_batch_avg = statistics.mean(baseline_before_data['batch_delays'])
            if spike_batch_avg > baseline_batch_avg * 1.5:
                print("   ⚠️  Batch delays increased significantly")
                print("      → Possible cause: InfluxDB write bottleneck or batch flush delay")
        if spike_data['mqtt_latencies']:
            spike_mqtt_avg = statistics.mean(spike_data['mqtt_latencies'])
            baseline_mqtt_avg = statistics.mean(baseline_before_data['mqtt_latencies'])
            if spike_mqtt_avg > baseline_mqtt_avg * 2:
                print("   ⚠️  MQTT latency increased significantly")
                print("      → Possible cause: Network congestion or MQTT broker overload")

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) >= 3:
        spike_start = sys.argv[1]
        spike_end = sys.argv[2]
        investigate_spike(spike_start, spike_end)
    else:
        print("Usage: python investigate_spike.py <spike_start> <spike_end>")
        print("Example: python investigate_spike.py 17:23:00 17:25:00")
        print("Example: python investigate_spike.py '2026-01-02 17:23:00' '2026-01-02 17:25:00'")
        print("\nOr analyze a specific time period:")
        print("Example: python investigate_spike.py 17:20:00 17:30:00")


