# Powerwall Influx Service

Async FastAPI service that continuously polls a Tesla Powerwall gateway, writes comprehensive metrics to InfluxDB, and optionally publishes live telemetry to MQTT/Home Assistant with built-in health monitoring.

## Features

- **Continuous Polling**: Automatic background polling every 5 seconds (configurable)
- **InfluxDB Integration**: Writes comprehensive Powerwall metrics to InfluxDB 2.x
- **MQTT Publishing**: Optional real-time sensor publishing to MQTT/Home Assistant with auto-discovery
- **Independent Health Monitoring**: Resilient health status tracking with separate MQTT publishing
- **WiFi Auto-Connect**: Automatic connection to Powerwall WiFi AP using NetworkManager
- **Per-String Solar Data**: Detailed monitoring of individual solar panel strings (voltage, current, power, connection status)
- **Robust Error Handling**: Advanced reconnection logic with circuit breaker pattern
- **FastAPI REST API**: HTTP endpoints for health checks, configuration, and manual polling
- **Systemd Integration**: Production-ready systemd service with auto-restart

## Quick Start

1. Clone & enter the repo
   ```bash
   git clone <repo-url> powerwall-influx
   cd powerwall-influx
   ```
2. (Optional) create a virtual environment
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies
   ```bash
   pip install -r requirements.txt
   ```
4. Configure credentials
   ```bash
   cp .env.example .env
   $EDITOR .env
   ```
5. Run a one-off poll to verify connectivity
   ```bash
   python -m powerwall_service.cli poll --pretty
   ```

## Run Modes

- **HTTP service (development)**
  ```bash
  python -m powerwall_service.cli serve --reload
  ```
- **HTTP service (systemd production)**
   - Run the installer; it asks a few questions (user, group, repo path, venv path, etc.) and renders `powerwall-influx.service.template` into `powerwall-influx.service.local`
   - The generated file is gitignored and reused on subsequent runs unless you choose to regenerate it
   - Need to skip the connectivity smoke test (for example, when the Powerwall is offline)? prefix the command with `PW_INSTALLER_SKIP_TEST=1`
   ```bash
   ./install-service.sh
   sudo systemctl status powerwall-influx
   ```
   The unit starts `python -m powerwall_service.cli serve` and loads `.env` automatically.
- **Manual poll**
  ```bash
  python -m powerwall_service.cli poll --no-push --include-snapshot --pretty
  ```
- **String status helper**
  ```bash
  python -m powerwall_service.string_status --env-file .env
  ```

## Configuration (`.env`)

| Section | Keys (defaults) | Notes |
| --- | --- | --- |
| InfluxDB | `INFLUX_URL`, `INFLUX_ORG`, `INFLUX_BUCKET`, `INFLUX_TOKEN`, `INFLUX_MEASUREMENT=powerwall`, `INFLUX_TIMEOUT=10`, `INFLUX_VERIFY_TLS=false` | `INFLUX_TOKEN` must be set. |
| Powerwall | `PW_HOST=192.168.91.1`, `PW_TIMEZONE=UTC`, `PW_CACHE_EXPIRE=5`, `PW_REQUEST_TIMEOUT=10`, `PW_POLL_INTERVAL=5` | TEDAPI gateway credentials required below. Default poll interval is 5 seconds. |
| Credentials | `PW_CUSTOMER_EMAIL`, `PW_CUSTOMER_PASSWORD`, `PW_GATEWAY_PASSWORD` | Provide whichever combination grants access. |
| Wi-Fi (optional) | `PW_CONNECT_WIFI`, `PW_WIFI_SSID`, `PW_WIFI_PASSWORD`, `PW_WIFI_INTERFACE` | Requires NetworkManager for auto-association. |
| MQTT (optional) | `MQTT_ENABLED`, `MQTT_HOST`, `MQTT_PORT=1883`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TOPIC_PREFIX=homeassistant/sensor/powerwall`, `MQTT_QOS=1`, `MQTT_RETAIN=true`, `MQTT_METRICS=` | Leave `MQTT_METRICS` empty to publish all supported metrics. |
| MQTT Health | `MQTT_HEALTH_ENABLED=true`, `MQTT_HEALTH_HOST`, `MQTT_HEALTH_PORT`, `MQTT_HEALTH_USERNAME`, `MQTT_HEALTH_PASSWORD`, `MQTT_HEALTH_TOPIC_PREFIX=homeassistant/sensor/powerwall_health`, `MQTT_HEALTH_INTERVAL=60`, `MQTT_HEALTH_QOS=1` | Independent health monitoring; defaults to main MQTT settings. See [Health Monitoring](#health-monitoring) section. |
| Logging | `PW_LOG_LEVEL=INFO` | Use `DEBUG` for verbose troubleshooting. |

Save changes and restart the service (or rerun the poll) after updating `.env`.

## Dependencies

The service requires the following Python packages (see `requirements.txt`):

- `pypowerwall>=0.10.0` - Tesla Powerwall API client
- `requests>=2.31.0` - HTTP library
- `paho-mqtt>=1.6.1` - MQTT client for Home Assistant integration
- `fastapi>=0.111.0,<1.0.0` - Modern async web framework
- `uvicorn[standard]>=0.23.0` - ASGI server

## API Surface

The FastAPI service exposes the following HTTP endpoints:

| Method | Path | Description |
| --- | --- | --- |
| GET | `/` | Service banner with version info |
| GET | `/health` | Component health summary (Powerwall, InfluxDB, MQTT, WiFi status) |
| GET | `/config` | Redacted configuration view |
| GET | `/snapshot` | Latest stored poll data from cache |
| GET | `/snapshot/live` | Immediate live poll (supports `push_to_influx`, `publish_mqtt` query flags) |
| POST | `/poll` | Manual poll trigger (JSON body mirrors query flags) |
| GET | `/status` | Background task status and statistics |

Example usage:
```bash
# Check service health
curl http://localhost:8000/health

