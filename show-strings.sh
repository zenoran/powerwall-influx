#!/bin/bash
# Wrapper script to display Powerwall string status from InfluxDB

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE"
    exit 1
fi

cd "${SCRIPT_DIR}" || exit 1
python3 -m powerwall_service.string_status --env-file "$ENV_FILE" "$@"
