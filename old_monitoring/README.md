# Latency Performance Monitoring

This system collects end-to-end latency data over time and generates comprehensive reports with graphs.

## Components

All monitoring scripts are located in the `monitoring/` directory:

### 1. `monitoring/collect_latency_data.py`
Collects latency statistics periodically and stores them in `monitoring/latency_data.json`.

**Usage:**

```bash
# Single collection
python monitoring/collect_latency_data.py

# Continuous collection (every 60 seconds)
python monitoring/collect_latency_data.py --continuous

# Custom collection interval (set environment variable)
LATENCY_COLLECT_INTERVAL=30 python monitoring/collect_latency_data.py --continuous
```

**What it collects:**
- P95, P99, P50 (median) latency
- Average, min, max latency
- Message count per sample
- Target compliance (P95 < 2000ms)
- Timestamp for each sample

**Data storage:**
- Stored in `monitoring/latency_data.json` (JSON format)
- **Configurable retention duration** (default: 10 minutes)
- Automatically prunes old data based on retention setting

**Configuration:**
- `LATENCY_RETENTION_DURATION`: Data retention duration (default: `10m`)
  - Format: `"10m"` (minutes), `"1h"` (hours), `"30s"` (seconds)
  - Examples:
    - `LATENCY_RETENTION_DURATION=5m` - Keep 5 minutes of data
    - `LATENCY_RETENTION_DURATION=1h` - Keep 1 hour of data (for longer tests)
    - `LATENCY_RETENTION_DURATION=30m` - Keep 30 minutes of data
- `LATENCY_COLLECT_INTERVAL`: Collection interval in seconds (default: `60`)

### 2. `monitoring/generate_latency_report.py`
Generates comprehensive reports with graphs from collected data.

**Usage:**

```bash
python monitoring/generate_latency_report.py
```

**Output:**
- Console report with statistics
- PNG file: `monitoring/latency_report_YYYYMMDD_HHMMSS.png` with 6 graphs:
  1. P95 and P99 Latency Over Time
  2. Average and Median Latency Over Time
  3. Min and Max Latency Over Time
  4. P95 Latency Distribution (Histogram)
  5. Message Count Per Sample
  6. Latency Statistics Distribution (Box Plot)

### 3. `monitoring/monitor_latency.py`
Real-time latency monitoring (single snapshot).

**Usage:**

```bash
python monitoring/monitor_latency.py
```

## Workflow

### For Quick Tests (5-10 minutes):

1. **Set retention to 10 minutes:**
   ```bash
   export LATENCY_RETENTION_DURATION=10m
   ```

2. **Start data collection:**
   ```bash
   python monitoring/collect_latency_data.py --continuous
   ```
   Leave this running during your test.

3. **Generate report:**
   ```bash
   python monitoring/generate_latency_report.py
   ```

### For Longer Tests (30+ minutes):

1. **Set retention to match test duration:**
   ```bash
   export LATENCY_RETENTION_DURATION=1h  # For 1-hour test
   # or
   export LATENCY_RETENTION_DURATION=30m  # For 30-minute test
   ```

2. **Start data collection:**
   ```bash
   python monitoring/collect_latency_data.py --continuous
   ```

3. **Generate report after test:**
   ```bash
   python monitoring/generate_latency_report.py
   ```

### For One-Time Analysis:

1. **Collect a few samples:**
   ```bash
   # Run multiple times or use continuous mode for a few minutes
   python monitoring/collect_latency_data.py
   ```

2. **Generate report:**
   ```bash
   python monitoring/generate_latency_report.py
   ```

## Report Contents

The generated report includes:

### Statistics:
- Total samples collected
- Time range covered
- Overall P95, P99, Average, Median statistics
- Min/Max values across all samples
- Target compliance percentage

### Graphs:
1. **P95/P99 Over Time** - Shows latency trends
2. **Average/Median Over Time** - Shows typical performance
3. **Min/Max Over Time** - Shows latency range
4. **P95 Distribution** - Histogram of P95 values
5. **Message Count** - Throughput over time
6. **Box Plot** - Statistical distribution comparison

## Target Compliance

The system tracks whether P95 latency stays below 2000ms:
- **Excellent**: ≥99% compliance
- **Good**: ≥95% compliance
- **Acceptable**: ≥90% compliance
- **Poor**: <90% compliance (needs attention)

## Data File Format

`monitoring/latency_data.json` contains an array of samples:

```json
[
  {
    "timestamp": "2026-01-02T14:00:00",
    "unix_timestamp": 1704204000.0,
    "count": 273,
    "min_ms": 19.5,
    "max_ms": 524.6,
    "avg_ms": 258.0,
    "median_ms": 255.3,
    "p95_ms": 477.1,
    "p99_ms": 510.0,
    "p50_ms": 255.3,
    "target_met": true
  }
]
```

## Configuration Examples

### Quick 5-minute test:
```bash
export LATENCY_RETENTION_DURATION=5m
export LATENCY_COLLECT_INTERVAL=10  # Collect every 10 seconds
python monitoring/collect_latency_data.py --continuous
```

### 30-minute load test:
```bash
export LATENCY_RETENTION_DURATION=30m
export LATENCY_COLLECT_INTERVAL=60  # Collect every minute
python monitoring/collect_latency_data.py --continuous
```

### 1-hour stress test:
```bash
export LATENCY_RETENTION_DURATION=1h
export LATENCY_COLLECT_INTERVAL=60
python monitoring/collect_latency_data.py --continuous
```

## Tips

1. **For quick tests**: Use 5-10 minute retention (default)
2. **For longer tests**: Set retention to match test duration
3. **Collection interval**: 
   - 10-30 seconds for short tests (more granular data)
   - 60 seconds for longer tests (less data, but sufficient)
4. **Generate reports** after significant test runs
5. **Compare reports** over time to track performance degradation

## Example: Setting up as a Service

```bash
# Create systemd service (Linux)
sudo nano /etc/systemd/system/latency-collector.service
```

```ini
[Unit]
Description=Latency Data Collector
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/Realtime-Datastreaming
Environment="LATENCY_RETENTION_DURATION=1h"
Environment="LATENCY_COLLECT_INTERVAL=60"
ExecStart=/path/to/venv/bin/python monitoring/collect_latency_data.py --continuous
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable latency-collector
sudo systemctl start latency-collector
```


