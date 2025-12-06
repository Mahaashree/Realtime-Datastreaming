# Environment Configuration Guide

This guide explains which `.env` file to use for different deployment scenarios.

## Quick Reference

| Scenario | Use File | InfluxDB URL |
|----------|----------|--------------|
| **Local Development** | `env.example` | `http://localhost:8086` |
| **VPS 1 (Docker)** | `env.example.vps1-docker` | `http://localhost:8086` |
| **VPS 2 (Flask)** | `env.example.vps2-flask` | `http://influxdb.secruin.cloud:8086` |
| **Device Simulators** | `env.example.device` | N/A (not needed) |

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

| Variable | Description | Example |
|----------|-------------|---------|
| `INFLUXDB_URL` | InfluxDB connection URL | `http://localhost:8086` |
| `INFLUXDB_TOKEN` | InfluxDB authentication token | `my-super-secret-auth-token` |
| `INFLUXDB_ORG` | InfluxDB organization | `my-org` |
| `INFLUXDB_BUCKET` | InfluxDB bucket name | `vehicle-data` |
| `MQTT_BROKER_HOST` | MQTT broker hostname | `localhost` or `mqtt.secruin.cloud` |
| `MQTT_BROKER_PORT` | MQTT broker port | `1883` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FLASK_HOST` | Flask bind address | `0.0.0.0` |
| `FLASK_PORT` | Flask port | `5000` |

## Verification

After setting up your `.env` file, verify the configuration:

```bash
# Check environment variables are loaded
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('INFLUXDB_URL:', os.getenv('INFLUXDB_URL'))"

# Test InfluxDB connection
curl $(grep INFLUXDB_URL .env | cut -d '=' -f2)/health

# Test MQTT connection
mosquitto_pub -h $(grep MQTT_BROKER_HOST .env | cut -d '=' -f2) -p $(grep MQTT_BROKER_PORT .env | cut -d '=' -f2) -t test -m "test"
```

