#!/bin/bash
#
# Install the Powerwall InfluxDB service as a systemd service
#

set -e

SERVICE_NAME="powerwall-influx"
SERVICE_FILE="powerwall-influx.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "======================================="
echo "Powerwall InfluxDB Service Installer"
echo "======================================="
echo ""

# Check if running from the correct directory
if [ ! -f "$SCRIPT_DIR/$SERVICE_FILE" ]; then
    echo "❌ Error: $SERVICE_FILE not found in $SCRIPT_DIR"
    echo "Please run this script from the powerwall-influx directory"
    exit 1
fi

# Check if .env exists
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "❌ Error: .env file not found"
    echo "Please create .env from .env.example and configure it"
    echo ""
    echo "  cp .env.example .env"
    echo "  nano .env"
    echo ""
    exit 1
fi

echo "📋 Step 1: Testing the service..."
echo ""
cd "$SCRIPT_DIR"
if python3 -m powerwall_service.influx_service --env-file .env --once; then
    echo ""
    echo "✅ Test successful!"
else
    echo ""
    echo "❌ Test failed. Please check your configuration in .env"
    exit 1
fi

echo ""
echo "📦 Step 2: Installing systemd service file..."
sudo cp "$SCRIPT_DIR/$SERVICE_FILE" /etc/systemd/system/
echo "✅ Service file installed to /etc/systemd/system/$SERVICE_FILE"

echo ""
echo "🔄 Step 3: Reloading systemd..."
sudo systemctl daemon-reload
echo "✅ Systemd reloaded"

echo ""
echo "🚀 Step 4: Enabling service to start on boot..."
sudo systemctl enable $SERVICE_NAME
echo "✅ Service enabled"

echo ""
echo "▶️  Step 5: Starting service..."
sudo systemctl start $SERVICE_NAME
echo "✅ Service started"

echo ""
echo "📊 Step 6: Checking service status..."
echo ""
sudo systemctl status $SERVICE_NAME --no-pager -l

echo ""
echo "======================================="
echo "✅ Installation Complete!"
echo "======================================="
echo ""
echo "The service is now running and will start automatically on boot."
echo ""
echo "Useful commands:"
echo "  sudo systemctl status $SERVICE_NAME       - Check status"
echo "  sudo systemctl stop $SERVICE_NAME         - Stop service"
echo "  sudo systemctl start $SERVICE_NAME        - Start service"
echo "  sudo systemctl restart $SERVICE_NAME      - Restart service"
echo "  sudo journalctl -u $SERVICE_NAME -f       - View live logs"
echo ""
echo "To view string data:"
echo "  ./show-strings.sh"
echo "  or: python -m powerwall_service.string_status --env-file .env"
echo ""
