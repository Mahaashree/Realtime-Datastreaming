# Telegraf Integration Guide

## What is Telegraf?

Telegraf is a plugin-driven agent for collecting, processing, aggregating, and writing metrics. It's part of the TICK stack (Telegraf, InfluxDB, Chronograf, Kapacitor) and is designed to work seamlessly with InfluxDB.

## Benefits of Using Telegraf

### 1. **Production-Ready & Robust**
- Battle-tested in production environments
- Built-in error handling and retry logic
- Automatic reconnection to MQTT broker and InfluxDB
- Better performance than custom Python scripts

### 2. **MQTT Integration**
- Native MQTT input plugin (no custom code needed)
- Supports QoS levels, persistent sessions
- Automatic topic subscription and message parsing
- Handles JSON payload parsing natively

### 3. **Additional Metrics Collection**
- **System Metrics**: CPU, memory, disk, network usage
- **Docker Metrics**: Container stats, resource usage
- **MQTT Broker Metrics**: Connection counts, message rates
- **Application Metrics**: Custom metrics from your services

### 4. **Data Processing**
- Built-in data transformation
- Aggregation (mean, sum, min, max)
- Data filtering and routing
- Tag manipulation

### 5. **Multiple Outputs**
- Write to multiple InfluxDB instances
- Send to other systems (Kafka, Prometheus, etc.)
- Duplicate data for backup/analytics

### 6. **Configuration-Based**
- No code changes needed
- Easy to modify and maintain
- Version control friendly

## Architecture Comparison

### Current Architecture (Python Collector)
```
MQTT Broker → Python Collector → InfluxDB
```

### With Telegraf
```
MQTT Broker → Telegraf → InfluxDB
              ↓
         System Metrics
         Docker Metrics
```

## When to Use Telegraf

**Use Telegraf if:**
- ✅ You want production-grade reliability
- ✅ You need system/Docker metrics
- ✅ You want better performance
- ✅ You prefer configuration over code
- ✅ You need data transformation/aggregation

**Keep Python Collector if:**
- ✅ You need custom device status tracking (like current implementation)
- ✅ You want to integrate with Flask dashboard status API
- ✅ You need complex custom logic

**Best Approach: Use Both**
- Telegraf for MQTT → InfluxDB data collection
- Python collector for device status tracking (optional, can be replaced)

## Setup Instructions

### Option 1: Telegraf as Replacement (Recommended)

Replace the Python collector with Telegraf for better reliability and performance.

### Option 2: Telegraf as Addition

Run both Telegraf and Python collector:
- Telegraf: Collects MQTT data + system metrics
- Python collector: Provides device status API for Flask dashboard

### Option 3: Hybrid Approach

- Use Telegraf for data collection
- Keep minimal Python script for device status API only

## Installation

### Docker Compose (Recommended)

Add Telegraf to your `docker-compose.yml`:

```yaml
telegraf:
  image: telegraf:1.28
  container_name: telegraf
  volumes:
    - ./telegraf/telegraf.conf:/etc/telegraf/telegraf.conf:ro
    - /var/run/docker.sock:/var/run/docker.sock:ro
  environment:
    - INFLUXDB_URL=${INFLUXDB_URL}
    - INFLUXDB_TOKEN=${INFLUXDB_TOKEN}
    - INFLUXDB_ORG=${INFLUXDB_ORG}
    - INFLUXDB_BUCKET=${INFLUXDB_BUCKET}
    - MQTT_BROKER_HOST=${MQTT_BROKER_HOST}
    - MQTT_BROKER_PORT=${MQTT_BROKER_PORT}
  depends_on:
    - influxdb
    - mosquitto
  restart: unless-stopped
```

### Manual Installation

```bash
# On VPS Instance 1 (with Docker services)
wget https://dl.influxdata.com/telegraf/releases/telegraf_1.28.0-1_amd64.deb
sudo dpkg -i telegraf_1.28.0-1_amd64.deb

# Copy configuration
sudo cp telegraf/telegraf.conf /etc/telegraf/telegraf.conf

# Start Telegraf
sudo systemctl start telegraf
sudo systemctl enable telegraf
```

## Configuration

See `telegraf/telegraf.conf` for complete configuration with:
- MQTT input plugin
- InfluxDB output plugin
- System metrics collection
- Docker metrics collection
- JSON parsing for vehicle speed data

## Monitoring Telegraf

```bash
# Check status
sudo systemctl status telegraf

# View logs
sudo journalctl -u telegraf -f

# Test configuration
telegraf --config /etc/telegraf/telegraf.conf --test
```

## Performance Comparison

| Metric | Python Collector | Telegraf |
|--------|-----------------|----------|
| Throughput | ~1,000 msg/s | ~10,000+ msg/s |
| Memory Usage | ~50-100 MB | ~20-50 MB |
| CPU Usage | Medium | Low |
| Reliability | Good | Excellent |
| Reconnection | Manual | Automatic |
| System Metrics | No | Yes |
| Docker Metrics | No | Yes |

## Migration Path

1. **Phase 1**: Deploy Telegraf alongside Python collector
2. **Phase 2**: Verify data collection matches
3. **Phase 3**: Update Flask dashboard to use InfluxDB queries for device status
4. **Phase 4**: Remove Python collector (optional)

## Troubleshooting

### Telegraf Not Collecting Data

```bash
# Check configuration
telegraf --config /etc/telegraf/telegraf.conf --test

# Check MQTT connection
mosquitto_sub -h localhost -p 1883 -t 'vehicle/speed/+' -v

# Check InfluxDB connection
curl http://localhost:8086/health
```

### Data Format Issues

Telegraf expects JSON format:
```json
{
  "device_id": "vehicle_01",
  "speed": 65.5,
  "timestamp": 1234567890.123
}
```

If your format differs, use Telegraf's JSON parser configuration.

## Additional Resources

- [Telegraf Documentation](https://docs.influxdata.com/telegraf/)
- [MQTT Input Plugin](https://github.com/influxdata/telegraf/tree/master/plugins/inputs/mqtt_consumer)
- [InfluxDB Output Plugin](https://github.com/influxdata/telegraf/tree/master/plugins/outputs/influxdb_v2)

