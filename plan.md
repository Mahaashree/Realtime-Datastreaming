# Real-time Data Streaming PoC Implementation Plan

## Architecture Overview

1. **Device Simulators** (10 Python scripts) - Generate vehicle speed data with randomness
2. **MQTT Broker** - Docker Mosquitto for message routing
3. **Data Collector** - MQTT subscriber that stores data in InfluxDB
4. **Flask Dashboard** - Web interface with real-time updates via WebSockets
5. **Offline Queue** - Local file-based queue for each device to handle network failures

## Tech Stack

- **Python 3.11+** with `uv` for package management
- **paho-mqtt** - MQTT client library with persistent sessions
- **InfluxDB** - Time-series database
- **Flask + Flask-SocketIO** - Web dashboard with WebSocket support
- **Docker** - Mosquitto MQTT broker
- **JSON** - Data serialization
- **SQLite** - Local offline queue storage (per device)

## Project Structure

```
Realtime-Datastreaming/
├── devices/
│   ├── device_simulator.py      # Base device simulator with offline queue
│   └── run_devices.py           # Script to run 10 device instances
├── collector/
│   └── mqtt_collector.py        # MQTT subscriber → InfluxDB writer
├── dashboard/
│   ├── app.py                   # Flask app with SocketIO
│   ├── templates/
│   │   └── dashboard.html       # Frontend with charts and device status
│   └── static/
│       └── js/
│           └── dashboard.js     # WebSocket client and Chart.js
├── docker/
│   └── docker-compose.yml       # Mosquitto + InfluxDB setup
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variables template
└── README.md                    # Setup and usage instructions
```

## Implementation Details

### 1. Device Simulator (`devices/device_simulator.py`)

- Generate realistic vehicle speed data (0-120 km/h with acceleration/deceleration patterns)
- Implement offline queue using SQLite (store messages when MQTT disconnected)
- Use MQTT QoS 1 (at least once delivery) with persistent sessions
- Automatic reconnection with queue flush on reconnect
- Configurable device ID, publish interval, and MQTT broker settings

### 2. MQTT Collector (`collector/mqtt_collector.py`)

- Subscribe to `vehicle/speed/{device_id}` topics
- Parse JSON messages and write to InfluxDB
- Track device connection status
- Handle duplicate messages (idempotency)

### 3. Flask Dashboard (`dashboard/app.py`)

- REST API endpoints for historical data queries
- WebSocket endpoint for real-time data push
- Device status endpoint (last seen, connection state)
- Serve static dashboard page

### 4. Dashboard Frontend (`dashboard/templates/dashboard.html`)

- Chart.js for speed visualization (10 line charts, one per device)
- Real-time updates via SocketIO
- Device status indicators (online/offline)
- Data table showing latest speeds

### 5. Docker Setup (`docker/docker-compose.yml`)

- Mosquitto MQTT broker (port 1883)
- InfluxDB (port 8086) with default bucket
- Network configuration for service communication

### 6. Offline Queue Mechanism

- Each device maintains local SQLite database
- Store messages with timestamp when MQTT disconnected
- On reconnection, publish queued messages in order
- Limit queue size to prevent disk overflow

## Key Features

- **Network Failure Handling**: SQLite-based offline queue per device
- **QoS 1**: MQTT quality of service for message delivery guarantees
- **Persistent Sessions**: MQTT clean_session=False for message retention
- **Real-time Dashboard**: WebSocket updates every second
- **Device Monitoring**: Track connection status and last seen timestamps

## Environment Variables

- `MQTT_BROKER_HOST` - MQTT broker address (default: localhost)
- `MQTT_BROKER_PORT` - MQTT broker port (default: 1883)
- `INFLUXDB_URL` - InfluxDB connection URL
- `INFLUXDB_TOKEN` - InfluxDB authentication token
- `INFLUXDB_ORG` - InfluxDB organization
- `INFLUXDB_BUCKET` - InfluxDB bucket name

## Testing Strategy

1. Run all 10 devices and verify data flow
2. Disconnect network on one device, generate data, reconnect
3. Verify queued messages are transmitted
4. Check InfluxDB for data persistence
5. Test dashboard real-time updates
6. Simulate broker restart and verify reconnection