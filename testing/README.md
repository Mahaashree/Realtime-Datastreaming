# Network Failure Testing

Comprehensive testing suite for validating offline queue functionality and network partition resilience.

## Overview

This testing suite simulates network partitions and validates:
- ✅ Offline queue storage (SQLite-based)
- ✅ Queue flush performance after reconnection
- ✅ Queue limit handling (10,000 messages per device)
- ✅ Message delivery reliability

## Test Scenarios

### Standard Scenarios

1. **20% Devices - 5 minutes**: Quick validation test
2. **30% Devices - 10 minutes**: Medium duration test
3. **50% Devices - 15 minutes**: High partition test
4. **20% Devices - 30 minutes**: Extended duration
5. **50% Devices - 30 minutes**: Stress test

### Custom Scenarios

Run interactive custom scenarios with specific parameters.

## Usage

### Prerequisites

1. **Start MQTT Broker and InfluxDB**:
   ```bash
   cd docker
   docker-compose up -d
   ```

2. **Start Collector** (in another terminal):
   ```bash
   python collector/mqtt_collector.py
   ```

### Run All Test Scenarios

```bash
# Run all standard scenarios with 50 devices
python testing/test_network_failures.py

# Run with custom number of devices
python testing/test_network_failures.py --devices 100
```

### Run Specific Scenario

```bash
# Run a specific scenario
python testing/test_network_failures.py --scenario "20% Devices - 5 minutes"
```

### Run Custom Scenario

```bash
# Interactive custom scenario
python testing/test_network_failures.py --custom
```

You'll be prompted for:
- Disconnect percentage (20-50%)
- Duration in minutes (5-30)

## Test Process

For each scenario, the test:

1. **Starts all devices** (connects to MQTT broker)
2. **Waits for stabilization** (10 seconds)
3. **Disconnects specified percentage** of devices (simulates network partition)
4. **Monitors queue growth** during disconnection
5. **Reconnects devices** after specified duration
6. **Measures flush time** (time to empty queues)
7. **Validates results** against targets

## Metrics Collected

### Queue Metrics
- Initial queue size
- Final queue size (before reconnect)
- Messages queued during disconnect
- Max queue per device
- Queue growth rate (messages/second)
- Queue history (time-series data)

### Flush Metrics
- Flush time (seconds)
- Messages flushed
- Flush rate (messages/second)
- Target compliance (<30s for reasonable backlog)
- All messages flushed (boolean)

### Queue Limits
- Devices at queue limit (10,000 messages)
- Device IDs at limit
- Max queue size reached

## Performance Targets

### Flush Time Targets

| Message Count | Target | Category |
|---------------|--------|----------|
| < 200 | < 30s | ✅ Small backlog |
| 200-500 | < 60s | ⚠️ Medium backlog |
| 500-1000 | < 120s | ⚠️ Large backlog |
| > 1000 | < 180s | ⚠️ Very large backlog |

**Note**: Flush time depends on:
- Number of messages queued
- MQTT broker throughput
- Network latency
- Device publish rate (~5 msg/s per device)

### Queue Limit

- **Max queue size**: 10,000 messages per device
- **Behavior**: When limit reached, oldest messages are dropped (FIFO)
- **Expected**: Devices offline for >30 minutes may hit limit

## Test Results

Results are saved to `testing/results/` directory as JSON files:

```
testing/results/network_failure_test_YYYYMMDD_HHMMSS.json
```

### Result Format

```json
[
  {
    "scenario": "20% Devices - 5 minutes",
    "timestamp": "2026-01-03T16:17:35.957625",
    "device_count": 50,
    "disconnect_percent": 20,
    "disconnect_duration_seconds": 300.0,
    "messages_queued_during_disconnect": 1500,
    "flush_time_seconds": 45.2,
    "all_messages_flushed": true,
    "flush_time_target_met": false,
    "queue_growth_rate_msg_per_sec": 5.0,
    "flush_rate_msg_per_sec": 33.2,
    ...
  }
]
```

## Understanding Results

### ✅ Good Results
- `flush_time_target_met: true` - Flush completed within target
- `all_messages_flushed: true` - No messages lost
- `devices_at_queue_limit: 0` - No devices hit limit

### ⚠️ Warning Signs
- `flush_time_target_met: false` - Flush took longer than target
- `devices_at_queue_limit > 0` - Some devices hit queue limit
- `flush_rate_msg_per_sec < 5` - Slow flush rate

