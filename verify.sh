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
    "powerwall_service/health_monitor.py"
    ".env"
    ".env.example"
    "requirements.txt"
    "README.md"
    "HEALTH_MONITORING.md"
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
DEPS=("pypowerwall" "requests" "dotenv" "fastapi" "uvicorn" "paho.mqtt.client")
for dep in "${DEPS[@]}"; do
    if python3 -c "import ${dep/python-/}" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $dep"
    else
        echo -e "  ${YELLOW}⚠${NC} $dep (run: pip install -r requirements.txt)"
    fi
done

echo ""

# Check health monitor module
echo "💚 Checking health monitoring..."
if python3 -c "from powerwall_service.health_monitor import HealthMonitor" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Health monitor module available"
else
    echo -e "  ${RED}✗${NC} Health monitor module import failed"
fi

if [ -f ".env" ]; then
    HEALTH_ENABLED=$(grep "^MQTT_HEALTH_ENABLED=" .env | cut -d'=' -f2)
    if [ "$HEALTH_ENABLED" = "true" ] || [ "$HEALTH_ENABLED" = "True" ] || [ "$HEALTH_ENABLED" = "1" ]; then
        echo -e "  ${GREEN}✓${NC} Health monitoring enabled in .env"
        
        # Check MQTT settings
        MQTT_HOST=$(grep "^MQTT_HOST=" .env | cut -d'=' -f2)
        MQTT_HEALTH_HOST=$(grep "^MQTT_HEALTH_HOST=" .env | cut -d'=' -f2)
        HEALTH_HOST=${MQTT_HEALTH_HOST:-${MQTT_HOST:-mqtt.home}}
        
        if [ -n "$HEALTH_HOST" ]; then
            echo -e "  ${GREEN}✓${NC} Health MQTT host: $HEALTH_HOST"
            
            # Test MQTT connectivity
            if command -v nc &> /dev/null || command -v telnet &> /dev/null; then
                MQTT_PORT=$(grep "^MQTT_HEALTH_PORT=" .env | cut -d'=' -f2)
                MQTT_PORT=${MQTT_PORT:-$(grep "^MQTT_PORT=" .env | cut -d'=' -f2)}
                MQTT_PORT=${MQTT_PORT:-1883}
                
                if command -v nc &> /dev/null; then
                    if nc -z -w 2 "$HEALTH_HOST" "$MQTT_PORT" 2>/dev/null; then
                        echo -e "  ${GREEN}✓${NC} MQTT broker reachable ($HEALTH_HOST:$MQTT_PORT)"
                    else
                        echo -e "  ${YELLOW}⚠${NC} MQTT broker not reachable ($HEALTH_HOST:$MQTT_PORT)"
                    fi
                fi
            fi
        fi
        
        HEALTH_TOPIC=$(grep "^MQTT_HEALTH_TOPIC_PREFIX=" .env | cut -d'=' -f2)
        HEALTH_TOPIC=${HEALTH_TOPIC:-homeassistant/sensor/powerwall_health}
        echo -e "  ${GREEN}✓${NC} Health topic: $HEALTH_TOPIC"
        
        HEALTH_INTERVAL=$(grep "^MQTT_HEALTH_INTERVAL=" .env | cut -d'=' -f2)
        HEALTH_INTERVAL=${HEALTH_INTERVAL:-60}
        echo -e "  ${GREEN}✓${NC} Health interval: ${HEALTH_INTERVAL}s"
    else
        echo -e "  ${YELLOW}⚠${NC} Health monitoring disabled (set MQTT_HEALTH_ENABLED=true)"
    fi
else
    echo -e "  ${YELLOW}⚠${NC} Cannot check health config (.env missing)"
fi

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

SERVICE_RUNNING=false
if systemctl is-active powerwall-influx &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Service running"
    SERVICE_RUNNING=true
else
    echo -e "  ${YELLOW}⚠${NC} Service not running (run: sudo systemctl start powerwall-influx)"
fi

# Check API accessibility if service is running
if [ "$SERVICE_RUNNING" = true ]; then
    echo ""
    echo "🌐 Checking API accessibility..."
    
    # Check root endpoint
    if curl -s -f http://localhost:8000/ >/dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} API accessible at http://localhost:8000/"
    else
        echo -e "  ${RED}✗${NC} API not accessible at http://localhost:8000/"
    fi
    
    # Check health endpoint
    if HEALTH_RESPONSE=$(curl -s http://localhost:8000/health 2>/dev/null); then
        OVERALL_HEALTH=$(echo "$HEALTH_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('overall', 'unknown'))" 2>/dev/null || echo "unknown")
        if [ "$OVERALL_HEALTH" = "true" ] || [ "$OVERALL_HEALTH" = "True" ]; then
            echo -e "  ${GREEN}✓${NC} Health endpoint: overall health is good"
        else
            echo -e "  ${YELLOW}⚠${NC} Health endpoint: overall health is $OVERALL_HEALTH"
        fi
    else
        echo -e "  ${RED}✗${NC} Health endpoint not responding"
    fi
    
    # Check status endpoint
    if STATUS_RESPONSE=$(curl -s http://localhost:8000/status 2>/dev/null); then
        IS_RUNNING=$(echo "$STATUS_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('running', 'unknown'))" 2>/dev/null || echo "unknown")
        if [ "$IS_RUNNING" = "true" ] || [ "$IS_RUNNING" = "True" ]; then
            echo -e "  ${GREEN}✓${NC} Status endpoint: background polling active"
        else
            echo -e "  ${YELLOW}⚠${NC} Status endpoint: background polling is $IS_RUNNING"
        fi
    else
        echo -e "  ${RED}✗${NC} Status endpoint not responding"
    fi
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
echo "  • Check health monitoring: cat HEALTH_MONITORING.md"
echo "  • See full docs: cat README.md"
echo ""
echo "Health Monitoring:"
if [ -f ".env" ] && grep -q "^MQTT_HEALTH_ENABLED=true" .env 2>/dev/null; then
    echo "  • Health metrics will be published to MQTT for Home Assistant"
    echo "  • Check Home Assistant → Settings → Devices → Powerwall Service"
    echo "  • Monitor service logs: sudo journalctl -u powerwall-influx -f | grep -i health"
else
    echo "  • Enable health monitoring by adding MQTT_HEALTH_ENABLED=true to .env"
    echo "  • See HEALTH_MONITORING.md for configuration examples"
fi
echo ""
