# Cloudflare Tunnel Configuration

This directory contains example configurations for Cloudflare Tunnels used in distributed deployment.

## Files

- `tunnel-config.example.yml` - Configuration for VPS Instance 1 (Docker services: MQTT + InfluxDB)
- `tunnel-config-flask.example.yml` - Configuration for VPS Instance 2 (Flask dashboard)

## Quick Setup

### VPS Instance 1 (Docker Services)

1. Install cloudflared:
   ```bash
   wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   sudo dpkg -i cloudflared-linux-amd64.deb
   ```

2. Login and create tunnel:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create docker-services
   ```

3. Configure routes in Cloudflare Zero Trust Dashboard:
   - Go to **Networks** → **Tunnels** → **docker-services** → **Configure**
   - Add public hostname: `mqtt.secruin.cloud` → `tcp://localhost:1883` (TCP)
   - Add public hostname: `influxdb.secruin.cloud` → `http://localhost:8086` (HTTP)

4. Run tunnel:
   ```bash
   cloudflared tunnel run docker-services
   ```

### VPS Instance 2 (Flask Dashboard)

1. Install cloudflared (same as above)

2. Create tunnel:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create flask-dashboard
   ```

3. Configure route in Cloudflare Zero Trust Dashboard:
   - Go to **Networks** → **Tunnels** → **flask-dashboard** → **Configure**
   - Add public hostname: `dashboard.secruin.cloud` → `http://localhost:5000` (HTTP)

4. Run tunnel:
   ```bash
   cloudflared tunnel run flask-dashboard
   ```

## Subdomain Setup

Ensure these subdomains are configured in Cloudflare DNS:
- `mqtt.secruin.cloud` (CNAME to tunnel)
- `influxdb.secruin.cloud` (CNAME to tunnel)
- `dashboard.secruin.cloud` (CNAME to tunnel)

Cloudflare will automatically create these when you configure the tunnel routes.

## Security Notes

- Consider enabling Cloudflare Access policies for additional security
- Use strong authentication tokens for InfluxDB
- Enable MQTT authentication in production (see DEPLOYMENT.md)
- Use HTTPS/TLS where possible

For detailed deployment instructions, see [../DEPLOYMENT.md](../DEPLOYMENT.md).

