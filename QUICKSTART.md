# Quick Start Guide

## New Installation

### 1. Install Dependencies

If using the global virtual environment:
```bash
cd ~/dev/powerwall-influx
# Dependencies should already be in ~/dev/global-venv
```

Or create a new virtual environment:
```bash
cd ~/dev/powerwall-influx
./setup.sh
```

### 2. Configure

Your `.env` file is already set up. If you need to make changes:
```bash
nano .env
```

### 3. Test

```bash
cd ~/dev/powerwall-influx
python3 -m powerwall_service.influx_service --env-file .env --once
```

### 4. Install Service

Update the existing systemd service to use the new location:

```bash
cd ~/dev/powerwall-influx
sudo systemctl stop powerwall-influx
sudo cp powerwall-influx.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start powerwall-influx
sudo systemctl status powerwall-influx
```

## Update Shell Aliases

Edit your `~/.zshrc` and update the aliases to point to the new location:

```bash
# Powerwall InfluxDB service aliases
alias pw-strings='cd ~/dev/powerwall-influx && python3 -m powerwall_service.string_status --env-file .env'
alias pw-influx-once='cd ~/dev/powerwall-influx && python3 -m powerwall_service.influx_service --env-file .env --once'
alias pw-influx='cd ~/dev/powerwall-influx && echo "🚀 Starting Powerwall InfluxDB service (Ctrl+C to stop)..." && python3 -m powerwall_service.influx_service --env-file .env'
alias pw-connect='cd ~/dev/powerwall-influx && python3 -m powerwall_service.connect_wifi --env-file .env'

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

Then reload:
```bash
source ~/.zshrc
```

## Verify Everything Works

```bash
# Test service
pw-influx-once

# View strings
pw-strings

# Check systemd service
sudo systemctl status powerwall-influx
```

## What Changed?

- **Old location**: `~/dev/pypowerwall/src/powerwall_service/`
- **New location**: `~/dev/powerwall-influx/`
- **Benefits**:
  - Standalone repository with its own `requirements.txt`
  - Cleaner project structure
  - Easy to version control and share
  - No dependency on the pypowerwall source repo
  - Can install pypowerwall via pip instead

## Next Steps

1. Initialize git repository (optional):
   ```bash
   cd ~/dev/powerwall-influx
   git init
   git add .
   git commit -m "Initial commit"
   ```

2. Push to GitHub/GitLab (optional):
   ```bash
   git remote add origin <your-repo-url>
   git push -u origin main
   ```

3. Old location can now be removed:
   ```bash
   # After verifying everything works
   rm -rf ~/dev/pypowerwall/src/powerwall_service
   rm ~/dev/pypowerwall/src/.env
   ```
