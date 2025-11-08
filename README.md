# Powerwall Influx Service

Async FastAPI service that polls a Tesla Powerwall gateway, writes metrics to InfluxDB, and optionally publishes live telemetry to MQTT/Home Assistant.

## Quick start

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

## Run modes

- **HTTP service (development)**
  ```bash
  python -m powerwall_service.cli serve --reload
  ```
- **HTTP service (systemd)**
   - Run the installer; it now asks a few questions (user, group, repo path, venv path, etc.) and renders `powerwall-influx.service.template` into `powerwall-influx.service.local` for you.
   - The generated file is gitignored and reused on subsequent runs unless you choose to regenerate it.
   - Need to skip the connectivity smoke test (for example, when the Powerwall is offline)? prefix the command with `PW_INSTALLER_SKIP_TEST=1`.
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
| InfluxDB | `INFLUX_URL`, `INFLUX_ORG`, `INFLUX_BUCKET`, `INFLUX_TOKEN`, `INFLUX_MEASUREMENT=powerwall`, `INFLUX_TIMEOUT=10`, `INFLUX_VERIFY_TLS=true` | `INFLUX_TOKEN` must be set. |
| Powerwall | `PW_HOST=192.168.91.1`, `PW_TIMEZONE=UTC`, `PW_CACHE_EXPIRE=5`, `PW_REQUEST_TIMEOUT=10`, `PW_POLL_INTERVAL=30` | TEDAPI gateway credentials required below. |
| Credentials | `PW_CUSTOMER_EMAIL`, `PW_CUSTOMER_PASSWORD`, `PW_GATEWAY_PASSWORD` | Provide whichever combination grants access. |
| Wi-Fi (optional) | `PW_CONNECT_WIFI`, `PW_WIFI_SSID`, `PW_WIFI_PASSWORD`, `PW_WIFI_INTERFACE` | Requires NetworkManager for auto-association. |
| MQTT (optional) | `MQTT_ENABLED`, `MQTT_HOST`, `MQTT_PORT=1883`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TOPIC_PREFIX=homeassistant/sensor/powerwall`, `MQTT_QOS=1`, `MQTT_RETAIN=true`, `MQTT_METRICS=` | Leave `MQTT_METRICS` empty to publish all supported metrics. |
| MQTT Health Monitoring | `MQTT_HEALTH_ENABLED=true`, `MQTT_HEALTH_HOST`, `MQTT_HEALTH_PORT`, `MQTT_HEALTH_USERNAME`, `MQTT_HEALTH_PASSWORD`, `MQTT_HEALTH_TOPIC_PREFIX=homeassistant/sensor/powerwall_health`, `MQTT_HEALTH_INTERVAL=60`, `MQTT_HEALTH_QOS=1` | Independent health monitoring; defaults to main MQTT settings. See [HEALTH_MONITORING.md](HEALTH_MONITORING.md) for details. |
| Logging | `PW_LOG_LEVEL=INFO` | Use `DEBUG` for verbose troubleshooting. |

Save changes and restart the service (or rerun the poll) after updating `.env`.

## MQTT integration

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

### Home Assistant discovery

1. Enable MQTT as above.
2. Check **Settings → Devices & Services → MQTT** for a *Powerwall* device; entities appear automatically via MQTT Discovery.
3. If sensors do not appear, reload the MQTT integration or delete the old *Powerwall* device to trigger rediscovery.

### Metric cheat sheet

- Battery: `battery_percentage`, `battery_power_w`, `battery_nominal_energy_remaining_wh`
- Solar / load: `solar_power_w`, `load_power_w`, `site_power_w`
- Strings (`a`–`f`): `string_x_voltage_v`, `string_x_current_a`, `string_x_power_w`, `string_x_connected`, `string_x_state`

Tune the output by setting `MQTT_METRICS=battery_percentage,solar_power_w,string_a_power_w` (or similar) in `.env`.

## Health Monitoring

The service includes **independent health monitoring** that publishes service health metrics to MQTT for Home Assistant integration. This allows you to create automations that alert you when the service experiences issues.

**Key features:**
- **Resilient**: Health metrics publish even when Powerwall/InfluxDB are down
- **Independent**: Separate MQTT connection and async task
- **Auto-discovery**: Seamless Home Assistant integration
- **Component tracking**: Individual health sensors for Powerwall, InfluxDB, MQTT, and WiFi

Enable health monitoring in `.env`:

```bash
MQTT_HEALTH_ENABLED=true
MQTT_HEALTH_HOST=mqtt.home          # Defaults to MQTT_HOST
MQTT_HEALTH_PORT=1883                # Defaults to MQTT_PORT
MQTT_HEALTH_USERNAME=your_user       # Defaults to MQTT_USERNAME
MQTT_HEALTH_PASSWORD=your_pass       # Defaults to MQTT_PASSWORD
```

**📖 See [HEALTH_MONITORING.md](HEALTH_MONITORING.md) for complete documentation**, including:
- Configuration options
- Available sensors
- Example Home Assistant automations
- Troubleshooting

### Useful commands

```bash
sudo systemctl status powerwall-influx      # Service status
sudo journalctl -u powerwall-influx -n 50   # Recent logs
sudo journalctl -u powerwall-influx -f      # Follow live logs
sudo journalctl -u powerwall-influx -n 20 | grep -i mqtt
```

## API surface

| Method | Path | Description |
| --- | --- | --- |
| GET | `/` | Service banner |
| GET | `/health` | Component health summary |
| GET | `/config` | Redacted configuration |
| GET | `/snapshot` | Latest stored poll |
| GET | `/snapshot/live` | Immediate poll (`push_to_influx`, `publish_mqtt` query flags) |
| POST | `/poll` | Manual poll (JSON body mirrors flags) |
| GET | `/status` | Background task status |

## Troubleshooting

- **General diagnostics**
   - Follow logs: `sudo journalctl -u powerwall-influx -f`
   - Check connectivity: `ping $PW_HOST`, `curl $INFLUX_URL/health`
   - Debug poll locally: `PW_LOG_LEVEL=DEBUG python -m powerwall_service.cli poll --pretty`
- **Wi-Fi auto-connect**
   - Requires NetworkManager (`nmcli`). If `PW_WIFI_INTERFACE` is not a Wi-Fi device the service will fall back to letting NM choose.
   - Verify availability: `nmcli device wifi list`
   - Check connection status: `nmcli connection show`
   - If Wi-Fi drops, manually reconnect: `sudo nmcli connection up <SSID>` (e.g., `sudo nmcli connection up TeslaPW_XXXXXX`)
   - The service now attempts to reactivate existing connection profiles before creating new ones, improving recovery from NetworkManager backoff states
- **MQTT**
   - Confirm broker reachability: `ping $MQTT_HOST`, `telnet $MQTT_HOST $MQTT_PORT`
   - Check for rapid connect/disconnect cycles: `sudo journalctl -u powerwall-influx -n 50 | grep -i mqtt`
     - If you see repeated "Connected/Disconnected" messages, verify no duplicate services are running with the same client ID
   - Ensure Home Assistant discovery prefix is `homeassistant` (default).
   - Remove stale Powerwall devices in HA to trigger rediscovery.

## License

MIT © Contributors
