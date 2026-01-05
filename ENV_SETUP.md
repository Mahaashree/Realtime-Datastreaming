# Environment Configuration Guide

This guide explains which `.env` file to use for different deployment scenarios.

## Quick Reference

| Scenario              | Use File                  | InfluxDB URL                         |
| --------------------- | ------------------------- | ------------------------------------ |
| **Local Development** | `env.example`             | `http://localhost:8086`              |
| **VPS 1 (Docker)**    | `env.example.vps1-docker` | `http://localhost:8086`              |
| **VPS 2 (Flask)**     | `env.example.vps2-flask`  | `http://influxdb.secruin.cloud:8086` |
| **Device Simulators** | `env.example.device`      | N/A (not needed)                     |

## Local Development Setup

If you're running everything on your local machine:

```bash
# Copy the local development template
cp env.example .env

# Edit .env - defaults should work:
# INFLUXDB_URL=http://localhost:8086
# MQTT_BROKER_HOST=localhost
```

## Production Deployment

### VPS Instance 1 (Docker Services)

```bash
# Copy VPS 1 template
cp env.example.vps1-docker .env

# Edit .env - services use localhost since they're in same Docker network:
# INFLUXDB_URL=http://localhost:8086
# MQTT_BROKER_HOST=mosquitto
```

### VPS Instance 2 (Flask Dashboard)

```bash
# Copy VPS 2 template
cp env.example.vps2-flask .env

# Edit .env - use Cloudflare tunnel subdomains:
# INFLUXDB_URL=http://influxdb.secruin.cloud:8086
# MQTT_BROKER_HOST=mqtt.secruin.cloud
```

### Device Simulators

```bash
# Copy device template
cp env.example.device .env

# Edit .env - use Cloudflare tunnel for MQTT:
# MQTT_BROKER_HOST=mqtt.secruin.cloud
```

## Troubleshooting Connection Issues

### Flask Dashboard Can't Connect to InfluxDB

**Error**: `Connection to influxdb.secruin.cloud timed out`

**Solutions**:

1. **For Local Development**:

   ```bash
   # Make sure you're using the local .env file
   cat .env | grep INFLUXDB_URL
   # Should show: INFLUXDB_URL=http://localhost:8086

   # If not, copy the correct template:
   cp env.example .env
   ```

2. **For Production (Cloudflare Tunnel)**:

   - Verify Cloudflare tunnel is running on VPS 1
   - Check tunnel status: `cloudflared tunnel info docker-services`
   - Verify subdomain DNS is configured in Cloudflare
   - Test connection: `curl http://influxdb.secruin.cloud:8086/health`

3. **Check InfluxDB is Running**:

   ```bash
   # On VPS 1 (Docker)
   docker ps | grep influxdb

   # Test local connection
   curl http://localhost:8086/health
   ```

### Common Mistakes

1. **Using production URL for local development**

   - ❌ Wrong: `INFLUXDB_URL=http://influxdb.secruin.cloud:8086` (when running locally)
   - ✅ Correct: `INFLUXDB_URL=http://localhost:8086` (for local dev)

2. **Using localhost URL in production**

   - ❌ Wrong: `INFLUXDB_URL=http://localhost:8086` (when Flask is on different VPS)
   - ✅ Correct: `INFLUXDB_URL=http://influxdb.secruin.cloud:8086` (for production)

3. **Wrong .env file**
   - Make sure you're using the correct template for your scenario

## Environment Variable Reference

### Required Variables

| Variable           | Description                   | Example                             |
| ------------------ | ----------------------------- | ----------------------------------- |
| `INFLUXDB_URL`     | InfluxDB connection URL       | `http://localhost:8086`             |
| `INFLUXDB_TOKEN`   | InfluxDB authentication token | `my-super-secret-auth-token`        |
| `INFLUXDB_ORG`     | InfluxDB organization         | `my-org`                            |
| `INFLUXDB_BUCKET`  | InfluxDB bucket name          | `vehicle-data`                      |
| `MQTT_BROKER_HOST` | MQTT broker hostname          | `localhost` or `mqtt.secruin.cloud` |
| `MQTT_BROKER_PORT` | MQTT broker port              | `1883`                              |

### Optional Variables

#### Flask Dashboard

| Variable                | Description                                              | Default                               |
| ----------------------- | -------------------------------------------------------- | ------------------------------------- |
| `FLASK_HOST`            | Flask bind address                                       | `0.0.0.0`                             |
| `FLASK_PORT`            | Flask port                                               | `5000`                                |
| `FLASK_SECRET_KEY`      | Flask secret key for sessions                            | `dev-secret-key-change-in-production` |
| `INFLUXDB_URL_FALLBACK` | Fallback InfluxDB URL (dashboard tries if primary fails) | `None`                                |

