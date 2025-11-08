# Health Monitoring with MQTT

The Powerwall Influx service now includes an independent health monitoring system that publishes service health metrics to MQTT for Home Assistant integration. This allows you to easily create automations to monitor service health and receive alerts when issues occur.

## Key Features

### Independent Operation
- **Isolated MQTT Connection**: The health monitor uses its own dedicated MQTT client, completely separate from the main service's MQTT publisher
- **Resilient to Service Failures**: Health metrics continue to be published even when the Powerwall gateway is unreachable, InfluxDB is down, or the WiFi connection fails
- **Separate Thread**: Runs in its own async task with independent error handling

### Home Assistant Integration
- **Auto-Discovery**: Automatically publishes MQTT discovery messages for seamless Home Assistant integration
- **Binary Sensors**: Health status for each component (Powerwall, InfluxDB, MQTT, WiFi)
- **Sensors**: Timestamps for last poll/success, consecutive failure count, background task status
- **Device Grouping**: All sensors are grouped under a single "Powerwall Service" device in Home Assistant

## Configuration

### Basic Setup

Add these environment variables to your `.env` file or `powerwall.env`:

```bash
# Enable health monitoring (default: true)
MQTT_HEALTH_ENABLED=true

# MQTT broker for health monitoring (defaults to MQTT_HOST/MQTT_PORT if not set)
MQTT_HEALTH_HOST=mqtt.home
MQTT_HEALTH_PORT=1883

# MQTT credentials for health monitoring (defaults to MQTT_USERNAME/MQTT_PASSWORD if not set)
MQTT_HEALTH_USERNAME=your_mqtt_username
MQTT_HEALTH_PASSWORD=your_mqtt_password

# Health publishing interval in seconds (default: 60)
MQTT_HEALTH_INTERVAL=60

# MQTT topic prefix for health sensors (default: homeassistant/sensor/powerwall_health)
MQTT_HEALTH_TOPIC_PREFIX=homeassistant/sensor/powerwall_health

# MQTT QoS level for health messages (default: 1)
MQTT_HEALTH_QOS=1
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
MQTT_PORT=1883
MQTT_USERNAME=metrics_user
MQTT_PASSWORD=metrics_pass

# Health monitoring MQTT broker (separate for redundancy)
MQTT_HEALTH_ENABLED=true
MQTT_HEALTH_HOST=mqtt-backup.home
MQTT_HEALTH_PORT=1883
MQTT_HEALTH_USERNAME=health_user
MQTT_HEALTH_PASSWORD=health_pass
```

## Home Assistant Sensors

Once enabled, the following sensors will automatically appear in Home Assistant:

### Binary Sensors (Device Class: Connectivity)
- **Powerwall Service Overall Health**: Overall service health status
- **Powerwall Service Powerwall Health**: Powerwall gateway connectivity
- **Powerwall Service InfluxDB Health**: InfluxDB write status
- **Powerwall Service MQTT Health**: MQTT connection status
- **Powerwall Service WiFi Health**: WiFi connection status (if auto-connect enabled)

> **Note:** Binary sensors show as "Connected" or "Disconnected" in Home Assistant. Use `"connected"` and `"disconnected"` in automation triggers.

### Binary Sensor (Device Class: Running)
- **Powerwall Service Background Task Running**: Whether the polling loop is active

> **Note:** This sensor shows as "Running" or "Stopped" in Home Assistant. Use `"running"` and `"stopped"` in automation triggers.

### Sensors
- **Powerwall Service Consecutive Failures**: Number of consecutive polling failures
- **Powerwall Service Last Poll Time**: Timestamp of last polling attempt (device class: timestamp)
- **Powerwall Service Last Success Time**: Timestamp of last successful poll (device class: timestamp)

### Attributes

Each component health sensor includes additional attributes:
- `detail`: Human-readable status message or error description
- `last_success`: ISO timestamp of last successful operation
- `last_error`: Last error message (if any)

## Example Home Assistant Automations

**Note:** Replace entity IDs below with your actual entity IDs from Home Assistant. The default entity IDs are:
- `binary_sensor.powerwall_service_overall_health`
- `binary_sensor.powerwall_service_powerwall_health`
- `binary_sensor.powerwall_service_influxdb_health`
- `binary_sensor.powerwall_service_mqtt_health`
- `binary_sensor.powerwall_service_wifi_health`
- `binary_sensor.powerwall_service_background_task_running`
- `sensor.powerwall_service_consecutive_failures`
- `sensor.powerwall_service_last_poll_time`
- `sensor.powerwall_service_last_success_time`

### Alert on Service Failure

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
            Status: {{ states('binary_sensor.powerwall_service_overall_health') }}
            Check the health sensors for details.
```

### Alert on Multiple Consecutive Failures

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

### Alert on Specific Component Failure

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
      # Don't alert on unknown/unavailable states (sensor initializing)
      - condition: template
        value_template: "{{ states('binary_sensor.powerwall_service_powerwall_health') == 'disconnected' }}"
    action:
      - service: notify.mobile_app
        data:
          title: "Powerwall Gateway Issue"
          message: >
            Cannot reach Powerwall gateway.
            Status: {{ states('binary_sensor.powerwall_service_powerwall_health') }}
            Error: {{ state_attr('binary_sensor.powerwall_service_powerwall_health', 'last_error') }}
```

