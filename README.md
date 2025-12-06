# Real-time Data Streaming PoC

A proof-of-concept implementation for real-time vehicle speed data streaming using MQTT, InfluxDB, and Flask. This system simulates 10 vehicle devices that continuously send speed data, handles network failures with offline queuing, and provides a real-time dashboard for visualization.

## Architecture

- **10 Device Simulators**: Python scripts that generate realistic vehicle speed data
- **MQTT Broker**: Mosquitto broker for message routing (Docker)
- **Data Collector**: MQTT subscriber that stores data in InfluxDB
- **Flask Dashboard**: Web interface with real-time updates via WebSockets
- **Offline Queue**: SQLite-based queue per device for network failure handling

### Deployment Options

- **Local Development**: Run all components on a single machine (see Setup Instructions below)
- **Distributed Deployment**: Deploy across multiple VPS instances using Cloudflare Tunnels (see [DEPLOYMENT.md](DEPLOYMENT.md))
- **Telegraf Integration**: Use Telegraf for production-grade data collection (see [TELEGRAF.md](TELEGRAF.md))

## Tech Stack

- **Python 3.11+** with `uv` for package management
- **paho-mqtt**: MQTT client library with persistent sessions
- **InfluxDB**: Time-series database
- **Flask + Flask-SocketIO**: Web dashboard with WebSocket support
- **Docker**: Mosquitto MQTT broker and InfluxDB
- **SQLite**: Local offline queue storage (per device)
- **Telegraf** (Optional): Production-grade metrics collector (see [TELEGRAF.md](TELEGRAF.md))

## Prerequisites

