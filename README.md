# Powerwall InfluxDB Service

A lightweight service that polls Tesla Powerwall data and writes metrics to InfluxDB.

## Features

- 🔌 **Automatic Powerwall Connection** - Auto-connects to Powerwall Wi-Fi AP if needed
- 📊 **Comprehensive Metrics** - Exports power, energy, voltage, current, and string data
- 🔄 **Continuous Polling** - Configurable polling interval (default: 5 seconds)
- 🗄️ **InfluxDB Integration** - Writes metrics in line protocol format
- 🎯 **String-Level Solar Data** - Per-string voltage, current, power, and connection status
- 🔧 **Systemd Service** - Run automatically on system startup
- 🛠️ **Easy Configuration** - Simple `.env` file configuration

## Requirements

- Python 3.8+
- Tesla Powerwall 3 (TEDAPI mode)
- InfluxDB v2.x
- NetworkManager (for Wi-Fi auto-connect, optional)

## Installation

### 1. Clone the repository

```bash
cd ~/dev
git clone <repo-url> powerwall-influx
cd powerwall-influx
```

### 2. Create a virtual environment (recommended)

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure the service

```bash
cp .env.example .env
nano .env
```

Edit `.env` with your settings:
- InfluxDB URL, token, organization, and bucket
- Powerwall IP address and credentials
- Wi-Fi settings (if using auto-connect)
- Polling interval

### 5. Test the service

```bash
# Single poll test
python -m powerwall_service.influx_service --env-file .env --once

# Continuous mode (Ctrl+C to stop)
python -m powerwall_service.influx_service --env-file .env
```

## Usage

### Run Manually

```bash
# Activate virtual environment (if using one)
source venv/bin/activate

# Single poll
python -m powerwall_service.influx_service --env-file .env --once

# Continuous polling
python -m powerwall_service.influx_service --env-file .env
```

### Install as Systemd Service

For automatic startup on boot:

```bash
./install-service.sh
```

This will:
1. Test the service
2. Install the systemd service file
3. Enable autostart on boot
4. Start the service immediately

### Query String Data

View current solar string status from InfluxDB:

```bash
python -m powerwall_service.string_status --env-file .env
```

Or use the shell wrapper:

```bash
./show-strings.sh
```

## Shell Aliases (Optional)

Add these to your `~/.zshrc` or `~/.bashrc` for convenience:

```bash
# Powerwall InfluxDB service aliases
alias pw-strings='cd ~/dev/powerwall-influx && python -m powerwall_service.string_status --env-file .env'
alias pw-influx-once='cd ~/dev/powerwall-influx && python -m powerwall_service.influx_service --env-file .env --once'
alias pw-influx='cd ~/dev/powerwall-influx && echo "🚀 Starting Powerwall InfluxDB service (Ctrl+C to stop)..." && python -m powerwall_service.influx_service --env-file .env'
alias pw-connect='cd ~/dev/powerwall-influx && python -m powerwall_service.connect_wifi --env-file .env'

pw-help() {
  cat << 'EOF'
Powerwall InfluxDB Service Commands:
====================================
pw-strings        - View current string status from InfluxDB
pw-influx-once    - Test service with single poll
pw-influx         - Run continuous service (foreground)
pw-connect        - Connect to Powerwall Wi-Fi AP

Systemd Service Commands:
=========================
sudo systemctl status powerwall-influx   - Check service status
sudo systemctl start powerwall-influx    - Start service
sudo systemctl stop powerwall-influx     - Stop service
sudo systemctl restart powerwall-influx  - Restart service
sudo journalctl -u powerwall-influx -f   - View live logs

Configuration:
=============
Edit ~/dev/powerwall-influx/.env to change settings
After editing, restart the service: sudo systemctl restart powerwall-influx
EOF
}
```

Then run `source ~/.zshrc` to activate.

## Configuration

The `.env` file supports these variables:

### InfluxDB Settings

```bash
INFLUX_URL=http://influxdb.home:8086
INFLUX_ORG=homeassistant
INFLUX_BUCKET=powerwall
INFLUX_TOKEN=your-token-here
INFLUX_MEASUREMENT=powerwall
INFLUX_TIMEOUT=10
INFLUX_VERIFY_TLS=false
```

### Powerwall Settings

```bash
PW_HOST=192.168.91.1
PW_TIMEZONE=America/New_York
PW_CACHE_EXPIRE=5
PW_REQUEST_TIMEOUT=10
PW_POLL_INTERVAL=5  # Polling interval in seconds
```