# Get live snapshot without writing to InfluxDB
curl http://localhost:8000/snapshot/live?push_to_influx=false

# Trigger manual poll with both InfluxDB and MQTT publishing
curl -X POST http://localhost:8000/poll -H "Content-Type: application/json" \
  -d '{"push_to_influx": true, "publish_mqtt": true}'
```


## MQTT Integration

### Configure `.env`

| Key | Purpose |
| --- | --- |
| `MQTT_ENABLED` | Turn publishing on/off. |
| `MQTT_HOST`, `MQTT_PORT` | MQTT broker address (default port 1883). |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | Optional authentication. |
| `MQTT_TOPIC_PREFIX` | Base topic for discovery/state messages (default `homeassistant/sensor/powerwall`). |
| `MQTT_QOS`, `MQTT_RETAIN` | Delivery semantics; defaults work well for Home Assistant. |
| `MQTT_METRICS` | Comma-separated whitelist of metrics; leave empty to publish all. |

After editing the file, restart the service:

```bash
sudo systemctl restart powerwall-influx
```

### Home Assistant Discovery

1. Enable MQTT as above.
2. Check **Settings → Devices & Services → MQTT** for a *Powerwall* device; entities appear automatically via MQTT Discovery.
3. If sensors do not appear, reload the MQTT integration or delete the old *Powerwall* device to trigger rediscovery.

### Available Metrics

The service can publish the following metrics to MQTT:

**Battery Metrics:**
- `battery_percentage` - Battery state of charge (%)
- `battery_power_w` - Battery power (W, positive = charging, negative = discharging)
- `battery_nominal_energy_remaining_wh` - Remaining energy (Wh)

**Power Flow Metrics:**
- `solar_power_w` - Solar generation (W)
- `load_power_w` - Home consumption (W)
- `site_power_w` - Grid power (W, positive = importing, negative = exporting)

**Solar String Metrics** (for strings `a` through `f`):**
- `string_x_voltage_v` - String voltage (V)
- `string_x_current_a` - String current (A)
- `string_x_power_w` - String power (W)
- `string_x_connected` - String connection status (boolean)
- `string_x_state` - String state label

Tune the output by setting `MQTT_METRICS=battery_percentage,solar_power_w,string_a_power_w` (or similar) in `.env`.

## Health Monitoring

The service includes **independent health monitoring** that publishes service health metrics to MQTT for Home Assistant integration. This allows you to create automations that alert you when the service experiences issues.

### Key Features

- **Resilient**: Health metrics publish even when Powerwall/InfluxDB are down
- **Independent**: Separate MQTT connection and async task from main service
- **Auto-discovery**: Seamless Home Assistant integration
- **Component tracking**: Individual health sensors for Powerwall, InfluxDB, MQTT, and WiFi

### Configuration

Enable health monitoring in `.env`:

```bash
MQTT_HEALTH_ENABLED=true
MQTT_HEALTH_HOST=mqtt.home          # Defaults to MQTT_HOST
MQTT_HEALTH_PORT=1883                # Defaults to MQTT_PORT
MQTT_HEALTH_USERNAME=your_user       # Defaults to MQTT_USERNAME
MQTT_HEALTH_PASSWORD=your_pass       # Defaults to MQTT_PASSWORD
MQTT_HEALTH_TOPIC_PREFIX=homeassistant/sensor/powerwall_health
MQTT_HEALTH_INTERVAL=60              # Publish interval in seconds
```

### Using Same MQTT Broker

If you want to use the same MQTT broker for both metrics and health monitoring, you only need to set the main MQTT variables:

```bash
MQTT_ENABLED=true
MQTT_HOST=mqtt.home
MQTT_PORT=1883
MQTT_USERNAME=your_username
MQTT_PASSWORD=your_password

