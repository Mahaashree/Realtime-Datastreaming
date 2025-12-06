# Quick Start: Distributed Deployment

Quick reference for deploying the system across multiple VPS instances with Cloudflare Tunnels.

## Prerequisites Checklist

- [ ] Two VPS instances (Linux)
- [ ] Cloudflare account with Zero Trust enabled
- [ ] Domain `secruin.cloud` configured in Cloudflare
- [ ] SSH access to both VPS instances
- [ ] Multiple laptops/devices for device simulation

## Subdomain Plan

- `mqtt.secruin.cloud` → MQTT Broker (VPS 1)
- `influxdb.secruin.cloud` → InfluxDB (VPS 1)
- `dashboard.secruin.cloud` → Flask Dashboard (VPS 2)

## VPS 1: Docker Services (5 minutes)

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh

# 2. Clone repo
git clone <repo-url> && cd Realtime-Datastreaming

# 3. Start services
cd docker && docker-compose up -d

# 4. Install cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# 5. Create tunnel
cloudflared tunnel login
cloudflared tunnel create docker-services

# 6. Configure in Cloudflare Dashboard:
#    Networks → Tunnels → docker-services → Configure
#    - mqtt.secruin.cloud → tcp://localhost:1883 (TCP)
#    - influxdb.secruin.cloud → http://localhost:8086 (HTTP)

# 7. Run tunnel
cloudflared tunnel run docker-services
```

## VPS 2: Flask Dashboard (5 minutes)

```bash
# 1. Install Python & UV
sudo apt update && sudo apt install python3.11 python3.11-venv -y
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env

# 2. Clone repo
git clone <repo-url> && cd Realtime-Datastreaming

# 3. Setup Python environment
uv venv && source venv/bin/activate
uv pip install -r requirements.txt

# 4. Configure environment
cp env.example.vps2-flask .env
nano .env  # Update MQTT_BROKER_HOST=mqtt.secruin.cloud
           # Update INFLUXDB_URL=http://influxdb.secruin.cloud:8086

# 5. Install cloudflared (same as VPS 1)
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# 6. Create tunnel
cloudflared tunnel login
cloudflared tunnel create flask-dashboard

# 7. Configure in Cloudflare Dashboard:
#    Networks → Tunnels → flask-dashboard → Configure
#    - dashboard.secruin.cloud → http://localhost:5000 (HTTP)

# 8. Start Flask (in screen/tmux)
screen -S flask
source venv/bin/activate
python dashboard/app.py
# Press Ctrl+A then D to detach

# 9. Run tunnel
cloudflared tunnel run flask-dashboard
```

## Devices: Simulators (2 minutes per device)

```bash
# On each laptop/device

# 1. Clone repo
git clone <repo-url> && cd Realtime-Datastreaming

# 2. Setup Python environment
uv venv && source venv/bin/activate  # Windows: .\venv\Scripts\activate
uv pip install -r requirements.txt

# 3. Configure environment
cp env.example.device .env
# Edit .env: MQTT_BROKER_HOST=mqtt.secruin.cloud

# 4. Run device simulator
python devices/device_simulator.py vehicle_01
# Use different IDs: vehicle_02, vehicle_03, etc.
```

## Verify Everything Works

1. **Check MQTT**: From any device, test connection:
   ```bash
   mosquitto_pub -h mqtt.secruin.cloud -p 1883 -t test -m "hello"
   ```

2. **Check Dashboard**: Open https://dashboard.secruin.cloud

3. **Check Devices**: Verify devices show "connected" in logs

4. **Check Data Flow**: Dashboard should show real-time speed data

## Common Issues

**MQTT connection fails:**
- Verify tunnel is running: `cloudflared tunnel info docker-services`
- Check tunnel logs
- Verify subdomain DNS is configured

**Dashboard shows no data:**
- Verify Flask app is running
- Check Flask logs for errors
- Verify InfluxDB connection in Flask logs
- Ensure MQTT collector is running (optional, can run on either VPS)

**Devices can't connect:**
- Verify `.env` file has correct `MQTT_BROKER_HOST`
- Test MQTT connection manually
- Check device logs for connection errors

## Next Steps

- Set up systemd services for auto-start (see DEPLOYMENT.md)
- Configure security (MQTT auth, InfluxDB tokens)
- Set up monitoring and alerts
- Configure backups for InfluxDB

For detailed instructions, see [DEPLOYMENT.md](DEPLOYMENT.md).