### Credentials

```bash
PW_CUSTOMER_EMAIL=
PW_CUSTOMER_PASSWORD=
PW_GATEWAY_PASSWORD=your-gateway-password
```

### Wi-Fi Auto-Connect (Optional)

```bash
PW_CONNECT_WIFI=true
PW_WIFI_SSID=TeslaPW_XXXXXX
PW_WIFI_PASSWORD=your-wifi-password
PW_WIFI_INTERFACE=wlp0s20f3
```

### Logging

```bash
PW_LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR
```

## Metrics Exported

### Power & Energy

- `battery_power` - Battery charge/discharge power (W)
- `site_power` - Grid import/export power (W)
- `load_power` - Home consumption (W)
- `solar_power` - Solar production (W)
- `battery_soe` - State of energy (%)

### Battery Details

- `battery_blocks` - Number of battery blocks
- `nominal_energy_remaining` - Remaining energy (Wh)
- `nominal_full_pack_energy` - Total capacity (Wh)

### Grid & Frequency

- `grid_status` - Grid connection status
- `island_status` - Backup mode status
- `frequency` - Grid frequency (Hz)
- `voltage_*` - Various voltage measurements

### Solar Strings (A-F)

For each string:
- `string_X_connected` - Connection status (0/1)
- `string_X_state` - Operating state
- `string_X_voltage` - DC voltage (V)
- `string_X_current` - DC current (A)
- `string_X_power` - DC power (W)

## Systemd Service Management

### Check Status

```bash
sudo systemctl status powerwall-influx
```

### View Logs

```bash
# Live logs
sudo journalctl -u powerwall-influx -f

# Recent logs
sudo journalctl -u powerwall-influx -n 100

# Logs since last boot
sudo journalctl -b -u powerwall-influx
```

### Control Service

```bash
sudo systemctl start powerwall-influx
sudo systemctl stop powerwall-influx
sudo systemctl restart powerwall-influx
```

### Disable/Enable Autostart

```bash
sudo systemctl disable powerwall-influx  # Disable autostart
sudo systemctl enable powerwall-influx   # Enable autostart
```

### Remove Service

```bash
sudo systemctl stop powerwall-influx
sudo systemctl disable powerwall-influx
sudo rm /etc/systemd/system/powerwall-influx.service
sudo systemctl daemon-reload
```

## Troubleshooting

### Service Won't Start

1. Check logs: `sudo journalctl -u powerwall-influx -n 100`
2. Test manually: `python -m powerwall_service.influx_service --env-file .env --once`
3. Verify credentials in `.env`
4. Check InfluxDB connectivity

### No Data in InfluxDB

1. Verify InfluxDB is running: `curl http://influxdb.home:8086/health`
2. Check InfluxDB token is valid
3. Verify bucket name matches
4. Enable DEBUG logging: `PW_LOG_LEVEL=DEBUG` in `.env`

### Wi-Fi Connection Issues

1. Check NetworkManager is running: `systemctl status NetworkManager`
2. Verify SSID and password in `.env`
3. Test manually: `python -m powerwall_service.connect_wifi --env-file .env`
4. Check Wi-Fi interface name: `ip link show`

### String Data Not Showing

String data is only available during daylight when solar is active. At night, all strings show 0V/0A/0W.

## Project Structure

```
powerwall-influx/
├── powerwall_service/
│   ├── __init__.py           # Package initialization
│   ├── connect_wifi.py       # Wi-Fi auto-connect helper
│   ├── influx_service.py     # Main polling service
│   └── string_status.py      # InfluxDB query tool
├── .env.example              # Example configuration
├── .env                      # Your configuration (not in git)
├── requirements.txt          # Python dependencies
├── README.md                 # This file
├── install-service.sh        # Systemd service installer
├── show-strings.sh           # Shell wrapper for string status
└── powerwall-influx.service  # Systemd service file
```

## Credits

Built with:
- [pypowerwall](https://github.com/jasonacox/pypowerwall) - Tesla Powerwall API client
- [InfluxDB](https://www.influxdata.com/) - Time series database
- [requests](https://requests.readthedocs.io/) - HTTP library

## License

MIT License - See LICENSE file for details

## Contributing

Contributions welcome! Please open an issue or PR.

## Support

For issues or questions:
1. Check the troubleshooting section
2. Enable DEBUG logging
3. Review service logs
4. Open an issue with logs and configuration (redact credentials!)