### Monitor Background Task

```yaml
automation:
  - alias: "Powerwall Service Stopped"
    trigger:
      - platform: state
        entity_id: binary_sensor.powerwall_service_background_task_running
        from: "running"
        for:
          minutes: 2
    condition:
      # Don't alert on unknown/unavailable states (sensor initializing)
      - condition: template
        value_template: "{{ states('binary_sensor.powerwall_service_background_task_running') == 'stopped' }}"
    action:
      - service: notify.mobile_app
        data:
          title: "Powerwall Service Stopped"
          message: >
            The Powerwall monitoring background task has stopped.
            Status: {{ states('binary_sensor.powerwall_service_background_task_running') }}
```

## MQTT Topics

Health status is published to the following topics:

```
homeassistant/sensor/powerwall_health/overall/state
homeassistant/sensor/powerwall_health/powerwall/state
homeassistant/sensor/powerwall_health/powerwall/attributes
homeassistant/sensor/powerwall_health/influxdb/state
homeassistant/sensor/powerwall_health/influxdb/attributes
homeassistant/sensor/powerwall_health/mqtt/state
homeassistant/sensor/powerwall_health/mqtt/attributes
homeassistant/sensor/powerwall_health/wifi/state
homeassistant/sensor/powerwall_health/wifi/attributes
homeassistant/sensor/powerwall_health/consecutive_failures/state
homeassistant/sensor/powerwall_health/last_poll_time/state
homeassistant/sensor/powerwall_health/last_success_time/state
homeassistant/sensor/powerwall_health/background_task/state
```

Discovery configurations are published to:
```
homeassistant/binary_sensor/powerwall_influx_service/overall_health/config
homeassistant/binary_sensor/powerwall_influx_service/powerwall_health/config
homeassistant/binary_sensor/powerwall_influx_service/influxdb_health/config
homeassistant/binary_sensor/powerwall_influx_service/mqtt_health/config
homeassistant/binary_sensor/powerwall_influx_service/wifi_health/config
homeassistant/sensor/powerwall_influx_service/consecutive_failures/config
homeassistant/sensor/powerwall_influx_service/last_poll_time/config
homeassistant/sensor/powerwall_influx_service/last_success_time/config
homeassistant/binary_sensor/powerwall_influx_service/background_task/config
```

## Troubleshooting

### Understanding Binary Sensor States

The binary sensors can show different states in Home Assistant:

| State | Meaning |
|-------|---------|
| `connected` | Component is working correctly |
| `disconnected` | Component has an error or is unreachable |
| `unavailable` | Health monitor not connected to MQTT or sensor not yet initialized |
| `unknown` | Initial state before first health status published |

When writing automations, trigger on `from: "connected"` (or `from: "running"` for the background task sensor) to catch any non-healthy state including `disconnected`, `unavailable`, or `unknown`.

**Important:** Add a condition to filter out `unknown` and `unavailable` states to prevent false alerts during sensor initialization or MQTT reconnections. See automation examples above.

### Health sensors not appearing in Home Assistant

1. Check that `MQTT_HEALTH_ENABLED=true` is set
2. Verify MQTT broker connection details are correct
3. Check the service logs for health monitor connection messages
4. Ensure Home Assistant MQTT integration is configured and working
5. Wait up to 60 seconds for initial discovery messages to be sent

### Health monitoring stopped working

The health monitor runs independently, so it should continue even if the main service fails. If it stops:

1. Check MQTT broker availability
2. Review service logs for health monitor errors
3. Verify MQTT credentials are still valid
4. Restart the service to reinitialize the health monitor

### Getting alerts with "unknown" state

If you receive alerts showing state as "unknown", this typically happens when:

1. **Service just restarted**: Health monitor waits 5 seconds before first publish
2. **MQTT reconnection**: Brief moment when Home Assistant hasn't received the state yet
3. **Sensor initialization**: First time the sensor is created

**Solution:** Add a condition to your automations (see examples above) to only alert on `disconnected` state, filtering out `unknown` and `unavailable`. This prevents false alerts during normal service restarts or MQTT reconnections.

### Different values for health vs main MQTT

This is expected and intentional. The health monitor operates independently and publishes to a separate topic structure to ensure resilience.

## Technical Details

### Architecture

The health monitoring system is designed with the following principles:

1. **Independence**: Separate async task, separate MQTT client, separate error handling
2. **Resilience**: Continues operating even when Powerwall/InfluxDB services fail
3. **Non-blocking**: Does not interfere with main polling loop performance
4. **Fail-safe**: Catches and logs all exceptions to prevent crashes

### Resource Usage

- Additional MQTT connection (minimal overhead)
- Separate async task publishing every 60 seconds (configurable)
- Discovery messages sent once on startup and after reconnection
- Typical payload size: < 1KB per publish cycle

### Startup Sequence

1. Health monitor initializes with its own MQTT client
2. Connects to MQTT broker independently
3. Waits 5 seconds for services to initialize
4. Sends Home Assistant discovery messages
5. Begins periodic health status publishing
6. Continues regardless of main service status
