#!/bin/bash
#
# Verify the Powerwall InfluxDB service installation
#

echo "======================================="
echo "Powerwall InfluxDB Service Verification"
echo "======================================="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

cd "$(dirname "$0")" || exit 1

# Check files
echo "📁 Checking files..."
FILES=(
    "powerwall_service/__init__.py"
    "powerwall_service/app.py"
    "powerwall_service/cli.py"
    "powerwall_service/connect_wifi.py"
    "powerwall_service/string_status.py"
    ".env"
    ".env.example"
    "requirements.txt"
    "README.md"
    "install-service.sh"
    "show-strings.sh"
)

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "  ${GREEN}✓${NC} $file"
    else
        echo -e "  ${RED}✗${NC} $file (missing!)"
    fi
done

echo ""

# Check Python
echo "🐍 Checking Python..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1)
    echo -e "  ${GREEN}✓${NC} $PYTHON_VERSION"
else
    echo -e "  ${RED}✗${NC} Python 3 not found"
fi

echo ""

# Check dependencies
echo "📦 Checking dependencies..."
DEPS=("pypowerwall" "requests" "dotenv" "fastapi" "uvicorn")
for dep in "${DEPS[@]}"; do
    if python3 -c "import ${dep/python-/}" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $dep"
    else
        echo -e "  ${YELLOW}⚠${NC} $dep (run: pip install -r requirements.txt)"
    fi
done

echo ""

# Check .env configuration
echo "⚙️  Checking configuration..."
if [ -f ".env" ]; then
    if grep -q "INFLUX_TOKEN=your-influxdb-token-here" .env; then
        echo -e "  ${YELLOW}⚠${NC} .env needs configuration (still has placeholder token)"
    elif grep -q "INFLUX_TOKEN=.*" .env; then
        echo -e "  ${GREEN}✓${NC} .env configured"
    else
        echo -e "  ${RED}✗${NC} .env missing INFLUX_TOKEN"
    fi
else
    echo -e "  ${RED}✗${NC} .env file missing"
fi

echo ""

# Test service
echo "🧪 Testing service..."
if python3 -m powerwall_service.cli poll --pretty >/tmp/powerwall_influx_verify.json 2>&1; then
    echo -e "  ${GREEN}✓${NC} Service poll completed"
else
    echo -e "  ${YELLOW}⚠${NC} Service poll failed (check logs above)"
fi
rm -f /tmp/powerwall_influx_verify.json

echo ""

# Check systemd service
echo "🔧 Checking systemd service..."
if systemctl is-enabled powerwall-influx &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Service enabled (will start on boot)"
else
    echo -e "  ${YELLOW}⚠${NC} Service not enabled (run: ./install-service.sh)"
fi

if systemctl is-active powerwall-influx &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Service running"
else
    echo -e "  ${YELLOW}⚠${NC} Service not running (run: sudo systemctl start powerwall-influx)"
fi

echo ""

# Check network
echo "🌐 Checking connectivity..."
if ping -c 1 -W 2 192.168.91.1 &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Powerwall reachable (192.168.91.1)"
else
    echo -e "  ${YELLOW}⚠${NC} Powerwall not reachable (may need Wi-Fi connection)"
fi

if [ -f ".env" ]; then
    INFLUX_URL=$(grep "^INFLUX_URL=" .env | cut -d'=' -f2)
    if [ -n "$INFLUX_URL" ]; then
        INFLUX_HOST=$(echo "$INFLUX_URL" | sed -E 's|https?://([^:/]+).*|\1|')
        if ping -c 1 -W 2 "$INFLUX_HOST" &>/dev/null; then
            echo -e "  ${GREEN}✓${NC} InfluxDB reachable ($INFLUX_HOST)"
        else
            echo -e "  ${YELLOW}⚠${NC} InfluxDB not reachable ($INFLUX_HOST)"
        fi
    fi
fi

echo ""
echo "======================================="
echo "Verification Complete!"
echo "======================================="
echo ""
echo "Next steps:"
echo "  • View string data: ./show-strings.sh"
echo "  • Test poll: python3 -m powerwall_service.cli poll --pretty"
echo "  • View logs: sudo journalctl -u powerwall-influx -f"
echo "  • See full docs: cat README.md"
echo ""
