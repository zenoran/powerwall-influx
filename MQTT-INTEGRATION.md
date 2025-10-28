# Powerwall-Influx MQTT Integration

## Status: **FULLY OPERATIONAL with Auto-Discovery**

The MQTT integration provides **Home Assistant MQTT Discovery** support for instant sensor updates!

---

## 🎉 Features

### ✅ Home Assistant MQTT Discovery
- **Automatic sensor creation** - No manual configuration needed!
- Publishes discovery configs on connection
- All sensors appear automatically in HA
- Includes device information, icons, and units
- Availability tracking (online/offline status)

### ✅ MQTT Publishing
- Connects to MQTT broker (configurable in .env)
- Publishes selected metrics every poll interval
- Optional authentication support
- Retained messages for instant HA updates
- QoS level 1 for reliable delivery

### ✅ Systemd Service
- Service: `powerwall-influx.service`
- Status: **Active and running**
- Auto-starts on boot: **Enabled**
- Logs show successful MQTT connection and regular metric writes

### ✅ Available Metrics

Configure which metrics to publish via the `MQTT_METRICS` setting in `.env`.

**Battery Metrics:**
- `battery_percentage` - Battery charge level (%)
- `battery_power_w` - Battery charging/discharging power (W)

**Solar Metrics:**
- `solar_power_w` - Solar production power (W)

**Load/Site Metrics:**
- `load_power_w` - Home load consumption (W)
- `site_power_w` - Grid import/export power (W)

**String Metrics (per string A, B, C):**
- `string_X_voltage_v` - String voltage (V)
- `string_X_current_a` - String current (A)
- `string_X_power_w` - String power (W)
- `string_X_connected` - Connection status (true/false)
- `string_X_state` - String state (PV_Active, PV_Active_Parallel, etc.)

**Example:** 20 sensors when all string metrics are enabled

---

## 📋 Next Steps for Home Assistant

### 1. Enable MQTT in Configuration

Edit `.env` and set:
```bash
MQTT_ENABLED=true
MQTT_HOST=your-mqtt-broker
MQTT_PORT=1883
MQTT_METRICS=battery_percentage,battery_power_w,solar_power_w,load_power_w,site_power_w
```

Restart the service:
```bash
sudo systemctl restart powerwall-influx
```

### 2. Verify Auto-Discovery in Home Assistant

**Sensors appear automatically** after the service connects to MQTT.

Check in Home Assistant:
- Settings → Devices & Services → MQTT
- Look for "Powerwall" device
- All configured sensors should be listed

**Or:**
- Developer Tools → States
- Search for "powerwall"
- All sensors should appear with entity IDs like `sensor.powerwall_battery_percentage`

### 3. Verify Sensors Update

After the powerwall-influx service starts:

**Option A: Via Devices**
- Settings → Devices & Services → MQTT → Devices
- Click "Powerwall"
- See all configured sensors with live data updating every poll interval

**Option B: Via States**
- Developer Tools → States
- Search for "powerwall"
- You should see all sensors with live data and 2 decimal precision

### 4. Create Dashboard

Example Energy Dashboard card:
```yaml
type: entities
title: Powerwall Live Metrics
entities:
  - entity: sensor.powerwall_battery
    name: Battery Level
  - entity: sensor.powerwall_battery_power
    name: Battery Power
  - entity: sensor.powerwall_solar_power
    name: Solar Production
  - entity: sensor.powerwall_load_power
    name: Home Load
  - entity: sensor.powerwall_site_power
    name: Grid Power
```

---

## 🔧 Current Configuration

### MQTT Settings (.env)
```bash
MQTT_ENABLED=true
MQTT_HOST=mqtt.home
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=
MQTT_TOPIC_PREFIX=homeassistant/sensor/powerwall
MQTT_QOS=1
MQTT_RETAIN=true
# Leave empty to publish all available metrics, or specify comma-separated list
MQTT_METRICS=battery_percentage,battery_power_w,solar_power_w,load_power_w,site_power_w
```

### Service Details
- Poll Interval: Configurable via `PW_POLL_INTERVAL` (default: 5 seconds)
- Discovery Topic: `homeassistant/sensor/powerwall/<metric>/config`
- State Topic: `homeassistant/sensor/powerwall/<metric>/state`
- Device Name: "Powerwall"
- Entity ID Format: `sensor.powerwall_<metric_name>`

---

## 🏗️ Architecture

```
Powerwall (192.168.91.1)
    ↓ (poll every 5s)
powerwall_service.influx_service
    ↓
    ├→ InfluxDB (your-influxdb-server:8086)
    │  └─ All 50+ sensors, configurable resolution
    │     └─ For Grafana dashboards (high-res historical data)
    │
    └→ MQTT (your-mqtt-broker:1883)
       └─ Selected sensors, instant updates
          └─ For Home Assistant
             └─ Live dashboard with auto-discovery
```

**Benefits:**
- ✅ **Instant HA dashboard updates** - MQTT push instead of polling
- ✅ **High-resolution historical data** - Direct InfluxDB writes
- ✅ **Selective metric publishing** - Choose which sensors to expose via MQTT
- ✅ **String-level monitoring** - Individual solar string diagnostics

---

## 📊 Service Logs

### Check Service Status
```bash
sudo systemctl status powerwall-influx
```

### View Recent Logs
```bash
sudo journalctl -u powerwall-influx -n 50
```