# Health monitoring will automatically use the above settings
MQTT_HEALTH_ENABLED=true
```

### Using Separate MQTT Brokers

For maximum resilience, you can use a different MQTT broker for health monitoring:

```bash
# Main metrics MQTT broker
MQTT_ENABLED=true
MQTT_HOST=mqtt-primary.home
MQTT_USERNAME=metrics_user
MQTT_PASSWORD=metrics_pass

# Health monitoring MQTT broker (separate for redundancy)
MQTT_HEALTH_ENABLED=true
MQTT_HEALTH_HOST=mqtt-backup.home
MQTT_HEALTH_USERNAME=health_user
MQTT_HEALTH_PASSWORD=health_pass
```

### Available Health Sensors

Once enabled, the following sensors automatically appear in Home Assistant:

**Binary Sensors (Device Class: Connectivity)**
- **Powerwall Service Overall Health**: Overall service health status
- **Powerwall Service Powerwall Health**: Powerwall gateway connectivity
- **Powerwall Service InfluxDB Health**: InfluxDB write status
- **Powerwall Service MQTT Health**: MQTT connection status
- **Powerwall Service WiFi Health**: WiFi connection status (if auto-connect enabled)

**Binary Sensor (Device Class: Running)**
- **Powerwall Service Background Task Running**: Whether the polling loop is active

**Regular Sensors**
- **Powerwall Service Consecutive Failures**: Number of consecutive polling failures
- **Powerwall Service Last Poll Time**: Timestamp of last polling attempt
- **Powerwall Service Last Success Time**: Timestamp of last successful poll

Each health sensor includes additional attributes like `detail`, `last_success`, and `last_error` for detailed diagnostics.

### Example Home Assistant Automations

#### Alert on Service Failure

```yaml
automation:
  - alias: "Powerwall Service Health Alert"
    trigger:
      - platform: state
        entity_id: binary_sensor.powerwall_service_overall_health
        from: "connected"
        for:
          minutes: 5
    condition:
      # Don't alert on unknown/unavailable states (sensor initializing)
      - condition: template
        value_template: "{{ states('binary_sensor.powerwall_service_overall_health') == 'disconnected' }}"
    action:
      - service: notify.mobile_app
        data:
          title: "Powerwall Service Issue"
          message: >
            The Powerwall monitoring service is experiencing issues.
            Check the health sensors for details.
```

#### Alert on Multiple Consecutive Failures

```yaml
automation:
  - alias: "Powerwall Multiple Failures"
    trigger:
      - platform: numeric_state
        entity_id: sensor.powerwall_service_consecutive_failures
        above: 5
    action:
      - service: notify.mobile_app
        data:
          title: "Powerwall Service Failing"
          message: >
            The Powerwall service has failed {{ states('sensor.powerwall_service_consecutive_failures') }} times in a row.
```

#### Alert on Powerwall Gateway Unreachable

```yaml
automation:
  - alias: "Powerwall Gateway Unreachable"
    trigger:
      - platform: state
        entity_id: binary_sensor.powerwall_service_powerwall_health
        from: "connected"
        for:
          minutes: 10
    condition:
      - condition: template
        value_template: "{{ states('binary_sensor.powerwall_service_powerwall_health') == 'disconnected' }}"
    action:
      - service: notify.mobile_app
        data:
          title: "Powerwall Gateway Issue"
          message: >
            Cannot reach Powerwall gateway.
            Error: {{ state_attr('binary_sensor.powerwall_service_powerwall_health', 'last_error') }}
