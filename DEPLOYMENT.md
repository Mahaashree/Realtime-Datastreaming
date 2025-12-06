scree# Distributed Deployment Guide

This guide covers deploying the Real-time Data Streaming PoC across multiple VPS instances and devices using Cloudflare Tunnels.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│ VPS Instance 1 (Docker Services)                           │
│ ┌──────────────┐  ┌──────────────┐                        │
│ │  Mosquitto   │  │   InfluxDB   │                        │
│ │   (MQTT)     │  │  (Database)  │                        │
│ └──────┬───────┘  └──────┬───────┘                        │
│        │                  │                                 │
│        └──────────┬───────┘                                 │
│                   │                                         │
│         Cloudflare Tunnel                                   │
│         ├─ mqtt.secruin.cloud                              │
│         └─ influxdb.secruin.cloud                          │
└─────────────────────────────────────────────────────────────┘
                            │
                            │
┌───────────────────────────┼─────────────────────────────────┐
│                           │                                 │
│ VPS Instance 2           │   Multiple Laptops/Devices      │
│ ┌──────────────┐         │   ┌──────────────┐              │
│ │ Flask        │         │   │ Device       │              │
│ │ Dashboard    │         │   │ Simulators   │              │
│ └──────┬───────┘         │   │ (10+)        │              │
│        │                 │   └──────┬───────┘              │
│        │                 │          │                       │
│ Cloudflare Tunnel        │          │                       │
│ └─ dashboard.secruin.cloud│         │                       │
│                           │          │                       │
└───────────────────────────┴──────────┴───────────────────────┘
```

## Prerequisites

- Two VPS instances (Linux recommended)
- Cloudflare account with Zero Trust enabled
- Domain: `secruin.cloud` configured in Cloudflare
- Multiple laptops/devices for device simulation
- SSH access to all VPS instances

## Step 1: Set Up VPS Instance 1 (Docker Services)

### 1.1 Install Dependencies

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker and Docker Compose
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Logout and login again for docker group to take effect
```

### 1.2 Clone Repository and Configure

```bash
# Clone repository
git clone <repository-url>
cd Realtime-Datastreaming

# Copy environment file
cp env.example.vps1-docker .env

# Edit .env with your InfluxDB token (generate a secure token)
nano .env
```

### 1.3 Start Docker Services

```bash
cd docker
docker-compose up -d

# Verify services are running
docker-compose ps
docker-compose logs mosquitto
docker-compose logs influxdb
```

### 1.4 Configure InfluxDB (First Time)

1. Access InfluxDB UI via SSH tunnel temporarily:
   ```bash
   # On your local machine
   ssh -L 8086:localhost:8086 user@vps1-ip
   ```
2. Open http://localhost:8086 in your browser
3. Complete setup and note your token
4. Update `.env` file with the token

### 1.5 Install and Configure Cloudflare Tunnel

```bash
# Install cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# Login to Cloudflare
cloudflared tunnel login

# Create tunnel for Docker services
cloudflared tunnel create docker-services

# Copy tunnel ID (you'll need this)
cloudflared tunnel list
```

### 1.6 Configure Tunnel Routes

In Cloudflare Zero Trust Dashboard:

1. Go to **Networks** → **Tunnels**
2. Click on your tunnel → **Configure**
3. Add public hostname routes:
   - **Subdomain**: `mqtt`
   - **Domain**: `secruin.cloud`
   - **Service**: `tcp://localhost:1883`
   - **Type**: TCP
   
   - **Subdomain**: `influxdb`
   - **Domain**: `secruin.cloud`
   - **Service**: `http://localhost:8086`
   - **Type**: HTTP

### 1.7 Start Cloudflare Tunnel

```bash
# Create systemd service for tunnel
sudo cloudflared service install

# Edit the service file
sudo nano /etc/cloudflared/config.yml

# Add configuration (or use the example from cloudflare/tunnel-config.example.yml)
```

Or run manually:
```bash
cloudflared tunnel run docker-services
```