### Follow Live Logs
```bash
sudo journalctl -u powerwall-influx -f
```

### Debug Mode
```bash
# Edit .env file
PW_LOG_LEVEL=DEBUG

# Restart service
sudo systemctl restart powerwall-influx

# Watch logs for detailed MQTT publishing info
sudo journalctl -u powerwall-influx -f
```

---

## 🐛 Troubleshooting

### MQTT Not Connecting

**Check MQTT broker is reachable:**
```bash
ping your-mqtt-broker
telnet your-mqtt-broker 1883
```

**Check service logs:**
```bash
sudo journalctl -u powerwall-influx -n 50
```

Look for:
- ✅ "Connecting to MQTT broker at ..."
- ✅ "Connected to MQTT broker"
- ✅ "Published X MQTT Discovery configurations"
- ❌ Connection errors or timeouts

### Sensors Not Showing in HA

1. **Verify discovery prefix matches:**
   - HA MQTT integration should use `homeassistant` as discovery prefix (default)
   - Check in Settings → Devices & Services → MQTT → Configure

2. **Check if device was previously registered with old entity IDs:**
   - Delete the "Powerwall" device in HA MQTT integration
   - Wait 5-10 seconds for auto-rediscovery
   - New device should appear with clean entity IDs

3. **Reload MQTT integration:**
   - Settings → Devices & Services → MQTT
   - Three dots menu → Reload

4. **Check HA logs:**
   - Settings → System → Logs
   - Look for MQTT integration errors

5. **Verify MQTT messages exist:**
   - Use MQTT Explorer or similar tool
   - Connect to your MQTT broker
   - Look for topics under `homeassistant/sensor/powerwall/`

### Modify Published Metrics

Edit `.env` file and change `MQTT_METRICS`:

```bash
# Publish only essential metrics
MQTT_METRICS=battery_percentage,battery_power_w,solar_power_w,load_power_w,site_power_w

# Add string monitoring for troubleshooting
MQTT_METRICS=battery_percentage,battery_power_w,solar_power_w,load_power_w,site_power_w,string_a_voltage_v,string_a_current_a,string_a_power_w,string_b_voltage_v,string_b_current_a,string_b_power_w,string_c_voltage_v,string_c_current_a,string_c_power_w,string_a_connected,string_a_state,string_b_connected,string_b_state,string_c_connected,string_c_state

# Publish all available metrics (leave empty)
MQTT_METRICS=
```

After editing, restart service:
```bash
sudo systemctl restart powerwall-influx
```

The service will automatically republish discovery configs with the new metric list.

---

---

## 🎯 Quick Test

Verify MQTT is working:

```bash
# Restart service
sudo systemctl restart powerwall-influx

# Check logs for MQTT connection
sudo journalctl -u powerwall-influx -n 20 | grep -i mqtt
```

Look for:
- "Connected to MQTT broker"
- "Published X MQTT Discovery configurations"

Then check Home Assistant for the "Powerwall" device in the MQTT integration.

---

## 💡 Usage Tips

### Dashboard Ideas

**Real-time Energy Flow:**
- Battery charging/discharging power
- Solar production
- Home consumption
- Grid import/export

**String Monitoring:**
- Voltage drops indicate shading or panel issues
- Current spikes during peak production
- Power output per string for diagnostics

**Automations:**
- Alert if battery drops below threshold
- Notify when solar production peaks
- Trigger actions based on grid power

### Example Automation

```yaml
automation:
  - alias: "Low Battery Alert"
    trigger:
      - platform: numeric_state
        entity_id: sensor.powerwall_battery
        below: 20
    action:
      - service: notify.mobile_app
        data:
          message: "Powerwall battery is at {{ states('sensor.powerwall_battery') }}%"
```

---

## 📝 Implementation Details

### Key Components

1. **MQTTPublisher Class** (`powerwall_service/influx_service.py`)
   - Manages MQTT connection and publishing
   - Publishes Home Assistant Discovery configs on connect
   - Sends sensor state updates with each poll
   - Handles availability (online/offline) status

2. **Configuration** (`.env` file)
   - `MQTT_ENABLED` - Enable/disable MQTT publishing
   - `MQTT_HOST` - MQTT broker hostname/IP
   - `MQTT_PORT` - MQTT broker port (default: 1883)
   - `MQTT_USERNAME` / `MQTT_PASSWORD` - Optional authentication
   - `MQTT_TOPIC_PREFIX` - Base topic for all messages
   - `MQTT_QOS` - Quality of Service level (0, 1, or 2)
   - `MQTT_RETAIN` - Retain messages for instant HA updates
   - `MQTT_METRICS` - Comma-separated list of metrics to publish

3. **Dependencies** (`requirements.txt`)
   - `paho-mqtt>=2.1.0` - MQTT client library

### Discovery Protocol

The service uses Home Assistant's MQTT Discovery protocol:

- **Discovery Topic:** `homeassistant/sensor/powerwall/<metric>/config`
- **State Topic:** `homeassistant/sensor/powerwall/<metric>/state`
- **Availability Topic:** `homeassistant/sensor/powerwall/availability`

Discovery configs include:
- Sensor name and unique ID
- Device information (manufacturer, model, identifiers)
- Unit of measurement and device class
- Icon and state class
- Value formatting (2 decimal places for floats)

---

**Status:** Production ready! 🚀

````
