#!/usr/bin/env python3
"""
Generate latency performance report with graphs showing actual trends
"""

import os
import json
import argparse
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

def load_data():
    """Load latency data from file."""
    if not os.path.exists(DATA_FILE):
        print(f"⚠️  Data file not found: {DATA_FILE}")
        print("   Will query InfluxDB directly for trends...")
        return None
    
    try:
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            if data:
                print(f"✅ Loaded {len(data)} collected data sample(s) from {DATA_FILE}")
            return data
    except Exception as e:
        print(f"⚠️  Error loading data: {e}")
        return None

def generate_report(data, duration=None, sample_limit=2000, use_collected_only=False):
    """Generate comprehensive latency report.
    
    Args:
        data: Collected aggregated data from JSON file
        duration: Time window to query from InfluxDB (e.g., '16h', '10m')
        sample_limit: Maximum number of samples to query
        use_collected_only: If True, only use collected data, don't query InfluxDB
    """
    print("=" * 70)
    print("📊 LATENCY PERFORMANCE REPORT")
    print("=" * 70)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # If we have collected data, show it first
    if data and len(data) > 0:
        print("📊 Using collected data from latency_data.json:")
        latest_sample = data[-1]  # Most recent sample
        print(f"   Sample timestamp: {latest_sample.get('timestamp', 'N/A')}")
        print(f"   Total messages: {latest_sample.get('count', 0):,}")
        print(f"   P95 latency: {latest_sample.get('p95_ms', 0):.1f} ms")
        print(f"   Average latency: {latest_sample.get('avg_ms', 0):.1f} ms")
        print(f"   Target met: {'✅' if latest_sample.get('target_met', False) else '❌'}\n")
    
    # Determine duration for InfluxDB query
    if duration is None:
        # If we have collected data, try to infer duration or use a reasonable default
        if data and len(data) > 0:
            # Use a longer default window to match collected data
            duration = '16h'  # Default to 16h to match typical collection windows
            print(f"ℹ️  No duration specified, using {duration} to match collected data window")
        else:
            duration = '10m'  # Default fallback
    
    timestamps = None
    latencies = None
    
    if not use_collected_only:
        # Get individual message latencies for trend analysis
        print(f"📈 Querying InfluxDB for individual message latencies (window: {duration})...")
        timestamps, latencies = get_individual_latencies(duration, sample_limit=sample_limit)
        
        if not latencies:
            print("   ⚠️  No individual message data found in InfluxDB")
            if data and len(data) > 0:
                print("   → Will use collected aggregated data for report\n")
            else:
                print("   ❌ Cannot generate report: no data available")
                return
        else:
            print(f"   ✅ Found {len(latencies)} individual messages\n")
    
    # Display statistics
    if latencies:
        # Use individual message data for detailed statistics
        latencies_sorted = sorted(latencies)
        n = len(latencies)
        
        print(f"📊 Detailed Statistics (from InfluxDB):")
        print(f"   Total Messages: {n:,}")
        if timestamps:
            print(f"   Time Range: {timestamps[0].strftime('%Y-%m-%d %H:%M:%S')} to {timestamps[-1].strftime('%Y-%m-%d %H:%M:%S')}")
            duration_seconds = (timestamps[-1] - timestamps[0]).total_seconds()
            if duration_seconds > 3600:
                print(f"   Duration: {duration_seconds/3600:.1f} hours")
            elif duration_seconds > 60:
                print(f"   Duration: {duration_seconds/60:.1f} minutes")
            else:
                print(f"   Duration: {duration_seconds:.1f} seconds")
        print()
        
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
    elif data and len(data) > 0:
        # Use collected aggregated data
        latest_sample = data[-1]
        print(f"📊 Statistics (from collected data):")
        print(f"   Total Messages: {latest_sample.get('count', 0):,}")
        print(f"   Min:    {latest_sample.get('min_ms', 0):>8.1f} ms")
        print(f"   Avg:    {latest_sample.get('avg_ms', 0):>8.1f} ms")
        print(f"   Median: {latest_sample.get('median_ms', 0):>8.1f} ms")
        print(f"   P50:    {latest_sample.get('p50_ms', 0):>8.1f} ms")
        print(f"   P95:    {latest_sample.get('p95_ms', 0):>8.1f} ms")
        print(f"   P99:    {latest_sample.get('p99_ms', 0):>8.1f} ms")
        print(f"   Max:    {latest_sample.get('max_ms', 0):>8.1f} ms\n")
        
        p95 = latest_sample.get('p95_ms', 0)
        target_met = latest_sample.get('target_met', False)
        print(f"🎯 Target: P95 < 2000 ms")
        if target_met:
            print(f"   ✅ TARGET MET: P95 = {p95:.1f} ms < 2000 ms")
        else:
            print(f"   ❌ TARGET MISSED: P95 = {p95:.1f} ms >= 2000 ms")
    else:
        print("❌ No data available for report")
        return
    
    print("\n" + "=" * 70)
    
    # Generate graphs
    if latencies and timestamps:
        print("\n📈 Generating trend graphs from individual message data...")
        generate_trend_graphs(timestamps, latencies, data)
    elif data and len(data) > 0:
        print("\n📈 Generating graphs from collected aggregated data...")
        # Generate simplified graphs from aggregated data
        generate_aggregated_graphs(data)
    else:
        print("\n⚠️  Cannot generate graphs: no data available")
        return
    
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

