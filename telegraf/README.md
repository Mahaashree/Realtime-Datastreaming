# Telegraf Configuration

This directory contains Telegraf configuration for collecting MQTT vehicle data and system metrics.

## Quick Start

### Using Docker Compose

1. **Option 1: Use separate compose file (recommended)**
   ```bash
   cd docker
   docker-compose -f docker-compose.with-telegraf.yml up -d
   ```

2. **Option 2: Uncomment Telegraf in main compose file**
   ```bash
   # Edit docker/docker-compose.yml
   # Uncomment the telegraf service section
   cd docker
   docker-compose up -d
   ```

### Manual Installation

```bash
# Install Telegraf
wget https://dl.influxdata.com/telegraf/releases/telegraf_1.28.0-1_amd64.deb
sudo dpkg -i telegraf_1.28.0-1_amd64.deb

# Copy configuration
sudo cp telegraf/telegraf.conf /etc/telegraf/telegraf.conf

# Edit configuration with your values
sudo nano /etc/telegraf/telegraf.conf

# Start Telegraf
sudo systemctl start telegraf
sudo systemctl enable telegraf
```

## Configuration

The `telegraf.conf` file includes:

### Inputs
- **MQTT Consumer**: Subscribes to `vehicle/speed/+` topics
- **CPU**: System CPU metrics
- **Memory**: System memory usage
- **Disk**: Disk usage and I/O
- **Network**: Network interface statistics
- **Docker**: Container metrics

### Outputs
- **InfluxDB v2**: Writes all metrics to InfluxDB

## Environment Variables

When using Docker Compose, set these in your `.env` file or docker-compose.yml:

```env
INFLUXDB_URL=http://influxdb:8086
INFLUXDB_TOKEN=my-super-secret-auth-token
INFLUXDB_ORG=my-org
INFLUXDB_BUCKET=vehicle-data
MQTT_BROKER_HOST=mosquitto
MQTT_BROKER_PORT=1883
```

For manual installation, edit `telegraf.conf` directly or use environment variable substitution.

## Testing Configuration

```bash
# Test configuration syntax
telegraf --config /etc/telegraf/telegraf.conf --test

# Check what metrics will be collected
telegraf --config /etc/telegraf/telegraf.conf --test --input-filter mqtt_consumer
```

## Monitoring Telegraf

```bash
# Check status
sudo systemctl status telegraf

# View logs
sudo journalctl -u telegraf -f

# Docker logs
docker logs telegraf -f
```

## Data Format

Telegraf expects MQTT messages in JSON format:

```json
{
  "device_id": "vehicle_01",
  "speed": 65.5,
  "timestamp": 1234567890.123
}
```

The configuration automatically:
- Extracts `device_id` as a tag
- Extracts `speed` as a field
- Uses `timestamp` for the measurement time

## Metrics Collected

### Vehicle Data
- Measurement: `mqtt_consumer` (or configured name)
- Tags: `device_id`
- Fields: `speed`, and any other fields in JSON

### System Metrics
- `cpu`: CPU usage per core and total
- `mem`: Memory usage
- `disk`: Disk usage and I/O
- `net`: Network statistics
- `docker`: Container metrics (if Docker socket mounted)

## Customization

### Change Collection Interval

Edit `[agent]` section:
```toml
[agent]
  interval = "5s"  # Collect every 5 seconds
```

### Add More MQTT Topics

Edit `[[inputs.mqtt_consumer]]` section:
```toml
topics = [
  "vehicle/speed/+",
  "vehicle/location/+",
  "vehicle/temperature/+"
]
```

### Filter Metrics

Add processors to filter or transform data:
```toml
[[processors.regex]]
  [[processors.regex.tags]]
    key = "device_id"
    pattern = "^vehicle_(.*)$"
    replacement = "${1}"
```

## Troubleshooting

### Telegraf Not Receiving MQTT Messages

1. Check MQTT broker connection:
   ```bash
   mosquitto_sub -h localhost -p 1883 -t 'vehicle/speed/+' -v
   ```

2. Check Telegraf logs:
   ```bash
   docker logs telegraf
   ```

3. Verify configuration:
   ```bash
   telegraf --config /etc/telegraf/telegraf.conf --test
   ```

### Data Not Appearing in InfluxDB

1. Check InfluxDB connection:
   ```bash
   curl http://localhost:8086/health
   ```

2. Verify token and organization:
   ```bash
   # Check environment variables
   docker exec telegraf env | grep INFLUXDB
   ```

3. Check InfluxDB logs:
   ```bash
   docker logs influxdb
   ```

## Performance Tuning

### Increase Batch Size

```toml
[agent]
  metric_batch_size = 5000
  metric_buffer_limit = 50000
```

### Adjust Flush Interval

```toml
[agent]
  flush_interval = "5s"  # Flush more frequently
```

## Security

For production, consider:
- Using MQTT authentication
- Using TLS for MQTT connections
- Securing InfluxDB token
- Limiting Docker socket access

See `TELEGRAF.md` for more details.

