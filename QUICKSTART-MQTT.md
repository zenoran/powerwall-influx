# Quick Reference: MQTT Integration

## ✅ Status: Production Ready

MQTT publishing with Home Assistant Auto-Discovery support.

---

## 🚀 Setup

### 1. Configure MQTT in .env

```bash
MQTT_ENABLED=true
MQTT_HOST=your-mqtt-broker
MQTT_PORT=1883
MQTT_USERNAME=  # Optional
MQTT_PASSWORD=  # Optional
MQTT_METRICS=battery_percentage,battery_power_w,solar_power_w,load_power_w,site_power_w
```

### 2. Restart Service

```bash
sudo systemctl restart powerwall-influx
```

### 3. Check Home Assistant

- Settings → Devices & Services → MQTT
- Look for "Powerwall" device
- All sensors auto-discovered with entity IDs like `sensor.powerwall_battery_percentage`

**No manual configuration needed!**

---

## 📊 Available Metrics

Configure via `MQTT_METRICS` in `.env`:

- `battery_percentage` - Battery charge %
- `battery_power_w` - Battery power (W)
- `solar_power_w` - Solar production (W)
- `load_power_w` - Home consumption (W)
- `site_power_w` - Grid import/export (W)
- `string_X_voltage_v` - String voltage (V)
- `string_X_current_a` - String current (A)
- `string_X_power_w` - String power (W)
- `string_X_connected` - Connection status
- `string_X_state` - String state

Leave `MQTT_METRICS` empty to publish all available metrics.

---

## 🛠️ Commands

```bash
# Check status
sudo systemctl status powerwall-influx

# View logs
sudo journalctl -u powerwall-influx -n 50

# Follow live logs
sudo journalctl -u powerwall-influx -f

# Restart service
sudo systemctl restart powerwall-influx
```

---

## 🔧 Configuration

Edit `~/dev/powerwall-influx/.env`:

```bash
MQTT_ENABLED=true
MQTT_HOST=mqtt-broker-hostname
MQTT_PORT=1883
MQTT_TOPIC_PREFIX=homeassistant/sensor/powerwall
MQTT_QOS=1
MQTT_RETAIN=true
MQTT_METRICS=  # Comma-separated list or empty for all
```

**Updates every poll interval** (default: 5 seconds)

---

## 🐛 Troubleshooting

**No sensors in HA?**
1. Verify HA MQTT integration is configured
2. Check MQTT discovery prefix is `homeassistant` (HA default)
3. Delete old "Powerwall" device in HA and wait for rediscovery
4. Reload MQTT integration: Settings → Devices & Services → MQTT → Reload

**Service issues?**
```bash
sudo systemctl status powerwall-influx
sudo journalctl -u powerwall-influx -n 50 | grep -i mqtt
```

---

## 📚 Full Documentation

See `MQTT-INTEGRATION.md` for complete documentation.

````
