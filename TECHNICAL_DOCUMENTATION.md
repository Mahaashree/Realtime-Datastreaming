# Real-Time Data Streaming System - Technical Documentation

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Architecture](#system-architecture)
3. [Data Flow](#data-flow)
4. [Component Details](#component-details)
5. [Configuration](#configuration)
6. [Deployment](#deployment)
7. [Performance Characteristics](#performance-characteristics)
8. [Monitoring & Observability](#monitoring--observability)
9. [Security](#security)
10. [Troubleshooting](#troubleshooting)
11. [Development Guide](#development-guide)

---

## Executive Summary

The Real-Time Data Streaming system is a proof-of-concept implementation for collecting, processing, and visualizing vehicle telemetry data in real-time. The system uses MQTT for message routing, InfluxDB for time-series data storage, and Flask with WebSockets for real-time visualization.

### Key Features

- **Real-time data streaming** from multiple vehicle device simulators
- **Offline queue mechanism** for handling network failures
- **MQTT-based pub/sub architecture** with QoS guarantees
- **Time-series data storage** in InfluxDB
- **Real-time dashboard** with WebSocket updates
- **Comprehensive monitoring** and latency tracking
- **Distributed deployment** support via Cloudflare Tunnels
- **Production-ready options** with Telegraf integration

### Technology Stack

- **Language**: Python 3.11+
- **Message Broker**: Mosquitto MQTT (Docker)
- **Database**: InfluxDB 2.x (Docker)
- **Web Framework**: Flask + Flask-SocketIO
- **Package Manager**: UV
- **Containerization**: Docker & Docker Compose
- **Monitoring**: Custom Python scripts + Telegraf (optional)
- **Deployment**: Cloudflare Tunnels for distributed access

---

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Device Layer                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Device 1 │  │ Device 2 │  │ Device 3 │  │ Device N │       │
│  │(vehicle_01)│(vehicle_02)│(vehicle_03)│(vehicle_N)│       │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘  └─────┬────┘       │
│        │              │              │              │            │
│        └──────────────┴──────────────┴──────────────┘            │
│                          │                                         │
│                    MQTT Protocol                                   │
│                    (QoS 1, TLS)                                    │
└──────────────────────────┼─────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Message Broker Layer                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Mosquitto MQTT Broker                      │   │
│  │  - Port: 1883 (TCP) / 8883 (TLS)                        │   │
│  │  - Topics: device/data/+, vehicle/speed/+                │   │
│  │  - QoS: 1 (At least once delivery)                       │   │
│  │  - Persistent sessions enabled                            │   │
│  └───────────────────────┬──────────────────────────────────┘   │
└──────────────────────────┼─────────────────────────────────────────┘
                           │
        ┌──────────────────┴──────────────────┐
        │                                      │
        ▼                                      ▼
┌──────────────────┐                  ┌──────────────────┐
│  Data Collector  │                  │   Telegraf        │
│  (Python)        │                  │   (Optional)      │
│  - MQTT Sub      │                  │   - MQTT Input    │
│  - InfluxDB Write│                  │   - System Metrics│
└────────┬─────────┘                  └────────┬─────────┘
         │                                      │
         └──────────────┬───────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Data Storage Layer                           │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    InfluxDB 2.x                          │   │
│  │  - Port: 8086                                             │   │
│  │  - Bucket: vehicle-data                                   │   │
│  │  - Measurement: device_data                               │   │
│  │  - Fields: speed, publish_timestamp, collector_receive_time│  │
│  │  - Tags: device_id, collector                             │   │
│  └───────────────────────┬──────────────────────────────────┘   │
└──────────────────────────┼─────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Visualization Layer                           │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Flask Dashboard                               │   │
│  │  - REST API: /api/devices/status, /api/health              │   │
│  │  - WebSocket: Real-time data push                          │   │
│  │  - Frontend: Chart.js visualizations                       │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Component Interaction Flow

1. **Device Simulators** generate vehicle telemetry data (speed, CPU, memory, etc.)
2. **MQTT Broker** receives and routes messages to subscribers
3. **Collector(s)** subscribe to MQTT topics and write to InfluxDB
4. **InfluxDB** stores time-series data with timestamps
5. **Dashboard** queries InfluxDB and pushes updates via WebSocket
6. **Monitoring System** tracks latency and performance metrics

---

## Data Flow

### End-to-End Data Flow

```
Device → MQTT → Collector → InfluxDB → Dashboard
  │        │        │          │          │
  │        │        │          │          └─ WebSocket Push
  │        │        │          └─ Query API
  │        │        └─ Batch Write (250 msgs, 500ms flush)
  │        └─ QoS 1, Persistent Session
  └─ Offline Queue (SQLite) if disconnected
```

### Message Format

#### Device → MQTT

```json
{
  "device_id": "vehicle_01",
  "timestamp": 1704204000.123,
  "speed": 65.5,
  "cpu_usage": 45.2,
  "ram_usage": 60.1,
  "memory_total": 8192,
  "memory_used": 4915,
  "memory_available": 3277,
  "memory_percent": 60.0,
  "disk_total": 500000,
  "disk_used": 250000,
  "disk_free": 250000,
  "disk_percent": 50.0,
  "network_bytes_sent": 1000000,
  "network_bytes_recv": 2000000,
  "detection_label": "vehicle",
  "detection_confidence": 0.95
}
```

#### MQTT → InfluxDB (via Collector)

**Measurement**: `device_data`

**Tags**:
- `device_id`: Vehicle identifier
- `collector`: Collector type (python/telegraf)
- `detection_label`: Object detection label (if present)

**Fields**:
- `speed`: Vehicle speed (float)
- `publish_timestamp`: Device timestamp (float, Unix epoch)
- `collector_receive_time`: Collector receive time (float)
- `cpu_usage`: CPU usage percentage (float)
- `ram_usage`: RAM usage percentage (float)
- `memory_total`, `memory_used`, `memory_available`, `memory_percent`: Memory metrics
- `disk_total`, `disk_used`, `disk_free`, `disk_percent`: Disk metrics
- `network_bytes_sent`, `network_bytes_recv`: Network metrics
- `detection_confidence`: Detection confidence score (float)

**Timestamp**: InfluxDB write time (`_time`)

### Latency Calculation

```
End-to-End Latency = InfluxDB_write_time - Device_publish_timestamp
```

- **Target**: P95 latency < 2000ms
- **Measurement**: Time from device publish to InfluxDB write
- **Tracking**: `publish_timestamp` field preserved from device payload

---

## Component Details

### 1. Device Simulators (`devices/`)

#### Purpose
Simulate vehicle devices that generate and publish telemetry data via MQTT.

#### Key Components

**`device_simulator.py`**:
- `VehicleSpeedSimulator`: Generates realistic speed data (0-120 km/h)
- `DeviceSimulator`: Main device class with MQTT publishing
- `OfflineQueue`: SQLite-based queue for offline message storage
- `DeviceTelemetry`: System metrics collection (CPU, memory, disk, network)

**`run_devices.py`**:
- Manages multiple device instances
- Process monitoring and auto-restart
- Status reporting

#### Features

1. **Realistic Speed Simulation**:
   - Acceleration/deceleration patterns
   - Random target speed changes
   - Gaussian noise for variation
   - Speed range: 0-120 km/h

2. **Offline Queue**:
   - SQLite database per device (`devices/queues/{device_id}_queue.db`)
   - Automatic queuing when MQTT disconnected
   - Queue size limit: 10,000 messages (FIFO eviction)
   - Automatic flush on reconnection

3. **MQTT Configuration**:
   - QoS 1 (at least once delivery)
   - Persistent sessions (`clean_session=False`)
   - Automatic reconnection with exponential backoff
   - TLS support (optional)
   - Authentication support (optional)

4. **System Telemetry**:
   - CPU usage
   - RAM usage
   - Memory statistics (total, used, available, percent)
   - Disk statistics (total, used, free, percent)
   - Network statistics (bytes sent/received)

5. **Publish Topics**:
   - `device/data/{device_id}` (primary)
   - `vehicle/speed/{device_id}` (legacy)

#### Configuration

```python
# Environment variables
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
MQTT_USE_TLS=false
MQTT_TLS_INSECURE=false
MQTT_CA_CERTS=/path/to/ca.crt
MQTT_CERTFILE=/path/to/client.crt
MQTT_KEYFILE=/path/to/client.key
MQTT_USERNAME=username
MQTT_PASSWORD=password
```

#### Usage

```bash
# Single device
python devices/device_simulator.py vehicle_01

# Multiple devices (10 instances)
python devices/run_devices.py
```

---

### 2. MQTT Broker (`docker/mosquitto/`)

#### Purpose
Message broker for pub/sub communication between devices and collectors.

#### Configuration

**`docker/mosquitto/config/mosquitto.conf`**:
- Port 1883 (TCP)
- Port 8883 (TLS, if configured)
- Persistent storage
- Message retention
- Logging

#### Features

- **QoS Support**: QoS 0, 1, 2
- **Persistent Sessions**: Message retention for disconnected clients
- **TLS/SSL**: Optional encryption
- **Authentication**: Username/password or certificate-based
- **Wildcard Topics**: Support for `+` and `#` wildcards

#### Docker Setup

```yaml
# docker/docker-compose.yml
mosquitto:
  image: eclipse-mosquitto:2.0
  ports:
    - "1883:1883"
    - "8883:8883"
  volumes:
    - ./mosquitto/config:/mosquitto/config
    - ./mosquitto/data:/mosquitto/data
    - ./mosquitto/log:/mosquitto/log
```

---

### 3. Data Collector (`collector/`)

#### Purpose
Subscribe to MQTT messages and write to InfluxDB with batching and error handling.

#### Components

**`mqtt_collector.py`**:
- MQTT subscriber
- InfluxDB writer with batching
- Message parsing and transformation
- Error handling and retry logic

#### Features

1. **MQTT Subscription**:
   - Topics: `device/data/+`, `vehicle/speed/+`
   - QoS 1
   - Persistent session
   - Automatic reconnection

2. **InfluxDB Writing**:
   - Batched writes (250 messages per batch)
   - Flush interval: 500ms
   - Retry logic: 3 retries, 5s interval
   - Error handling and logging

3. **Data Transformation**:
   - Extract `device_id` from topic or payload
   - Preserve `publish_timestamp` for latency calculation
   - Record `collector_receive_time`
   - Handle nested and flat JSON formats
   - Type conversion (int, float, string)

4. **Performance**:
   - Throughput: ~1,000 messages/second
   - Memory: ~50-100 MB
   - CPU: Medium usage

#### Configuration

```python
# Batch write options
WriteOptions(
    batch_size=250,
    flush_interval=500,  # 0.5 seconds
    jitter_interval=0,
    retry_interval=5000,
    max_retries=3,
    max_retry_delay=30000
)
```

#### Usage

```bash
python collector/mqtt_collector.py
```

---

### 4. InfluxDB (`docker/influxdb/`)

#### Purpose
Time-series database for storing vehicle telemetry data.

#### Schema

**Bucket**: `vehicle-data`

**Measurement**: `device_data`

**Retention Policy**: Configurable (default: infinite)

**Data Model**:
- **Tags**: `device_id`, `collector`, `detection_label`
- **Fields**: `speed`, `publish_timestamp`, `collector_receive_time`, telemetry fields
- **Timestamp**: InfluxDB write time

#### Queries

**Latest data per device**:
```flux
from(bucket: "vehicle-data")
  |> range(start: -1h)
  |> filter(fn: (r) => r["_measurement"] == "device_data")
  |> filter(fn: (r) => r["_field"] == "speed")
  |> group(columns: ["device_id"])
  |> last()
```

**Latency calculation**:
```flux
from(bucket: "vehicle-data")
  |> range(start: -5m)
  |> filter(fn: (r) => r["_measurement"] == "device_data")
  |> filter(fn: (r) => r["_field"] == "publish_timestamp")
  |> keep(columns: ["_time", "_value", "device_id"])
```

#### Docker Setup

```yaml
influxdb:
  image: influxdb:2.7
  ports:
    - "8086:8086"
  volumes:
    - influxdb-data:/var/lib/influxdb2
  environment:
    - DOCKER_INFLUXDB_INIT_MODE=setup
    - DOCKER_INFLUXDB_INIT_USERNAME=admin
    - DOCKER_INFLUXDB_INIT_PASSWORD=adminpassword
    - DOCKER_INFLUXDB_INIT_ORG=my-org
    - DOCKER_INFLUXDB_INIT_BUCKET=vehicle-data
```

---

### 5. Dashboard (`dashboard/`)

#### Purpose
Web-based real-time visualization of vehicle data.

#### Components

**`app.py`**:
- Flask application
- REST API endpoints
- WebSocket server (Flask-SocketIO)
- InfluxDB query interface
- Automatic fallback between local and remote InfluxDB

**`templates/dashboard.html`**:
- Frontend UI with Chart.js
- Real-time WebSocket client
- Device status indicators
- Data tables

#### API Endpoints

**REST API**:
- `GET /`: Dashboard page
- `GET /api/health`: Health check with InfluxDB status
- `GET /api/devices/status`: Device status (online/offline, last seen)
- `GET /api/devices/<device_id>/latest`: Latest speed for device
- `GET /api/devices/<device_id>/history?duration=5m`: Historical data

**WebSocket Events**:
- `latest_data`: Broadcasts latest speed data for all devices (every 1 second)

#### Features

1. **Real-time Updates**:
   - WebSocket push every 1 second
   - Chart.js line charts (one per device)
   - Automatic reconnection

2. **Device Status**:
   - Online/offline detection (10-second timeout)
   - Last seen timestamp
   - Connection status indicator

3. **InfluxDB Fallback**:
   - Primary URL: `http://localhost:8086`
   - Fallback URL: `http://influxdb.secruin.cloud:8086`
   - Automatic switching on connection failure

#### Usage

```bash
python dashboard/app.py
# Access at http://localhost:5000
```

---

### 6. Monitoring System (`monitoring/`)

#### Purpose
Track and analyze system performance, particularly end-to-end latency.

#### Components

**`collect_latency_data.py`**:
- Collects latency statistics periodically
- Stores aggregated data in JSON
- Configurable time windows and retention

**`generate_latency_report.py`**:
- Generates comprehensive reports with graphs
- Uses collected data or queries InfluxDB directly
- Creates PNG visualizations

**`monitor_latency.py`**:
- Real-time latency monitoring (single snapshot)
- Field existence checking
- Diagnostic information

**`investigate_spike.py`**:
- Analyze latency spikes
- Time-period analysis
- Root cause investigation

**`check_influxdb_performance.py`**:
- InfluxDB write pattern analysis
- Performance metrics
- Bottleneck identification

#### Metrics Collected

- **Latency Percentiles**: P50, P95, P99
- **Average/Min/Max**: Latency statistics
- **Message Count**: Throughput per sample
- **Target Compliance**: P95 < 2000ms
- **Timestamps**: Collection time for each sample

#### Usage

```bash
# Collect data continuously
python monitoring/collect_latency_data.py --continuous --duration 16h

# Generate report
python monitoring/generate_latency_report.py --duration 16h --sample-limit 50000

# Real-time monitoring
python monitoring/monitor_latency.py
```

---

### 7. Telegraf Integration (`telegraf/`) - Optional

#### Purpose
Production-grade alternative to Python collector with better performance and system metrics.

#### Benefits

- **Performance**: ~10,000+ messages/second (vs ~1,000 for Python)
- **System Metrics**: CPU, memory, disk, network automatically
- **Docker Metrics**: Container statistics
- **Reliability**: Battle-tested, automatic reconnection
- **Configuration-Based**: No code changes needed

#### Configuration

**`telegraf.conf`**:
- MQTT consumer input plugin
- InfluxDB output plugin
- System metrics collection
- Docker metrics collection
- JSON parsing and transformation

#### Usage

```bash
# Docker Compose
cd docker
docker-compose -f docker-compose.with-telegraf.yml up -d

# Manual
telegraf --config telegraf/telegraf.conf
```

---

## Configuration

### Environment Variables

#### Universal Configuration (`env.example.universal`)

```bash
# Primary InfluxDB URL (tried first)
INFLUXDB_URL=http://localhost:8086

# Fallback InfluxDB URL (tried if primary fails)
INFLUXDB_URL_FALLBACK=http://influxdb.secruin.cloud:8086

# InfluxDB Authentication
INFLUXDB_TOKEN=your-token-here
INFLUXDB_ORG=my-org
INFLUXDB_BUCKET=vehicle-data

# MQTT Configuration
MQTT_BROKER_HOST=localhost #or domain/ip of server
MQTT_BROKER_PORT=1883
MQTT_CLIENT_ID=mqtt-collector-python

# MQTT Security (Optional)
MQTT_USE_TLS=false
MQTT_TLS_INSECURE=false
MQTT_CA_CERTS=/path/to/ca.crt
MQTT_CERTFILE=/path/to/client.crt
MQTT_KEYFILE=/path/to/client.key


# Flask Configuration
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_SECRET_KEY=dev-secret-key-change-in-production

# Monitoring Configuration
LATENCY_RETENTION_DURATION=10m
LATENCY_COLLECT_INTERVAL=60
```

#### Device-Specific (`env.example.device`)

```bash
MQTT_BROKER_HOST=mqtt.secruin.cloud
MQTT_BROKER_PORT=1883
MQTT_USE_TLS=true
MQTT_TLS_INSECURE=false
MQTT_CA_CERTS=/path/to/ca.crt
```

#### VPS Configurations

**VPS 1 (Docker Services)** (`env.example.vps1-docker`):
- MQTT and InfluxDB on localhost
- Collector connects to local services

- InfluxDB via domain
- MQTT via domain
- Dashboard accessible via Cloudflare Tunnel

---

## Deployment

### Local Development

1. **Start Docker Services**:
   ```bash
   cd docker
   docker-compose up -d
   ```

2. **Start Collector**:
   ```bash
   python collector/mqtt_collector.py
   ```

3. **Start Devices**:
   ```bash
   python devices/run_devices.py
   ```

4. **Start Dashboard**:
   ```bash
   python dashboard/app.py
   ```

### Distributed Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for complete guide.

#### Architecture

- **VPS 1**: Docker services (Mosquitto, InfluxDB) + Cloudflare Tunnel
            -  Flask Dashboard + Cloudflare Tunnel
- **Devices**: Multiple laptops/devices running device simulators

#### Cloudflare Tunnel Setup

1. Install `cloudflared` on VPS instances
2. Create tunnels for each service
3. Configure routes in Cloudflare Zero Trust Dashboard
4. Start tunnels as systemd services

#### Service URLs

- MQTT: `mqtt.secruin.cloud:1883`
- InfluxDB: `http://influxdb.secruin.cloud:8086`
- Dashboard: `https://dashboard.secruin.cloud`

---

## Performance Characteristics

### Throughput

| Component | Throughput | Notes |
|-----------|------------|-------|
| Device Simulator | ~10 msg/s per device | Configurable publish interval |
| MQTT Broker | 10,000+ msg/s | Depends on hardware |
| Python Collector | ~1,000 msg/s | Single-threaded, batched writes |
| Telegraf Collector | ~10,000+ msg/s | Multi-threaded, optimized |
| InfluxDB Write | 50,000+ points/s | Depends on batch size |

### Latency

| Metric | Target | Typical | Notes |
|---------|--------|---------|-------|
| P50 (Median) | < 500ms | ~300ms | Most messages |
| P95 | < 2000ms | ~500ms | 95th percentile |
| P99 | < 3000ms | ~800ms | 99th percentile |

**Latency Components**:
1. Device → MQTT: ~10-50ms
2. MQTT → Collector: ~10-50ms
3. Collector → InfluxDB (batch delay): ~500ms (flush interval)
4. **Total**: ~520-600ms typical

### Resource Usage

| Component | CPU | Memory | Disk |
|-----------|-----|--------|------|
| Device Simulator | Low | ~20 MB | Queue DB (~1 MB) |
| MQTT Broker | Low | ~50 MB | Logs (~10 MB) |
| Python Collector | Medium | ~50-100 MB | Minimal |
| Telegraf | Low | ~20-50 MB | Minimal |
| InfluxDB | Medium | ~200-500 MB | Data dependent |
| Flask Dashboard | Low | ~50 MB | Minimal |

---

## Monitoring & Observability

### Latency Monitoring

**Collection**:
```bash
# Continuous collection
python monitoring/collect_latency_data.py --continuous --duration 16h

# Single collection
python monitoring/collect_latency_data.py --duration 5m
```

**Reporting**:
```bash
# Generate comprehensive report
python monitoring/generate_latency_report.py --duration 16h --sample-limit 50000
```

**Real-time Monitoring**:
```bash
python monitoring/monitor_latency.py
```

### System Health Checks

**InfluxDB**:
```bash
python check_influxdb_connection.py
```

**MQTT**:
```bash
python check_mqtt_connection.py
```

**WebSocket**:
```bash
python check_socket.py
```

### Performance Analysis

**Write Patterns**:
```bash
python monitoring/check_influxdb_performance.py
```

**Spike Investigation**:
```bash
python monitoring/investigate_spike.py
```

### Metrics Collected

1. **End-to-End Latency**: Device publish → InfluxDB write
2. **Message Throughput**: Messages per second
3. **Device Status**: Online/offline, last seen
4. **System Metrics**: CPU, memory, disk (via Telegraf)
5. **Error Rates**: Connection failures, write errors

---

## Security

### MQTT Security

**TLS/SSL**:
- Enable TLS on port 8883
- Use CA certificates for verification
- Client certificates for authentication

**Authentication**:
- Username/password authentication
- Certificate-based authentication
- Access control lists (ACLs)

**Configuration**:
```conf
# mosquitto.conf
listener 8883
certfile /mosquitto/config/certs/server.crt
cafile /mosquitto/config/certs/ca.crt
keyfile /mosquitto/config/certs/server.key
require_certificate true
```

### InfluxDB Security

- Strong authentication tokens
- HTTPS (if exposed)
- IP whitelisting
- Cloudflare Access policies

### Dashboard Security

- HTTPS via Cloudflare
- Authentication (Flask-Login, recommended)
- Rate limiting
- CSRF protection

### Cloudflare Access

Set up access policies for:
- InfluxDB UI
- MQTT broker (if possible)
- Dashboard (optional)

---

## Troubleshooting

### Common Issues

#### 1. MQTT Connection Failures

**Symptoms**: Devices can't connect to broker

**Solutions**:
- Verify Mosquitto is running: `docker-compose ps`
- Check broker logs: `docker-compose logs mosquitto`
- Verify port 1883 is accessible
- Check firewall rules
- Verify TLS configuration if using

#### 2. InfluxDB Connection Failures

**Symptoms**: Collector can't write to InfluxDB

**Solutions**:
- Verify InfluxDB is running: `docker-compose ps`
- Check InfluxDB logs: `docker-compose logs influxdb`
- Verify token and organization
- Test connection: `python check_influxdb_connection.py`
- Check bucket exists

#### 3. No Data in Dashboard

**Symptoms**: Dashboard shows no data

**Solutions**:
- Verify collector is running
- Check InfluxDB has data: Query via UI
- Verify WebSocket connection (browser console)
- Check device status: `/api/devices/status`
- Verify time range in queries

#### 4. High Latency

**Symptoms**: P95 latency > 2000ms

**Solutions**:
- Reduce batch flush interval (collector)
- Increase batch size
- Check network latency
- Verify InfluxDB performance
- Consider Telegraf for better performance

#### 5. Queue Overflow

**Symptoms**: Device queue files growing too large

**Solutions**:
- Check MQTT connection status
- Verify broker is accessible
- Clear queue files if needed: `rm devices/queues/*.db`
- Increase queue size limit (not recommended)

### Diagnostic Commands

```bash
# Check all services
docker-compose ps

# Check MQTT messages
mosquitto_sub -h localhost -p 1883 -t 'device/data/+' -v

# Test InfluxDB query
curl -X POST http://localhost:8086/api/v2/query \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/vnd.flux" \
  -d 'from(bucket:"vehicle-data") |> range(start:-1h)'

# Check device queue
sqlite3 devices/queues/vehicle_01_queue.db "SELECT COUNT(*) FROM messages;"
```

---

## Development Guide

### Project Structure

```
Realtime-Datastreaming/
├── devices/              # Device simulators
│   ├── device_simulator.py
│   ├── run_devices.py
│   └── queues/           # SQLite queue databases
├── collector/            # Data collectors
│   ├── mqtt_collector.py
│   └── device_status_api.py
├── dashboard/            # Flask dashboard
│   ├── app.py
│   └── templates/
│       └── dashboard.html
├── monitoring/           # Monitoring tools
│   ├── collect_latency_data.py
│   ├── generate_latency_report.py
│   ├── monitor_latency.py
│   └── ...
├── docker/              # Docker configurations
│   ├── docker-compose.yml
│   ├── docker-compose.with-telegraf.yml
│   └── mosquitto/
├── telegraf/            # Telegraf configuration
│   └── telegraf.conf
├── cloudflare/          # Cloudflare tunnel configs
├── testing/             # Test scripts
├── requirements.txt     # Python dependencies
└── .env                 # Environment variables
```

### Adding New Features

#### New Device Type

1. Extend `DeviceSimulator` class
2. Add new telemetry fields
3. Update MQTT topic structure
4. Update collector to handle new fields
5. Update dashboard to visualize new data

#### New Collector

1. Create new collector class
2. Implement MQTT subscription
3. Implement InfluxDB writing
4. Add error handling
5. Add configuration options

#### New Dashboard Feature

1. Add API endpoint in `app.py`
2. Add frontend component in `dashboard.html`
3. Add WebSocket event if needed
4. Update Chart.js configuration

### Testing

**Manual Testing**:
1. Run all components
2. Verify data flow
3. Test network failure handling
4. Test queue recovery
5. Verify dashboard updates

**Automated Testing**:
- Unit tests for device simulator
- Integration tests for collector
- API tests for dashboard
- End-to-end latency tests

### Code Style

- Follow PEP 8
- Use type hints where possible
- Document functions and classes
- Use meaningful variable names
- Add error handling

---

## Appendix

### A. MQTT Topic Structure

- `device/data/{device_id}`: Primary topic for device data
- `vehicle/speed/{device_id}`: Legacy topic (backward compatibility)

### B. InfluxDB Query Examples

**Latest speed per device**:
```flux
from(bucket: "vehicle-data")
  |> range(start: -1h)
  |> filter(fn: (r) => r["_measurement"] == "device_data")
  |> filter(fn: (r) => r["_field"] == "speed")
  |> group(columns: ["device_id"])
  |> last()
```

**Average speed over time**:
```flux
from(bucket: "vehicle-data")
  |> range(start: -1h)
  |> filter(fn: (r) => r["_measurement"] == "device_data")
  |> filter(fn: (r) => r["_field"] == "speed")
  |> aggregateWindow(every: 1m, fn: mean)
```

### C. Performance Tuning

**Collector Batch Settings**:
- Increase `batch_size` for higher throughput
- Decrease `flush_interval` for lower latency
- Adjust `max_retries` for reliability

**InfluxDB Settings**:
- Adjust retention policies
- Optimize queries with time ranges
- Use appropriate batch sizes

**MQTT Settings**:
- Use QoS 0 for high-frequency, non-critical data
- Use QoS 1 for reliable delivery
- Adjust keepalive intervals

### D. References

- [MQTT Specification](https://mqtt.org/mqtt-specification/)
- [InfluxDB Documentation](https://docs.influxdata.com/influxdb/)
- [Flask Documentation](https://flask.palletsprojects.com/)
- [Telegraf Documentation](https://docs.influxdata.com/telegraf/)
- [Cloudflare Tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)

---

**Document Version**: 1.0  
**Last Updated**: 2026-01-04  
**Maintained By**: Development Team

