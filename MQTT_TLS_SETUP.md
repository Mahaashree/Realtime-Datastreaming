# MQTT TLS Setup Guide

This guide explains how to enable TLS encryption and authentication for MQTT connections, following MQTT best practices.

## Quick Start

### Option 1: Development with Self-Signed Certificates

1. **Generate certificates:**
   ```bash
   cd docker/mosquitto
   ./generate_certs.sh
   ```

2. **Update Mosquitto config** (`docker/mosquitto/config/mosquitto.conf`):
   ```conf
   # Enable TLS listener
   listener 8883
   allow_anonymous false
   password_file /mosquitto/config/passwd
   certfile /mosquitto/config/certs/server.crt
   keyfile /mosquitto/config/certs/server.key
   cafile /mosquitto/config/certs/ca.crt
   ```

3. **Update your `.env` file:**
   ```env
   MQTT_BROKER_PORT=8883
   MQTT_USE_TLS=true
   MQTT_TLS_INSECURE=true  # Required for self-signed certs
   MQTT_CA_CERTS=docker/mosquitto/config/certs/ca.crt
   ```

4. **Restart services:**
   ```bash
   docker compose -f docker/docker-compose.yml restart mosquitto
   ```

### Option 2: Production with CA Certificates

1. **Obtain certificates from a trusted CA** (Let's Encrypt, commercial CA, etc.)

2. **Update Mosquitto config:**
   ```conf
   listener 8883
   allow_anonymous false
   password_file /mosquitto/config/passwd
   certfile /mosquitto/config/certs/server.crt
   keyfile /mosquitto/config/certs/server.key
   cafile /mosquitto/config/certs/ca.crt
   ```

3. **Update your `.env` file:**
   ```env
   MQTT_BROKER_PORT=8883
   MQTT_USE_TLS=true
   MQTT_TLS_INSECURE=false  # Use false for CA-signed certificates
   MQTT_CA_CERTS=docker/mosquitto/config/certs/ca.crt
   ```

## MQTT Authentication Setup

### 1. Create Password File

```bash
# Inside Mosquitto container
docker exec -it mqtt-broker mosquitto_passwd -c /mosquitto/config/passwd device_user
# Enter password when prompted

# Add more users
docker exec -it mqtt-broker mosquitto_passwd /mosquitto/config/passwd collector_user
```

### 2. Update Mosquitto Config

```conf
allow_anonymous false
password_file /mosquitto/config/passwd
```

### 3. Update `.env` File

```env
MQTT_USERNAME=device_user
MQTT_PASSWORD=your_password_here
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MQTT_USE_TLS` | Enable TLS encryption | `false` |
| `MQTT_BROKER_PORT` | MQTT broker port (1883 for plain, 8883 for TLS) | `1883` |
| `MQTT_TLS_INSECURE` | Allow self-signed certificates | `false` |
| `MQTT_CA_CERTS` | Path to CA certificate file | - |
| `MQTT_CERTFILE` | Path to client certificate (optional, for mutual TLS) | - |
| `MQTT_KEYFILE` | Path to client private key (optional, for mutual TLS) | - |
| `MQTT_USERNAME` | MQTT username for authentication | - |
| `MQTT_PASSWORD` | MQTT password for authentication | - |
| `MQTT_CLIENT_ID` | Custom MQTT client ID | Auto-generated |

## MQTT Best Practices Implemented

### ✅ Persistent Sessions
- `clean_session=False` - Messages retained during disconnections
- Enables reliable message delivery

### ✅ QoS 1 (At Least Once Delivery)
- All messages published with QoS 1
- Guarantees message delivery (may have duplicates)

### ✅ Keepalive (60 seconds)
- Maintains connection health
- Detects dead connections quickly

### ✅ Automatic Reconnection
- Exponential backoff (1s to 120s)
- Handles network interruptions gracefully

### ✅ TLS Encryption
- End-to-end encryption for data in transit
- Protects sensitive driver drowsiness data

### ✅ Authentication
- Username/password authentication
- Prevents unauthorized access

### ✅ Offline Queue
- SQLite-based queue for devices
- Messages stored when disconnected
- Automatic flush on reconnection

## Testing TLS Connection

### Test with mosquitto_pub (TLS)

```bash
# With self-signed certs
mosquitto_pub \
  -h localhost \
  -p 8883 \
  --cafile docker/mosquitto/config/certs/ca.crt \
  -t test/topic \
  -m "Hello TLS"

# With authentication
mosquitto_pub \
  -h localhost \
  -p 8883 \
  --cafile docker/mosquitto/config/certs/ca.crt \
  -u device_user \
  -P your_password \
  -t test/topic \
  -m "Hello TLS with Auth"
```

### Test with mosquitto_sub (TLS)

```bash
mosquitto_sub \
  -h localhost \
  -p 8883 \
  --cafile docker/mosquitto/config/certs/ca.crt \
  -t "device/data/+" \
  -v
```

## Troubleshooting

### Certificate Errors

**Error:** `SSL: CERTIFICATE_VERIFY_FAILED`

**Solution:** Set `MQTT_TLS_INSECURE=true` for self-signed certificates (development only)

### Connection Refused

**Error:** `Connection refused on port 8883`

**Solution:** 
1. Check Mosquitto config has TLS listener enabled
2. Verify certificates exist in `docker/mosquitto/config/certs/`
3. Restart Mosquitto container

### Authentication Failed

**Error:** `Bad username or password`

**Solution:**
1. Verify password file exists: `docker exec mqtt-broker ls /mosquitto/config/passwd`
2. Check username/password in `.env` file
3. Verify `allow_anonymous false` in Mosquitto config

## Production Recommendations

1. **Use CA-signed certificates** (Let's Encrypt, commercial CA)
2. **Set `MQTT_TLS_INSECURE=false`** for production
3. **Use strong passwords** for MQTT authentication
4. **Enable ACLs** (Access Control Lists) for topic-level permissions
5. **Monitor connection logs** for security issues
6. **Rotate certificates** before expiration
7. **Use mutual TLS** (client certificates) for high-security environments

## Performance Impact

- **Bandwidth:** +4% overhead (negligible)
- **Latency:** +1-2ms per message (acceptable)
- **CPU:** +1-3% (acceptable)
- **Connection time:** +50-200ms per device (one-time)

For detailed performance analysis, see the scaling documentation.


