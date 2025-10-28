#!/bin/bash
#
# Install the Powerwall InfluxDB service as a systemd service
#

set -e

SERVICE_NAME="powerwall-influx"
SERVICE_FILE="$SERVICE_NAME.service"
SERVICE_TEMPLATE="powerwall-influx.service.template"
GENERATED_SERVICE_FILE="powerwall-influx.service.local"
SERVICE_INSTALL_PATH="/etc/systemd/system/$SERVICE_FILE"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_TEST=${PW_INSTALLER_SKIP_TEST:-${1:-}}

is_truthy() {
    case "$1" in
        1|true|TRUE|y|Y|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

prompt_with_default() {
    local prompt="$1"
    local default="$2"
    local value

    if [ -n "$default" ]; then
        read -r -p "$prompt [$default]: " value
    else
        read -r -p "$prompt: " value
    fi

    if [ -z "$value" ]; then
        value="$default"
    fi

    echo "$value"
}

configure_service_unit() {
    local template="$SCRIPT_DIR/$SERVICE_TEMPLATE"
    local target="$SCRIPT_DIR/$GENERATED_SERVICE_FILE"

    echo "🧩 Step 0: Configuring systemd unit..."
    echo ""

    local default_user default_group default_workdir default_venv_bin default_env_file default_python_bin
    default_user="$(id -un)"
    default_group="$(id -gn)"
    default_workdir="$SCRIPT_DIR"

    if [ -d "$SCRIPT_DIR/.venv/bin" ]; then
        default_venv_bin="$SCRIPT_DIR/.venv/bin"
    else
        default_venv_bin="$(dirname "$(command -v python3)")"
    fi

    default_env_file="$SCRIPT_DIR/.env"

    if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
        default_python_bin="$SCRIPT_DIR/.venv/bin/python"
    else
        default_python_bin="$(command -v python3)"
    fi

    if [ -f "$target" ]; then
        local existing_user existing_group existing_workdir existing_path existing_env existing_exec

        existing_user="$(grep '^User=' "$target" | head -n1 | cut -d= -f2)"
        [ -n "$existing_user" ] && default_user="$existing_user"

        existing_group="$(grep '^Group=' "$target" | head -n1 | cut -d= -f2)"
        [ -n "$existing_group" ] && default_group="$existing_group"

        existing_workdir="$(grep '^WorkingDirectory=' "$target" | head -n1 | cut -d= -f2)"
        [ -n "$existing_workdir" ] && default_workdir="$existing_workdir"

        existing_path="$(grep '^Environment="PATH=' "$target" | head -n1)"
        if [ -n "$existing_path" ]; then
            existing_path="${existing_path#Environment=\"PATH=}"
            existing_path="${existing_path%%:*}"
            existing_path="${existing_path%\"}"
            [ -n "$existing_path" ] && default_venv_bin="$existing_path"
        fi

        existing_env="$(grep '^Environment="POWERWALL_ENV_FILE=' "$target" | head -n1)"
        if [ -n "$existing_env" ]; then
            existing_env="${existing_env#Environment=\"POWERWALL_ENV_FILE=}"
            existing_env="${existing_env%\"}"
            [ -n "$existing_env" ] && default_env_file="$existing_env"
        fi

        existing_exec="$(grep '^ExecStart=' "$target" | head -n1)"
        if [ -n "$existing_exec" ]; then
            existing_exec="${existing_exec#ExecStart=}"
            existing_exec="${existing_exec%% *}"
            [ -n "$existing_exec" ] && default_python_bin="$existing_exec"
        fi

        echo "ℹ️  Found existing generated unit at $target."
        read -r -p "Reuse this file without changes? [Y/n]: " reuse_choice
        if [[ ! "$reuse_choice" =~ ^[Nn]$ ]]; then
            echo "✅ Reusing existing systemd unit."
            echo ""
            return
        fi
    fi

    local service_user service_group working_directory venv_bin env_file python_bin
    service_user="$(prompt_with_default "Service user" "$default_user")"
    service_group="$(prompt_with_default "Service group" "$default_group")"
    working_directory="$(prompt_with_default "Repository path" "$default_workdir")"
    venv_bin="$(prompt_with_default "Virtualenv bin directory" "$default_venv_bin")"
    env_file="$(prompt_with_default ".env path" "$default_env_file")"
    python_bin="$(prompt_with_default "Python interpreter" "$default_python_bin")"

    python3 - "$template" "$target" \
        "$service_user" "$service_group" "$working_directory" \
        "$venv_bin" "$env_file" "$python_bin" <<'PYCODE'
import sys
from pathlib import Path

template_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
service_user, service_group, working_directory, venv_bin, env_file, python_bin = sys.argv[3:]

mapping = {
    "SERVICE_USER": service_user,
    "SERVICE_GROUP": service_group,
    "WORKING_DIRECTORY": working_directory,
    "VENV_BIN": venv_bin,
    "ENV_FILE": env_file,
    "PYTHON_BIN": python_bin,
}

content = template_path.read_text()
for key, value in mapping.items():
    content = content.replace(f"{{{{{key}}}}}", value)

target_path.write_text(content)
PYCODE

    echo ""
    echo "✅ Generated $target"
    echo ""
}

echo "======================================="
echo "Powerwall InfluxDB Service Installer"
echo "======================================="
echo ""

# Check if running from the correct directory
if [ ! -f "$SCRIPT_DIR/$SERVICE_TEMPLATE" ]; then
    echo "❌ Error: $SERVICE_TEMPLATE not found in $SCRIPT_DIR"
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

configure_service_unit

if is_truthy "$SKIP_TEST"; then
    echo "📋 Step 1: Testing the service... (skipped)"
    echo "⏭️  Skipping smoke test because PW_INSTALLER_SKIP_TEST is set."
else
    echo "📋 Step 1: Testing the service..."
    echo ""
    cd "$SCRIPT_DIR"
    set +e
    python3 -m powerwall_service.cli poll --no-push --pretty
    test_status=$?
    set -e
    if [ $test_status -eq 0 ]; then
        echo ""
        echo "✅ Test successful!"
    else
        echo ""
        echo "❌ Test failed (exit code $test_status)."
        read -r -p "Continue installation anyway? [y/N]: " continue_choice
        if [[ ! "$continue_choice" =~ ^[Yy]$ ]]; then
            echo "Abort requested. Fix the configuration in .env and rerun the installer."
            exit 1
        fi
        echo "➡️  Proceeding despite failed connectivity test."
    fi
fi

echo ""
echo "📦 Step 2: Installing systemd service file..."
if [ ! -f "$SCRIPT_DIR/$GENERATED_SERVICE_FILE" ]; then
    echo "❌ Error: $GENERATED_SERVICE_FILE not found."
    echo "Run this installer again and generate the systemd unit file when prompted."
    exit 1
fi
sudo cp "$SCRIPT_DIR/$GENERATED_SERVICE_FILE" "$SERVICE_INSTALL_PATH"
echo "✅ Service file installed to $SERVICE_INSTALL_PATH"

echo ""
echo "🔄 Step 3: Reloading systemd..."
sudo systemctl daemon-reload
echo "✅ Systemd reloaded"

echo ""
echo "🚀 Step 4: Enabling service to start on boot..."
sudo systemctl enable $SERVICE_NAME
echo "✅ Service enabled"

echo ""
if systemctl is-active --quiet $SERVICE_NAME; then
    echo "▶️  Step 5: Restarting service to apply changes..."
    sudo systemctl restart $SERVICE_NAME
    echo "✅ Service restarted"
else
    echo "▶️  Step 5: Starting service..."
    sudo systemctl start $SERVICE_NAME
    echo "✅ Service started"
fi

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
