#!/bin/bash
#
# Quick setup script for Powerwall InfluxDB Service
#

set -e

echo "======================================="
echo "Powerwall InfluxDB Service Setup"
echo "======================================="
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python version: $PYTHON_VERSION"

# Option to use virtual environment
echo ""
read -p "Create a virtual environment? (recommended) [Y/n]: " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    echo ""
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    echo "✅ Virtual environment created and activated"
    PYTHON_CMD="./venv/bin/python3"
    PIP_CMD="./venv/bin/pip"
else
    PYTHON_CMD="python3"
    PIP_CMD="pip3"
fi

# Install dependencies
echo ""
echo "📥 Installing dependencies..."
$PIP_CMD install -r requirements.txt
echo "✅ Dependencies installed"

# Check if .env exists
echo ""
if [ -f ".env" ]; then
    echo "✓ Configuration file .env already exists"
else
    echo "📝 Creating .env from .env.example..."
    cp .env.example .env
    echo "✅ Created .env file"
    echo ""
    echo "⚠️  IMPORTANT: Edit .env with your configuration:"
    echo "   - InfluxDB URL, token, org, and bucket"
    echo "   - Powerwall IP and credentials"
    echo "   - Wi-Fi settings (if using auto-connect)"
    echo ""
    read -p "Press Enter to edit .env now (or Ctrl+C to skip)..."
    ${EDITOR:-nano} .env
fi

# Test the service
echo ""
echo "🧪 Testing service configuration..."
if PW_LOG_LEVEL=INFO $PYTHON_CMD -m powerwall_service.cli poll --pretty; then
    echo ""
    echo "✅ Configuration test successful!"
else
    echo ""
    echo "❌ Configuration test failed. Please check your .env file"
    exit 1
fi

echo ""
echo "======================================="
echo "✅ Setup Complete!"
echo "======================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Test manually:"
echo "   python -m powerwall_service.cli poll --pretty"
echo ""
echo "2. Run the HTTP service:"
echo "   python -m powerwall_service.cli serve"
echo ""
echo "3. Install as systemd service:"
echo "   ./install-service.sh"
echo ""
echo "4. View string data:"
echo "   python -m powerwall_service.string_status --env-file .env"
echo ""
echo "See README.md for more information."
echo ""
