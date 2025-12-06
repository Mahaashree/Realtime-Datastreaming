# Troubleshooting Guide

## Common Issues and Solutions

### Issue: Devices Can't Connect to MQTT Broker

**Symptoms:**
- All devices show "connection error: timed out"
- Queue sizes are growing (messages being queued)
- No "Device X connected to MQTT broker" messages

**Diagnosis:**

Run the connection checker:
```bash
python check_mqtt_connection.py
```

**Solutions:**

#### Solution 1: Use Localhost for Local Testing

If you're testing locally and don't have Cloudflare tunnel set up:

```bash
# Edit .env file
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
```

Make sure Mosquitto is running:
```bash
cd docker
docker-compose ps
# Should show mosquitto running
```

#### Solution 2: Verify Cloudflare Tunnel

If using `mqtt.secruin.cloud`:

1. **Check tunnel is running on VPS:**
   ```bash
   # SSH to VPS 1
   cloudflared tunnel info docker-services
   ```

2. **Check tunnel logs:**
   ```bash
   docker logs telegraf  # or check cloudflared logs
   ```

3. **Test connection manually:**
   ```bash
   mosquitto_pub -h mqtt.secruin.cloud -p 1883 -t test -m "hello"
   mosquitto_sub -h mqtt.secruin.cloud -p 1883 -t test
   ```

4. **Verify DNS:**
   - Check Cloudflare dashboard
   - Ensure `mqtt.secruin.cloud` CNAME points to tunnel

#### Solution 3: Check Firewall

If testing from local machine to remote broker:

```bash
# Test if port is accessible
telnet mqtt.secruin.cloud 1883
# or
nc -zv mqtt.secruin.cloud 1883
```

### Issue: Flask Dashboard Can't Connect to InfluxDB

**Symptoms:**
- "Connection to influxdb.secruin.cloud timed out" errors
- Dashboard shows no data

**Solution:**

Run the InfluxDB connection checker:
```bash
python check_influxdb_connection.py
```

**Quick Fix:**

For local development:
```bash
# Edit .env
INFLUXDB_URL=http://localhost:8086
# Remove or comment out fallback
# INFLUXDB_URL_FALLBACK=
```

### Issue: Too Many Queue Messages

**Symptoms:**
- Queue sizes growing rapidly (70+, 100+)
- Devices never connect

**Solution:**

This means devices are working but can't reach broker. Fix the MQTT connection issue above. Once connected, queued messages will automatically flush.

To clear queues manually (if needed):
```bash
# Stop devices first
# Then delete queue files
rm devices/queues/*.db
```

### Issue: Devices Connect But No Data in Dashboard

**Symptoms:**
- Devices show "connected to MQTT broker"
- Dashboard shows no data

**Check:**

1. **Is collector/Telegraf running?**
   ```bash
   # Check Telegraf
   docker ps | grep telegraf
   docker logs telegraf
   
   # Or check Python collector
   ps aux | grep mqtt_collector
   ```

2. **Is data reaching InfluxDB?**
   ```bash
   # Access InfluxDB UI
   # http://localhost:8086
   # Check if data exists in vehicle-data bucket
   ```

3. **Check MQTT topics:**
   ```bash
   # Subscribe to see if messages are published
   mosquitto_sub -h localhost -p 1883 -t 'vehicle/speed/+' -v
   ```

### Issue: Cloudflare Tunnel Not Working

**Symptoms:**
- Can't access services via domain
- Connection timeouts

**Checklist:**

1. **Tunnel is running:**
   ```bash
   cloudflared tunnel list
   cloudflared tunnel run <tunnel-name>
   ```

2. **Routes are configured:**
   - Cloudflare Zero Trust Dashboard → Networks → Tunnels
   - Verify public hostname routes are set up

3. **DNS is configured:**
   - Cloudflare DNS → Check CNAME records
   - Should auto-create when tunnel route is added

4. **Test tunnel locally:**
   ```bash
   # On VPS, test local connection
   curl http://localhost:8086/health  # InfluxDB
   mosquitto_pub -h localhost -p 1883 -t test -m "test"  # MQTT
   ```

## Quick Diagnostic Commands

### Check All Services

```bash
# MQTT Connection
python check_mqtt_connection.py

# InfluxDB Connection  
python check_influxdb_connection.py

# Docker Services
cd docker && docker-compose ps

# Device Status
# Look for "connected to MQTT broker" in logs
```

### Test MQTT Manually

```bash
# Publish test message
mosquitto_pub -h localhost -p 1883 -t test/topic -m "Hello MQTT"

# Subscribe to vehicle topics
mosquitto_sub -h localhost -p 1883 -t 'vehicle/speed/+' -v

# Subscribe to all topics
mosquitto_sub -h localhost -p 1883 -t '#' -v
```

### Test InfluxDB Manually

```bash
# Health check
curl http://localhost:8086/health

# Or via browser
# http://localhost:8086
```

## Getting Help

If issues persist:

1. Check logs:
   - Device logs: Look for ERROR messages
   - Flask logs: Check for connection errors
   - Docker logs: `docker-compose logs`

2. Verify configuration:
   ```bash
   # Check .env file
   cat .env
   
   # Verify environment variables are loaded
   python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('MQTT:', os.getenv('MQTT_BROKER_HOST')); print('InfluxDB:', os.getenv('INFLUXDB_URL'))"
   ```

3. Test each component individually:
   - Start Docker services → Test MQTT → Test InfluxDB → Start devices → Start collector → Start dashboard