- Python 3.11 or higher
- [UV](https://github.com/astral-sh/uv) package manager
- Docker and Docker Compose
- Git

## Setup Instructions

### 1. Clone the Repository

```bash
git clone <repository-url>
cd Realtime-Datastreaming
```

### 2. Set Up Python Environment with UV

```bash
# Create virtual environment
uv venv

# Activate virtual environment
# On Windows:
.\venv\Scripts\activate
# On Linux/Mac:
source venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt
```

### 3. Configure Environment Variables

Copy the example environment file and update with your settings:

```bash
# On Windows:
copy env.example .env
# On Linux/Mac:
cp env.example .env
```

Edit `.env` file with your configuration (defaults should work for local development):

```env
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
INFLUXDB_URL=http://localhost:8086
INFLUXDB_TOKEN=my-super-secret-auth-token
INFLUXDB_ORG=my-org
INFLUXDB_BUCKET=vehicle-data
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
```

### 4. Start Docker Services

Start the MQTT broker and InfluxDB using Docker Compose:

```bash
cd docker
docker-compose up -d
```

This will start:
- **Mosquitto MQTT Broker** on port 1883
- **InfluxDB** on port 8086

Verify services are running:

```bash
docker-compose ps
```

### 5. Initialize InfluxDB (First Time Only)

1. Open http://localhost:8086 in your browser
2. Login with:
   - Username: `admin`
   - Password: `adminpassword`
3. Complete the setup wizard (or use the default token from `.env`)

## Usage

### Running the System

You need to run three components:

#### 1. Start the MQTT Collector

In one terminal:

```bash
python collector/mqtt_collector.py
```

This will:
- Connect to the MQTT broker
- Subscribe to vehicle speed topics
- Store data in InfluxDB

#### 2. Start Device Simulators

In another terminal:

```bash
python devices/run_devices.py
```

This will start 10 device simulators (vehicle_01 through vehicle_10) that:
- Generate realistic vehicle speed data
- Publish to MQTT broker
- Queue messages when offline

Alternatively, run a single device:

```bash
python devices/device_simulator.py vehicle_01
```

#### 3. Start the Flask Dashboard

In a third terminal:

```bash
python dashboard/app.py
```

Then open http://localhost:5000 in your browser to view the real-time dashboard.

## Testing Network Failure Handling

To test the offline queue mechanism:

1. Start all components (collector, devices, dashboard)
2. Verify data is flowing normally
3. Stop the MQTT broker: `docker-compose stop mosquitto` (in docker directory)
4. Let devices generate data for a few seconds (they will queue messages)
5. Restart the broker: `docker-compose start mosquitto`
6. Observe that queued messages are automatically transmitted

You can also check the queue files in `devices/queues/` directory.

## Project Structure

```
Realtime-Datastreaming/
├── devices/
│   ├── device_simulator.py      # Base device simulator with offline queue
│   ├── run_devices.py           # Script to run 10 device instances
│   └── queues/                  # SQLite queue databases (created at runtime)
├── collector/
│   └── mqtt_collector.py        # MQTT subscriber → InfluxDB writer
├── dashboard/
│   ├── app.py                   # Flask app with SocketIO
│   └── templates/
│       └── dashboard.html       # Frontend with charts and device status
├── docker/
│   ├── docker-compose.yml       # Mosquitto + InfluxDB setup
│   └── mosquitto/
│       └── config/
│           └── mosquitto.conf   # Mosquitto configuration
├── requirements.txt             # Python dependencies
├── env.example                  # Environment variables template (local dev)
├── env.example.vps1-docker      # Environment for VPS 1 (Docker services)
├── env.example.vps2-flask       # Environment for VPS 2 (Flask dashboard)
├── env.example.device           # Environment for device simulators
├── cloudflare/                  # Cloudflare tunnel configurations
│   ├── tunnel-config.example.yml
│   └── tunnel-config-flask.example.yml
├── DEPLOYMENT.md                # Distributed deployment guide
└── README.md                    # This file
```

## Key Features

### Offline Queue Mechanism

- Each device maintains a local SQLite database for queuing messages
- Messages are automatically queued when MQTT connection is lost
- On reconnection, queued messages are published in order
- Queue size is limited to prevent disk overflow (default: 10,000 messages)

### MQTT Quality of Service

- Uses QoS 1 (at least once delivery) for reliable message delivery
- Persistent sessions (`clean_session=False`) for message retention
- Automatic reconnection with exponential backoff

### Real-time Dashboard

- WebSocket-based real-time updates (every second)
- 10 individual charts showing speed over time
- Device status indicators (online/offline)
- Latest speed data table
- Connection status indicator

### Vehicle Speed Simulation

- Realistic acceleration/deceleration patterns
- Random target speed changes
- Gaussian noise for realistic variation
- Speed range: 0-120 km/h

## API Endpoints

- `GET /` - Dashboard page
- `GET /api/devices/status` - Get status of all devices
- `GET /api/devices/<device_id>/latest` - Get latest speed for a device
- `GET /api/devices/<device_id>/history?duration=5m` - Get historical data

## WebSocket Events

- `latest_data` - Broadcasts latest speed data for all devices (every second)

## Troubleshooting

### MQTT Connection Issues

- Verify Mosquitto is running: `docker-compose ps` (in docker directory)
- Check broker logs: `docker-compose logs mosquitto`
- Ensure port 1883 is not blocked by firewall

### InfluxDB Connection Issues

- Verify InfluxDB is running: `docker-compose ps`
- Check InfluxDB logs: `docker-compose logs influxdb`
- Verify token and organization match `.env` file
- Access InfluxDB UI at http://localhost:8086

### Device Queue Issues

- Check queue files in `devices/queues/` directory
- Queue databases are SQLite files (can be inspected with SQLite tools)
- If queue grows too large, delete the queue file to reset (device will start fresh)

### Dashboard Not Updating

- Check browser console for WebSocket errors
- Verify Flask app is running and SocketIO is connected
- Check collector is running and writing to InfluxDB

## Development

### Running Tests

Manual testing procedure:

1. **Test Normal Operation**: Run all components and verify data flows
2. **Test Network Failure**: Stop broker, generate data, restart broker
3. **Test Queue Recovery**: Verify queued messages are transmitted
4. **Test Dashboard**: Verify real-time updates and device status

### Modifying Device Behavior

Edit `devices/device_simulator.py`:
- `VehicleSpeedSimulator` class for speed generation logic
- `DeviceSimulator` class for MQTT publishing behavior
- `OfflineQueue` class for queue management

### Modifying Dashboard

Edit `dashboard/templates/dashboard.html` for frontend changes.
Edit `dashboard/app.py` for backend API and WebSocket handlers.

## License

This is a proof-of-concept project for demonstration purposes.

## Optional: Telegraf Integration

For production deployments, consider using **Telegraf** instead of the Python collector:

### Benefits
- ✅ **10x better performance** (~10,000+ msg/s vs ~1,000 msg/s)
- ✅ **System metrics**: CPU, memory, disk, network automatically collected
- ✅ **Docker metrics**: Container statistics
- ✅ **Production-ready**: Battle-tested reliability
- ✅ **Configuration-based**: No code changes needed

### Quick Start
```bash
# Use Telegraf-enabled docker-compose
cd docker
docker-compose -f docker-compose.with-telegraf.yml up -d
```

See [TELEGRAF.md](TELEGRAF.md) for complete setup and benefits.

## Future Enhancements

- Add authentication/authorization
- Implement data retention policies
- Add alerting for offline devices
- Support for more device types
- Historical data analysis features
- Export data functionality
- Multi-broker support for scalability