### 1.8 Verify Services

Test MQTT connection:
```bash
# Install mosquitto clients
sudo apt install mosquitto-clients -y

# Test publish (from VPS1)
mosquitto_pub -h mqtt.secruin.cloud -p 1883 -t test/topic -m "Hello"

# Test subscribe (from another machine)
mosquitto_sub -h mqtt.secruin.cloud -p 1883 -t test/topic
```

## Step 2: Set Up VPS Instance 2 (Flask Dashboard)

### 2.1 Install Dependencies

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11+
sudo apt install python3.11 python3.11-venv python3-pip -y

# Install UV
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env

# Install Nginx (optional, for reverse proxy)
sudo apt install nginx -y
```

### 2.2 Clone Repository and Configure

```bash
# Clone repository
git clone <repository-url>
cd Realtime-Datastreaming

# Set up Python environment
uv venv
source venv/bin/activate
uv pip install -r requirements.txt

# Copy environment file
cp env.example.vps2-flask .env

# Edit .env with your configuration
nano .env
# Update:
# - MQTT_BROKER_HOST=mqtt.secruin.cloud
# - INFLUXDB_URL=http://influxdb.secruin.cloud:8086
# - INFLUXDB_TOKEN=<your-token>
```

### 2.3 Install and Configure Cloudflare Tunnel

```bash
# Install cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# Login to Cloudflare
cloudflared tunnel login

# Create tunnel for Flask dashboard
cloudflared tunnel create flask-dashboard

# Note the tunnel ID
cloudflared tunnel list
```

### 2.4 Configure Tunnel Route

In Cloudflare Zero Trust Dashboard:

1. Go to **Networks** → **Tunnels**
2. Click on your Flask tunnel → **Configure**
3. Add public hostname route:
   - **Subdomain**: `dashboard`
   - **Domain**: `secruin.cloud`
   - **Service**: `http://localhost:5000`
   - **Type**: HTTP

### 2.5 Start Flask Dashboard

```bash
# Activate virtual environment
source venv/bin/activate

# Start Flask app (consider using systemd or screen/tmux)
python dashboard/app.py

# Or run in background with screen
screen -S flask
python dashboard/app.py
# Press Ctrl+A then D to detach
```

### 2.6 Start Cloudflare Tunnel

```bash
# Run tunnel
cloudflared tunnel run flask-dashboard

# Or set up as systemd service (recommended)
sudo cloudflared service install
```

### 2.7 Verify Dashboard

Open https://dashboard.secruin.cloud in your browser. You should see the dashboard (may be empty until devices start sending data).

## Step 3: Set Up Device Simulators

### 3.1 On Each Laptop/Device

```bash
# Clone repository
git clone <repository-url>
cd Realtime-Datastreaming

# Set up Python environment
uv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate
uv pip install -r requirements.txt

# Copy device environment file
cp env.example.device .env

# Edit .env
nano .env  # or notepad .env on Windows
# Set: MQTT_BROKER_HOST=mqtt.secruin.cloud
```

### 3.2 Run Device Simulators

**Option 1: Run single device**
```bash
python devices/device_simulator.py vehicle_01
```

**Option 2: Run multiple devices (10 devices)**
```bash
python devices/run_devices.py
```

**Option 3: Run devices on different machines**
- Each machine runs: `python devices/device_simulator.py vehicle_XX`
- Use different device IDs (vehicle_01, vehicle_02, etc.)

### 3.3 Verify Device Connection

Check logs to ensure devices connect to MQTT broker:
```bash
# You should see connection messages
# Device vehicle_01 connected to MQTT broker
```

## Step 4: Run MQTT Collector (Optional)

The collector can run on either VPS:

**On VPS Instance 1 (with Docker):**
```bash
cd Realtime-Datastreaming
source venv/bin/activate
python collector/mqtt_collector.py
```

**On VPS Instance 2 (with Flask):**
```bash
# Already configured, just run
python collector/mqtt_collector.py
```

## Step 5: Verify Complete System