### ❌ Issues
- `all_messages_flushed: false` - Messages not flushed (timeout)
- `final_queue_size_after_flush > 0` - Messages still queued
- `max_queue_size_reached >= 10000` - Queue limit reached

## Troubleshooting

### Devices Not Starting
- Check MQTT broker is running: `docker-compose ps` (in docker directory)
- Verify broker host/port in `.env` file
- Check firewall/network connectivity

### Queue Not Growing
- Verify devices are actually disconnected (check processes)
- Check queue database files exist: `ls devices/queues/`
- Verify devices are publishing (check logs)

### Flush Taking Too Long
- **Normal**: Large backlogs (>1000 messages) take time
- **Issue**: If flush rate < 5 msg/s, check:
  - MQTT broker performance
  - Network latency
  - Collector processing speed

### Queue Limit Reached
- **Expected**: For devices offline >30 minutes
- **Solution**: Increase `max_queue_size` in `device_simulator.py` (if needed)
- **Alternative**: Implement message expiration/dropping strategy

## Advanced Testing

### Test Queue Limits (Hours/Days)

To test what happens if devices are offline for extended periods:

1. **Modify test scenario**:
   ```python
   {"name": "50% Devices - 2 hours", "disconnect_percent": 50, "duration_minutes": 120}
   ```

2. **Run test**:
   ```bash
   python testing/test_network_failures.py --scenario "50% Devices - 2 hours"
   ```

3. **Monitor**:
   - Queue growth rate
   - When devices hit 10,000 message limit
   - Message dropping behavior

### Test Different Device Counts

```bash
# Test with 100 devices
python testing/test_network_failures.py --devices 100

# Test with 200 devices
python testing/test_network_failures.py --devices 200
```

### Continuous Testing

Run multiple iterations:

```bash
# Run 5 iterations of same scenario
for i in {1..5}; do
  echo "Iteration $i"
  python testing/test_network_failures.py --scenario "20% Devices - 5 minutes"
  sleep 60
done
```

## Integration with Performance Testing

Network failure tests complement latency performance testing:

1. **Baseline**: Run latency tests with all devices connected
2. **Failure**: Run network failure tests
3. **Recovery**: Measure latency after reconnection
4. **Compare**: Analyze impact of network failures on latency

## Best Practices

1. **Run tests during low-load periods** (if testing on shared infrastructure)
2. **Monitor system resources** during tests
3. **Save results** for comparison over time
4. **Test incrementally**: Start with short durations, increase gradually
5. **Document findings** in test results

## Example Test Run

```bash
$ python testing/test_network_failures.py --devices 50

======================================================================
🌐 NETWORK FAILURE TESTING SUITE
======================================================================
Number of Devices: 50
Test Scenarios: 5
Results Directory: testing/results

🚀 Starting all devices...
✅ 50 devices started

⏳ Waiting for devices to stabilize (10 seconds)...

======================================================================
Test 1/5
======================================================================
🧪 TEST SCENARIO: 20% Devices - 5 minutes
   Disconnect: 20% of devices
   Duration: 5 minutes (300 seconds)
   
   📊 Devices: 10 disconnected, 40 remaining
   🔌 Disconnecting 10 devices...
   ✅ 10 devices disconnected
   
   📈 Monitoring queue growth for 5 minutes...
   ⏱️  10.0s: Total queued=50, Max per device=5
   ⏱️  20.0s: Total queued=100, Max per device=10
   ...
   
   📊 Queue Statistics:
      Initial: 0 messages
      Final: 1500 messages
      Queued during disconnect: 1500
      Max per device: 150
      Growth rate: 5.00 msg/s
   
   🔄 Reconnecting and measuring flush time...
   ⏱️  Measuring flush time for 10 devices...
   ✅ All queues flushed in 45.2s (33.2 msg/s)
   
   📋 Test Summary:
      Messages queued: 1500
      Flush time: 45.20s
      Target (<30s): ❌ MISSED
      All flushed: ✅ YES

💾 Results saved to: testing/results/network_failure_test_20260103_161735.json
```

## Related Documentation

- [Performance Testing Documentation](../PERFORMANCE_TESTING_DOCUMENTATION.md)
- [Device Simulator](../devices/device_simulator.py) - Offline queue implementation
- [Monitoring Tools](../monitoring/README.md) - Latency monitoring