```

### Understanding Binary Sensor States

The binary health sensors can show different states:

| State | Meaning |
|-------|---------|
| `connected` | Component is working correctly |
| `disconnected` | Component has an error or is unreachable |
| `unavailable` | Health monitor not connected to MQTT or sensor not yet initialized |
| `unknown` | Initial state before first health status published |

**Important:** When writing automations, add a condition to filter out `unknown` and `unavailable` states to prevent false alerts during sensor initialization or MQTT reconnections.

## Connection Error Handling

The service includes robust error handling and automatic recovery mechanisms:

### Circuit Breaker Pattern

The service implements a circuit breaker pattern to prevent infinite retry loops:

- **Authentication failures**: After 3 consecutive auth errors (403/401), forces full reconnection
- **Connection failures**: After 2 consecutive connection failures, pauses connection attempts until next poll cycle

### Automatic Recovery

When connectivity issues occur, the service:

1. **Detects** authentication and connection errors through enhanced error detection
2. **Tracks** consecutive failures with separate counters for auth vs. connection issues
3. **Attempts** immediate recovery on first auth failure
4. **Escalates** to full reconnection after threshold is exceeded
5. **Resets** counters on successful operation

### Fast-Fail Strategy

Connection attempts use `retry_modes=False` to fail fast (10 seconds) instead of retrying multiple auth modes (60+ seconds). This keeps the service responsive even when the Powerwall is offline.

### Error Flow

```
Poll Attempt
  ↓
Check: consecutive_auth_failures >= 3?
  YES → Force full reconnect
  NO  → Use existing connection
  ↓
Fetch metrics (power, status, vitals)
  ↓
On auth error (403/401)?
  - Increment failure counter
  - If < threshold: Immediate reconnect + retry
  - If >= threshold: Raise error, next poll will force full reconnect
  ↓
On success?
  - Reset counters
  - Log recovery if previously failing