1. **Check Device Simulators**: All devices should show "connected" in logs
2. **Check MQTT Collector**: Should show "Processed X messages"
3. **Check Dashboard**: https://dashboard.secruin.cloud should show real-time data
4. **Check InfluxDB**: Query data via InfluxDB UI or CLI

## Security Considerations

### 1. MQTT Security

**Current Setup**: MQTT is exposed publicly. For production:

- Enable authentication in Mosquitto
- Use TLS/SSL (configure in `docker/mosquitto/config/mosquitto.conf`)
- Restrict access via Cloudflare Access policies

**Add to `docker/mosquitto/config/mosquitto.conf`:**
```
listener 1883
allow_anonymous false
password_file /mosquitto/config/passwd

listener 8883
certfile /mosquitto/config/certs/server.crt
cafile /mosquitto/config/certs/ca.crt
keyfile /mosquitto/config/certs/server.key
```

### 2. InfluxDB Security

- Use strong authentication tokens
- Enable HTTPS (configure in InfluxDB)
- Restrict access via Cloudflare Access policies
- Consider IP whitelisting

### 3. Flask Dashboard Security

- Add authentication (Flask-Login)
- Use HTTPS (Cloudflare handles this)
- Implement rate limiting
- Add CSRF protection

### 4. Cloudflare Access (Recommended)

Set up Cloudflare Access policies to restrict:
- InfluxDB UI access
- MQTT broker access (if possible)
- Dashboard access (optional)

## Troubleshooting

### MQTT Connection Issues

```bash
# Test MQTT connection from device
mosquitto_pub -h mqtt.secruin.cloud -p 1883 -t test -m "test"

# Check tunnel logs
cloudflared tunnel info docker-services

# Check Mosquitto logs
docker-compose -f docker/docker-compose.yml logs mosquitto
```

### InfluxDB Connection Issues

```bash
# Test InfluxDB connection
curl http://influxdb.secruin.cloud:8086/health

# Check tunnel status
cloudflared tunnel info docker-services

# Check InfluxDB logs
docker-compose -f docker/docker-compose.yml logs influxdb
```

### Flask Dashboard Issues

```bash
# Check Flask logs
# View running process logs

# Test local connection
curl http://localhost:5000

# Check tunnel status
cloudflared tunnel info flask-dashboard
```

### Device Connection Issues

- Verify `.env` file has correct `MQTT_BROKER_HOST`
- Check firewall rules on device
- Verify Cloudflare tunnel is running
- Check device logs for connection errors

## Monitoring

### Systemd Services (Recommended)

Create systemd services for:
- Cloudflare tunnels (on both VPS)
- Flask dashboard (on VPS 2)
- MQTT collector (optional)

Example Flask service (`/etc/systemd/system/flask-dashboard.service`):
```ini
[Unit]
Description=Flask Dashboard
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/Realtime-Datastreaming
Environment="PATH=/path/to/Realtime-Datastreaming/venv/bin"
ExecStart=/path/to/Realtime-Datastreaming/venv/bin/python dashboard/app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable flask-dashboard
sudo systemctl start flask-dashboard
sudo systemctl status flask-dashboard
```

## Maintenance

### Update Services

```bash
# On VPS 1 (Docker)
cd Realtime-Datastreaming/docker
docker-compose pull
docker-compose up -d

# On VPS 2 (Flask)
cd Realtime-Datastreaming
git pull
source venv/bin/activate
uv pip install -r requirements.txt
sudo systemctl restart flask-dashboard
```

### Backup InfluxDB

```bash
# Export data
docker exec influxdb influx backup /var/lib/influxdb2/backup

# Copy backup from container
docker cp influxdb:/var/lib/influxdb2/backup ./backup-$(date +%Y%m%d)
```

## Performance Optimization

- Use MQTT QoS 0 for high-frequency data (if acceptable)
- Adjust InfluxDB retention policies
- Implement data aggregation
- Use connection pooling for InfluxDB
- Enable compression in Cloudflare tunnel

