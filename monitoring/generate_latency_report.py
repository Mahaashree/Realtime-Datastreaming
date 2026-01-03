#!/usr/bin/env python3
"""
Generate latency performance report with graphs showing actual trends
"""

import os
import json
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from influxdb_client import InfluxDBClient
from dotenv import load_dotenv
import statistics

# Load .env from parent directory
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

MONITORING_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(MONITORING_DIR, "latency_data.json")

def get_individual_latencies(duration='10m', sample_limit=1000):
    """Get individual message latencies from InfluxDB for trend analysis."""
    client = InfluxDBClient(
        url=os.getenv('INFLUXDB_URL', 'http://localhost:8086'),
        token=os.getenv('INFLUXDB_TOKEN'),
        org=os.getenv('INFLUXDB_ORG', 'my-org')
    )
    query_api = client.query_api()
    bucket = os.getenv('INFLUXDB_BUCKET', 'vehicle-data')
    
    # Query individual message latencies
    query = f'''
    from(bucket: "{bucket}")
      |> range(start: -{duration})
      |> filter(fn: (r) => r["_measurement"] == "device_data")
      |> filter(fn: (r) => r["_field"] == "publish_timestamp")
      |> keep(columns: ["_time", "_value", "device_id"])
      |> sort(columns: ["_time"])
      |> limit(n: {sample_limit})
    '''
    
    result = query_api.query(query)
    
    latencies = []
    timestamps = []
    
    for table in result:
        for record in table.records:
            write_time = record.get_time().timestamp()
            publish_time = record.get_value()
            latency_seconds = write_time - publish_time
            
            if 0 < latency_seconds < 60:
                latencies.append(latency_seconds * 1000)  # Convert to ms
                timestamps.append(record.get_time())
    
    client.close()
    
    return timestamps, latencies

def load_data():
    """Load latency data from file."""
    if not os.path.exists(DATA_FILE):
        print(f"⚠️  Data file not found: {DATA_FILE}")
        print("   Will query InfluxDB directly for trends...")
        return None
    
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️  Error loading data: {e}")
        return None

def generate_report(data):
    """Generate comprehensive latency report."""
    print("=" * 70)
    print("📊 LATENCY PERFORMANCE REPORT")
    print("=" * 70)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Get individual message latencies for trend analysis
    print("📈 Querying InfluxDB for individual message latencies...")
    timestamps, latencies = get_individual_latencies('10m', sample_limit=2000)
    
    if not latencies:
        print("❌ No latency data found in InfluxDB")
        return
    
    print(f"   ✅ Found {len(latencies)} messages\n")
    
    # Calculate statistics
    latencies_sorted = sorted(latencies)
    n = len(latencies)
    
    print(f"📊 Statistics:")
    print(f"   Total Messages: {n}")
    print(f"   Time Range: {timestamps[0].strftime('%H:%M:%S')} to {timestamps[-1].strftime('%H:%M:%S')}")
    print(f"   Duration: {(timestamps[-1] - timestamps[0]).total_seconds():.1f} seconds\n")
    
    print(f"📈 Latency Statistics:")
    print(f"   Min:    {min(latencies):>8.1f} ms")
    print(f"   Avg:    {statistics.mean(latencies):>8.1f} ms")
    print(f"   Median: {statistics.median(latencies):>8.1f} ms")
    print(f"   P95:    {latencies_sorted[int(n * 0.95)]:>8.1f} ms")
    print(f"   P99:    {latencies_sorted[int(n * 0.99)]:>8.1f} ms")
    print(f"   Max:    {max(latencies):>8.1f} ms\n")
    
    # Target compliance
    p95 = latencies_sorted[int(n * 0.95)]
    target_met = p95 < 2000
    print(f"🎯 Target: P95 < 2000 ms")
    if target_met:
        print(f"   ✅ TARGET MET: P95 = {p95:.1f} ms < 2000 ms")
    else:
        print(f"   ❌ TARGET MISSED: P95 = {p95:.1f} ms >= 2000 ms")
    
    print("\n" + "=" * 70)
    
    # Generate graphs
    print("\n📈 Generating trend graphs...")
    generate_trend_graphs(timestamps, latencies, data)
    
    print("✅ Report complete!")
    print(f"   Graphs saved to: monitoring/latency_report_*.png")

