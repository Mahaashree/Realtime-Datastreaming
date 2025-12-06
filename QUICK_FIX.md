# Quick Fix: Make Dashboard Work Both Locally and Via Domain

## Problem
Dashboard only works locally OR via domain, but you want it to work in both scenarios.

## Solution
Use the **universal configuration** that automatically tries both localhost and domain.

## Quick Setup (1 minute)

```bash
# Copy the universal environment template
cp env.example.universal .env

# Edit .env and set both URLs:
nano .env
```

Set these values:
```env
# Primary URL (tried first) - use localhost for local, domain for production
INFLUXDB_URL=http://localhost:8086

# Fallback URL (tried if primary fails) - use domain for local, localhost for production
INFLUXDB_URL_FALLBACK=http://influxdb.secruin.cloud:8086
```

## How It Works

The dashboard will:
1. **Try primary URL first** (`INFLUXDB_URL`)
2. **If that fails, try fallback URL** (`INFLUXDB_URL_FALLBACK`)
3. **Automatically reconnect** if connection is lost
4. **Log which URL is being used**

## Verify It Works

### Check Health Status
```bash
curl http://localhost:5000/api/health
```

Response shows which URL is connected:
```json
{
  "status": "healthy",
  "influxdb_connected": true,
  "influxdb_url": "http://localhost:8086",
  "primary_url": "http://localhost:8086",
  "fallback_url": "http://influxdb.secruin.cloud:8086"
}
```

### Check Logs
```bash
# Look for connection messages
# Should see: "✓ Successfully connected to InfluxDB at http://..."
```

## Scenarios

### Scenario 1: Running Locally
- Primary: `http://localhost:8086` ✅ (works)
- Fallback: `http://influxdb.secruin.cloud:8086` (not tried)

### Scenario 2: Accessing Via Domain (Cloudflare Tunnel)
- Primary: `http://localhost:8086` ❌ (fails)
- Fallback: `http://influxdb.secruin.cloud:8086` ✅ (works)

### Scenario 3: Both Available
- Primary: `http://localhost:8086` ✅ (used)
- Fallback: `http://influxdb.secruin.cloud:8086` (not tried)

## Troubleshooting

### Still Getting Connection Errors?

1. **Check which URL is being tried:**
   ```bash
   curl http://localhost:5000/api/health
   ```

2. **Test URLs manually:**
   ```bash
   # Test localhost
   curl http://localhost:8086/health
   
   # Test domain
   curl http://influxdb.secruin.cloud:8086/health
   ```

3. **Check logs:**
   ```bash
   # Look for connection attempts
   # Should see: "Attempting to connect to InfluxDB at ..."
   ```

### Want to Force a Specific URL?

Set only one URL and leave fallback empty:
```env
INFLUXDB_URL=http://localhost:8086
INFLUXDB_URL_FALLBACK=
```

## Benefits

✅ **Works everywhere** - local development and production  
✅ **Automatic fallback** - no manual switching needed  
✅ **Self-healing** - reconnects automatically  
✅ **Clear logging** - see which URL is being used  

## Next Steps

1. Copy `env.example.universal` to `.env`
2. Set both URLs (primary and fallback)
3. Restart Flask dashboard
4. Check `/api/health` to verify connection

Done! Your dashboard now works both locally and via domain. 🎉

