# Network Failure Testing - Quick Start

## Quick Test (5 minutes)

Test 20% of devices disconnected for 5 minutes:

```bash
# 1. Start services
cd docker && docker-compose up -d

# 2. Start collector (in another terminal)
python collector/mqtt_collector.py

# 3. Run test
python testing/test_network_failures.py --scenario "20% Devices - 5 minutes"
```

## Full Test Suite

Run all standard scenarios:

```bash
python testing/test_network_failures.py
```

## Analyze Results

```bash
# Analyze latest test
python testing/analyze_results.py

# Analyze all tests
python testing/analyze_results.py --all
```

## Custom Test

Test specific parameters:

```bash
python testing/test_network_failures.py --custom
```

Then enter:
- Disconnect percentage: `30`
- Duration in minutes: `15`

## What Gets Tested

✅ **Offline Queue Storage**: Messages stored in SQLite during disconnection  
✅ **Queue Growth**: Monitors queue growth rate  
✅ **Queue Limits**: Detects when devices hit 10,000 message limit  
✅ **Flush Performance**: Measures time to flush queues after reconnection  
✅ **Message Delivery**: Verifies all messages are eventually delivered  

## Expected Results

For a 5-minute test with 20% devices (10 devices):
- **Messages Queued**: ~1,500 messages (10 devices × 5 msg/s × 300s)
- **Flush Time**: 30-60 seconds (depends on backlog size)
- **Target**: < 30s for < 200 messages, < 60s for < 500 messages

## Troubleshooting

**Devices not starting?**
- Check MQTT broker: `docker-compose ps` (in docker directory)
- Verify `.env` file has correct broker host/port

**Queue not growing?**
- Verify devices are actually disconnected
- Check queue files: `ls devices/queues/`

**Flush too slow?**
- Normal for large backlogs (>1000 messages)
- Check MQTT broker performance
- Monitor system resources