def generate_trend_graphs(timestamps, latencies, aggregated_data):
    """Generate useful trend graphs."""
    
    # Create figure with subplots
    fig = plt.figure(figsize=(16, 10))
    
    # Convert to numpy arrays for easier manipulation
    latencies_arr = np.array(latencies)
    timestamps_arr = np.array(timestamps)
    
    # 1. Individual Message Latency Over Time (with moving average)
    ax1 = plt.subplot(2, 2, 1)
    
    # Sample down if too many points for performance
    if len(latencies) > 1000:
        step = len(latencies) // 1000
        sample_indices = np.arange(0, len(latencies), step)
        ax1.scatter(timestamps_arr[sample_indices], latencies_arr[sample_indices], 
                   alpha=0.3, s=1, color='#3b82f6', label='Individual messages')
    else:
        ax1.scatter(timestamps_arr, latencies_arr, alpha=0.3, s=1, color='#3b82f6', label='Individual messages')
    
    # Moving average (window of 50 messages or 10% of data, whichever is smaller)
    window = min(50, max(10, len(latencies) // 10))
    if len(latencies) > window:
        moving_avg = np.convolve(latencies_arr, np.ones(window)/window, mode='valid')
        moving_avg_times = timestamps_arr[window-1:]
        ax1.plot(moving_avg_times, moving_avg, color='#ef4444', linewidth=2, label=f'Moving Avg ({window} msgs)')
    
    ax1.axhline(y=2000, color='green', linestyle=':', linewidth=2, label='Target (2000ms)')
    ax1.set_title('Message Latency Over Time (with Moving Average)', fontsize=14, fontweight='bold')
    ax1.set_xlabel('Time')
    ax1.set_ylabel('Latency (ms)')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
    
    # 2. Latency Distribution (Histogram)
    ax2 = plt.subplot(2, 2, 2)
    ax2.hist(latencies, bins=50, color='#8b5cf6', alpha=0.7, edgecolor='black')
    ax2.axvline(x=2000, color='green', linestyle=':', linewidth=2, label='Target (2000ms)')
    ax2.axvline(x=np.mean(latencies), color='blue', linestyle='--', linewidth=2, 
                label=f'Mean ({np.mean(latencies):.1f}ms)')
    ax2.axvline(x=np.percentile(latencies, 95), color='red', linestyle='--', linewidth=2,
                label=f'P95 ({np.percentile(latencies, 95):.1f}ms)')
    ax2.set_title('Latency Distribution', fontsize=14, fontweight='bold')
    ax2.set_xlabel('Latency (ms)')
    ax2.set_ylabel('Frequency')
    ax2.legend()
    ax2.grid(True, alpha=0.3, axis='y')
    
    # 3. Percentile Trends Over Time (P50, P95, P99)
    ax3 = plt.subplot(2, 2, 3)
    
    # Calculate percentiles in rolling windows
    window_size = min(100, len(latencies) // 10)
    if len(latencies) > window_size:
        p50_trend = []
        p95_trend = []
        p99_trend = []
        window_times = []
        
        for i in range(window_size, len(latencies), window_size // 2):
            window_latencies = latencies[i-window_size:i]
            p50_trend.append(np.percentile(window_latencies, 50))
            p95_trend.append(np.percentile(window_latencies, 95))
            p99_trend.append(np.percentile(window_latencies, 99))
            window_times.append(timestamps[i])
        
        ax3.plot(window_times, p50_trend, label='P50 (Median)', color='#3b82f6', linewidth=2)
        ax3.plot(window_times, p95_trend, label='P95', color='#ef4444', linewidth=2)
        ax3.plot(window_times, p99_trend, label='P99', color='#dc2626', linewidth=2, linestyle='--')
        ax3.axhline(y=2000, color='green', linestyle=':', linewidth=2, label='Target (2000ms)')
        ax3.set_title('Percentile Trends Over Time', fontsize=14, fontweight='bold')
        ax3.set_xlabel('Time')
        ax3.set_ylabel('Latency (ms)')
        ax3.legend()
        ax3.grid(True, alpha=0.3)
        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45)
    else:
        ax3.text(0.5, 0.5, 'Not enough data for trend analysis', 
                ha='center', va='center', transform=ax3.transAxes)
        ax3.set_title('Percentile Trends Over Time', fontsize=14, fontweight='bold')
    
    # 4. Aggregated Statistics Over Time (if we have aggregated data)
    ax4 = plt.subplot(2, 2, 4)
    
    if aggregated_data and len(aggregated_data) > 1:
        agg_timestamps = [datetime.fromisoformat(entry['timestamp']) for entry in aggregated_data]
        agg_p95 = [entry['p95_ms'] for entry in aggregated_data]
        agg_avg = [entry['avg_ms'] for entry in aggregated_data]
        
        ax4.plot(agg_timestamps, agg_p95, label='P95 (Aggregated)', color='#ef4444', linewidth=2, marker='o', markersize=4)
        ax4.plot(agg_timestamps, agg_avg, label='Average (Aggregated)', color='#3b82f6', linewidth=2, marker='s', markersize=4)
        ax4.axhline(y=2000, color='green', linestyle=':', linewidth=2, label='Target (2000ms)')
        ax4.set_title('Aggregated Statistics Over Time', fontsize=14, fontweight='bold')
        ax4.set_xlabel('Time')
        ax4.set_ylabel('Latency (ms)')
        ax4.legend()
        ax4.grid(True, alpha=0.3)
        ax4.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        plt.setp(ax4.xaxis.get_majorticklabels(), rotation=45)
    else:
        # Show box plot of latency statistics
        box_data = [latencies]
        box_labels = ['All Messages']
        bp = ax4.boxplot(box_data, labels=box_labels, patch_artist=True, 
                        showmeans=True, meanline=True)
        bp['boxes'][0].set_facecolor('#8b5cf6')
        bp['boxes'][0].set_alpha(0.7)
        ax4.axhline(y=2000, color='green', linestyle=':', linewidth=2, label='Target (2000ms)')
        ax4.set_title('Latency Distribution (Box Plot)', fontsize=14, fontweight='bold')
        ax4.set_ylabel('Latency (ms)')
        ax4.legend()
        ax4.grid(True, alpha=0.3, axis='y')
    
    plt.tight_layout()
    
    # Save figure in monitoring directory
    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = os.path.join(MONITORING_DIR, f"latency_report_{timestamp_str}.png")
    plt.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"   ✅ Saved: {filename}")
    
    plt.close()

if __name__ == '__main__':
    data = load_data()
    generate_report(data)