#### Collector Configuration

| Variable                   | Description                                      | Default              |
| -------------------------- | ------------------------------------------------ | -------------------- |
| `COLLECTOR_WORKER_THREADS` | Number of worker threads for processing messages | `8`                  |
| `COLLECTOR_MAX_QUEUE_SIZE` | Maximum queue size before messages are dropped   | `20000`              |
| `BATCH_DRAIN_SIZE`         | Batch size for draining messages from queue      | `50`                 |
| `LOG_FILE`                 | Path to collector log file                       | `logs/collector.log` |

#### Prometheus & Monitoring

| Variable            | Description                                       | Default |
| ------------------- | ------------------------------------------------- | ------- |
| `ENABLE_PROMETHEUS` | Enable Prometheus metrics export                  | `true`  |
| `PROMETHEUS_PORT`   | Prometheus metrics server port                    | `9090`  |
| `STATS_SERVER_PORT` | Collector stats server port for real-time metrics | `9091`  |

#### Alert Thresholds

| Variable               | Description                           | Default |
| ---------------------- | ------------------------------------- | ------- |
| `ALERT_P95_LATENCY_MS` | P95 latency threshold in milliseconds | `2000`  |
| `ALERT_P99_LATENCY_MS` | P99 latency threshold in milliseconds | `5000`  |
| `ALERT_ERROR_RATE_PCT` | Error rate threshold as percentage    | `5`     |
| `ALERT_DROP_RATE_PCT`  | Drop rate threshold as percentage     | `2`     |
| `ALERT_QUEUE_DEPTH`    | Queue depth threshold                 | `15000` |
| `ALERT_MIN_THROUGHPUT` | Minimum throughput threshold in msg/s | `50`    |

#### Device Simulator

| Variable            | Description                          | Default          |
| ------------------- | ------------------------------------ | ---------------- |
| `PUBLISH_INTERVAL`  | Publish interval in seconds          | `1.0`            |
| `OFFLINE_QUEUE_DIR` | Directory for offline message queues | `offline_queues` |

#### MQTT Security (Optional)

| Variable            | Description                               | Default        |
| ------------------- | ----------------------------------------- | -------------- |
| `MQTT_USE_TLS`      | Enable TLS encryption                     | `false`        |
| `MQTT_TLS_INSECURE` | Allow self-signed certificates (dev only) | `false`        |
| `MQTT_CA_CERTS`     | Path to CA certificate file               | `None`         |
| `MQTT_CERTFILE`     | Path to client certificate file           | `None`         |
| `MQTT_KEYFILE`      | Path to client private key file           | `None`         |
| `MQTT_USERNAME`     | MQTT username                             | `None`         |
| `MQTT_PASSWORD`     | MQTT password                             | `None`         |
| `MQTT_CLIENT_ID`    | Custom MQTT client ID                     | Auto-generated |

## Monitoring & Stats Server

The collector includes a built-in stats server that provides real-time metrics to the dashboard:

- **Stats Server Port**: Default `9091` (configurable via `STATS_SERVER_PORT`)
- **Purpose**: Provides accurate rolling window latency metrics (last 10,000 samples)
- **Endpoint**: `http://localhost:9091/stats`
- **Dashboard Integration**: The dashboard automatically uses the stats server for accurate latency metrics, falling back to Prometheus if unavailable

### Latency Measurement

The collector measures **full end-to-end latency** from device publish to InfluxDB write completion, including:

- MQTT network time (device → broker → collector)
- Queue wait time
- Message processing time
- InfluxDB write queue time

This provides a complete picture of system performance, not just network latency.

### Verifying Stats Server

```bash
# Check if stats server is running
curl http://localhost:9091/stats

# Should return JSON with latency, throughput, queue depth, etc.
```

### Prometheus Metrics

- **Prometheus Port**: Default `9090` (configurable via `PROMETHEUS_PORT`)
- **Metrics Endpoint**: `http://localhost:9090/metrics`
- **Note**: Prometheus shows cumulative metrics (all-time), while stats server shows rolling window (last 10,000 samples)

## Verification

After setting up your `.env` file, verify the configuration:

```bash
# Check environment variables are loaded
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('INFLUXDB_URL:', os.getenv('INFLUXDB_URL'))"

# Test InfluxDB connection
curl $(grep INFLUXDB_URL .env | cut -d '=' -f2)/health

# Test MQTT connection
mosquitto_pub -h $(grep MQTT_BROKER_HOST .env | cut -d '=' -f2) -p $(grep MQTT_BROKER_PORT .env | cut -d '=' -f2) -t test -m "test"

# Test stats server (after starting collector)
curl http://localhost:9091/stats

# Test Prometheus metrics (if enabled)
curl http://localhost:9090/metrics
```