def generate_aggregated_graphs(aggregated_data):
    """Generate graphs from aggregated data when individual message data is not available."""
    
    # Create figure with subplots
    fig = plt.figure(figsize=(16, 10))
    
    if len(aggregated_data) == 0:
        return
    
    # Convert timestamps
    agg_timestamps = [datetime.fromisoformat(entry['timestamp']) for entry in aggregated_data]
    
    # 1. Aggregated Statistics Over Time
    ax1 = plt.subplot(2, 2, 1)
    agg_p95 = [entry['p95_ms'] for entry in aggregated_data]
    agg_avg = [entry['avg_ms'] for entry in aggregated_data]
    agg_p50 = [entry.get('p50_ms', entry.get('median_ms', 0)) for entry in aggregated_data]
    
    ax1.plot(agg_timestamps, agg_p95, label='P95', color='#ef4444', linewidth=2, marker='o', markersize=6)
    ax1.plot(agg_timestamps, agg_avg, label='Average', color='#3b82f6', linewidth=2, marker='s', markersize=6)
    ax1.plot(agg_timestamps, agg_p50, label='P50 (Median)', color='#10b981', linewidth=2, marker='^', markersize=6)
    ax1.axhline(y=2000, color='green', linestyle=':', linewidth=2, label='Target (2000ms)')
    ax1.set_title('Latency Statistics Over Time (Aggregated)', fontsize=14, fontweight='bold')
    ax1.set_xlabel('Time')
    ax1.set_ylabel('Latency (ms)')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M:%S'))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
    
    # 2. Message Count Over Time
    ax2 = plt.subplot(2, 2, 2)
    message_counts = [entry['count'] for entry in aggregated_data]
    ax2.plot(agg_timestamps, message_counts, color='#8b5cf6', linewidth=2, marker='o', markersize=6)
    ax2.set_title('Message Count Over Time', fontsize=14, fontweight='bold')
    ax2.set_xlabel('Time')
    ax2.set_ylabel('Message Count')
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M:%S'))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45)
    
    # 3. Percentile Comparison (Bar chart for latest sample)
    ax3 = plt.subplot(2, 2, 3)
    latest = aggregated_data[-1]
    percentiles = ['Min', 'P50', 'Avg', 'P95', 'P99', 'Max']
    values = [
        latest.get('min_ms', 0),
        latest.get('p50_ms', latest.get('median_ms', 0)),
        latest.get('avg_ms', 0),
        latest.get('p95_ms', 0),
        latest.get('p99_ms', 0),
        latest.get('max_ms', 0)
    ]
    colors = ['#10b981', '#3b82f6', '#3b82f6', '#ef4444', '#dc2626', '#991b1b']
    bars = ax3.bar(percentiles, values, color=colors, alpha=0.7, edgecolor='black')
    ax3.axhline(y=2000, color='green', linestyle=':', linewidth=2, label='Target (2000ms)')
    ax3.set_title('Latency Percentiles (Latest Sample)', fontsize=14, fontweight='bold')
    ax3.set_ylabel('Latency (ms)')
    ax3.legend()
    ax3.grid(True, alpha=0.3, axis='y')
    # Add value labels on bars
    for bar, value in zip(bars, values):
        height = bar.get_height()
        ax3.text(bar.get_x() + bar.get_width()/2., height,
                f'{value:.1f}',
                ha='center', va='bottom', fontsize=9)
    
    # 4. Target Compliance
    ax4 = plt.subplot(2, 2, 4)
    target_met = [entry.get('target_met', False) for entry in aggregated_data]
    target_colors = ['#ef4444' if not met else '#10b981' for met in target_met]
    ax4.bar(agg_timestamps, [1 if met else 0 for met in target_met], 
           color=target_colors, alpha=0.7, edgecolor='black', width=0.8)
    ax4.axhline(y=0.5, color='gray', linestyle='--', linewidth=1, alpha=0.5)
    ax4.set_title('Target Compliance (P95 < 2000ms)', fontsize=14, fontweight='bold')
    ax4.set_xlabel('Time')
    ax4.set_ylabel('Target Met')
    ax4.set_ylim(-0.1, 1.1)
    ax4.set_yticks([0, 1])
    ax4.set_yticklabels(['Missed', 'Met'])
    ax4.grid(True, alpha=0.3, axis='y')
    ax4.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M:%S'))
    plt.setp(ax4.xaxis.get_majorticklabels(), rotation=45)
    
    plt.tight_layout()
    
    # Save figure
    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = os.path.join(MONITORING_DIR, f"latency_report_{timestamp_str}.png")
    plt.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"   ✅ Saved: {filename}")
    
    plt.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate latency performance report with graphs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Generate report using collected data (defaults to 16h window for InfluxDB if needed)
  python monitoring/generate_latency_report.py
  
  # Generate report with specific InfluxDB query window
  python monitoring/generate_latency_report.py --duration 16h
  
  # Use only collected data, don't query InfluxDB
  python monitoring/generate_latency_report.py --use-collected-only
  
  # Query InfluxDB with custom sample limit
  python monitoring/generate_latency_report.py --duration 1h --sample-limit 5000

Duration format: Use 's' for seconds, 'm' for minutes, 'h' for hours
  Examples: '30s', '10m', '1h', '16h', '24h'
        '''
    )
    parser.add_argument(
        '--duration', '-d',
        type=str,
        default=None,
        help='Time window to query from InfluxDB (default: 16h if collected data exists, else 10m). Format: 30s, 10m, 1h, etc.'
    )
    parser.add_argument(
        '--sample-limit', '-s',
        type=int,
        default=2000,
        help='Maximum number of samples to query from InfluxDB (default: 2000)'
    )
    parser.add_argument(
        '--use-collected-only',
        action='store_true',
        help='Use only collected data from JSON file, do not query InfluxDB'
    )
    
    args = parser.parse_args()
    
    # Validate duration format if provided
    if args.duration:
        try:
            parse_duration(args.duration)
        except (ValueError, AttributeError):
            print(f"❌ Invalid duration format: {args.duration}")
            print("   Use format like: 30s, 10m, 1h, 24h")
            exit(1)
    
    data = load_data()
    generate_report(data, duration=args.duration, sample_limit=args.sample_limit, 
                   use_collected_only=args.use_collected_only)