```

### Enhanced Logging

Monitor recovery with detailed log messages:

```
INFO: Forcing full reconnection to Powerwall (auth failures: 3)
WARNING: Authentication error fetching power metrics (failure 1/3)
INFO: Attempting reconnection to recover from auth error
INFO: Successfully recovered from previous auth failures
```

## Grafana Dashboard

The `grafana/powerwall-dashboard.json` file provides an importable Grafana dashboard for visualizing Powerwall metrics.

### Import Steps

1. Open **Grafana → Dashboards → New → Import**
2. Click **Upload JSON file** and select `grafana/powerwall-dashboard.json`
3. Choose your InfluxDB data source (must support Flux query language - InfluxDB 2.x)
4. Configure dashboard variables:
   - **Bucket**: InfluxDB bucket name (default `powerwall`)
   - **Measurement**: Measurement name (default `powerwall`)
   - **Site**: Site tag value (replace with your site name)

### Dashboard Contents

- **Battery State of Charge** - Current battery percentage
- **Energy Remaining** - Gauge showing available energy
- **Alert Count** - Number of active alerts
- **Latest Alert** - Most recent alert message
- **Power Flows** - Time series of site, solar, battery, and load power
- **Solar Generation, Home Load, Grid Power, Battery Power** - Individual time-series panels
- **PV String Power & PV String Current** - Multi-series charts for all strings
- **String Status** - Table showing connection status and state for each string

The dashboard refreshes every 30 seconds by default and uses Flux queries that automatically adjust based on selected variables.

### Customization

- Adjust refresh rate in Grafana settings
- Duplicate panels or add new ones using the same query patterns
- Switch visualization types (e.g., area mode for solar generation)
- Update variables if you've customized measurement or bucket names


## Troubleshooting

### General Diagnostics

- **View service logs**
  ```bash
  sudo journalctl -u powerwall-influx -f      # Follow live logs
  sudo journalctl -u powerwall-influx -n 50   # Recent 50 lines
  sudo journalctl -u powerwall-influx -n 20 | grep -i mqtt  # Filter for MQTT
  ```

- **Check connectivity**
  ```bash
  ping $PW_HOST                               # Test Powerwall reachability
  curl $INFLUX_URL/health                     # Test InfluxDB health
  ```

- **Debug poll locally**
  ```bash
  PW_LOG_LEVEL=DEBUG python -m powerwall_service.cli poll --pretty
  ```

- **Check service status**
  ```bash
  sudo systemctl status powerwall-influx
  curl http://localhost:8000/health           # API health endpoint
  ```

### WiFi Auto-Connect Issues

WiFi auto-connect requires NetworkManager (`nmcli`):

- **Verify WiFi availability**
  ```bash
  nmcli device wifi list
  ```

- **Check connection status**
  ```bash
  nmcli connection show
  ```

- **Manual reconnect if WiFi drops**
  ```bash
  sudo nmcli connection up TeslaPW_XXXXXX
  ```

**Notes:**
- If `PW_WIFI_INTERFACE` is not a WiFi device, the service falls back to letting NetworkManager choose
- The service attempts to reactivate existing connection profiles before creating new ones, improving recovery from NetworkManager backoff states

### MQTT Issues

- **Confirm broker reachability**
  ```bash
  ping $MQTT_HOST
  telnet $MQTT_HOST $MQTT_PORT
  ```

- **Check for rapid connect/disconnect cycles**
  ```bash
  sudo journalctl -u powerwall-influx -n 50 | grep -i mqtt
  ```
  
  If you see repeated "Connected/Disconnected" messages:
  - Verify no duplicate services are running with the same client ID
  - Check MQTT broker logs for connection rejections
  - Ensure credentials are correct

- **Home Assistant discovery issues**
  - Ensure discovery prefix is `homeassistant` (default)
  - Remove stale Powerwall devices in HA to trigger rediscovery
  - Check that MQTT integration is enabled in Home Assistant
  - Wait up to 60 seconds for discovery messages after restart

### Health Monitoring Issues

- **Health sensors not appearing**
  1. Verify `MQTT_HEALTH_ENABLED=true` is set
  2. Check MQTT broker connection details
  3. Review logs for health monitor connection messages
  4. Ensure Home Assistant MQTT integration is working
  5. Wait up to 60 seconds for initial discovery

- **Getting alerts with "unknown" state**
  - This happens during service restart or MQTT reconnection
  - Add a condition to your automations to filter out `unknown` and `unavailable` states (see [Health Monitoring](#health-monitoring) section for examples)

- **Health monitoring stopped**
  - Check MQTT broker availability
  - Review service logs for health monitor errors
  - Verify MQTT credentials are still valid
  - Restart the service to reinitialize

### Connection and Authentication Errors

The service automatically recovers from most connection issues, but you may see these in logs:

- **`WARNING: Authentication error (failure X/3)`**
  - Normal after network interruptions
  - Service will automatically reconnect
  - If persists beyond 3 failures, check gateway password

- **`INFO: Forcing full reconnection`**
  - Triggered after repeated auth failures
  - Normal recovery mechanism
  - Should resolve within 1-3 poll cycles

- **`PowerwallUnavailableError`**
  - Powerwall gateway unreachable
  - Check network connectivity
  - Verify WiFi connection if using auto-connect
  - Check `PW_HOST` is correct

**Expected behavior after network drop:**
1. Initial timeouts and WiFi reconnection (if enabled)
2. Possible 1-2 auth failures as session restores
3. Automatic recovery within 1-3 poll cycles
4. Normal operation resumes

If you see repeated forced reconnections every poll, this indicates a deeper issue with the Powerwall gateway or network configuration.

## Version History

### v1.0.0 (2025-10-27) - Initial Standalone Release

First standalone release after extracting from pypowerwall repository.

**Features:**
- Continuous polling service writing to InfluxDB
- WiFi auto-connect using NetworkManager
- String status viewer for per-string solar data
- Systemd integration with automatic restart
- FastAPI REST API with health endpoints
- MQTT publishing with Home Assistant auto-discovery
- Independent health monitoring system
- Comprehensive error handling with circuit breaker pattern
- Debug logging and verification tools

**Dependencies:**
- `pypowerwall>=0.10.0`
- `requests>=2.31.0`
- `paho-mqtt>=1.6.1`
- `fastapi>=0.111.0,<1.0.0`
- `uvicorn[standard]>=0.23.0`

**Migration from pypowerwall:**
- Standalone repository structure
- Independent dependency management
- Clean git history
- Removed coupling to pypowerwall source
- Self-contained documentation
- Production-ready deployment

**Backward Compatibility:**
- All functionality preserved
- Configuration format unchanged
- Same Python module structure
- Existing `.env` files compatible

## Contributing

Contributions are welcome! To contribute:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Update documentation
6. Submit a pull request

## Support

For issues, questions, or feature requests:
- Open an issue on GitHub
- Check existing documentation sections
- Review troubleshooting section above
- Enable `PW_LOG_LEVEL=DEBUG` for detailed diagnostics

## License

MIT © Contributors

---

**Project Information:**
- Repository: powerwall-influx
- Owner: zenoran  
- Current branch: main
- Python: 3.8+
- Platform: Linux (tested with systemd and NetworkManager)
