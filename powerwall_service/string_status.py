#!/usr/bin/env python3
"""
Query InfluxDB for current Powerwall string status and display in a table.

Usage:
    python3 -m powerwall_service.string_status [--env-file PATH]
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
import requests


def load_env_file(path: Path) -> None:
    """Load environment variables from a .env file."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def query_influx(url: str, org: str, bucket: str, token: str, verify_tls: bool = True) -> Optional[Dict[str, Any]]:
    """Query InfluxDB for the latest string status."""
    query_url = f"{url.rstrip('/')}/api/v2/query"
    
    # Flux query to get the latest values for all string fields
    flux_query = f'''
from(bucket: "{bucket}")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "powerwall")
  |> filter(fn: (r) => r._field =~ /^string_/)
  |> last()
'''
    
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/vnd.flux",
        "Accept": "application/json",
    }
    
    params = {
        "org": org,
    }
    
    try:
        response = requests.post(
            query_url,
            params=params,
            headers=headers,
            data=flux_query,
            timeout=10,
            verify=verify_tls,
        )
        
        if response.status_code != 200:
            print(f"Error querying InfluxDB: {response.status_code} {response.text}", file=sys.stderr)
            return None
        
        # Parse CSV response
        lines = response.text.strip().split('\n')
        if len(lines) < 2:
            return None
        
        # Parse header
        header = lines[0].split(',')
        field_idx = header.index('_field') if '_field' in header else None
        value_idx = header.index('_value') if '_value' in header else None
        time_idx = header.index('_time') if '_time' in header else None
        
        if field_idx is None or value_idx is None:
            print("Error: Could not parse InfluxDB response", file=sys.stderr)
            return None
        
        # Parse data rows
        data = {}
        latest_time = None
        for line in lines[1:]:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.split(',')
            if len(parts) <= max(field_idx, value_idx):
                continue
            field_name = parts[field_idx]
            value = parts[value_idx]
            if time_idx and parts[time_idx]:
                latest_time = parts[time_idx]
            data[field_name] = value
        
        return {"data": data, "time": latest_time}
    
    except Exception as e:
        print(f"Error querying InfluxDB: {e}", file=sys.stderr)
        return None


def parse_value(value: str) -> Any:
    """Parse a value from InfluxDB CSV format."""
    if value.lower() == 'true':
        return True
    elif value.lower() == 'false':
        return False
    try:
        if '.' in value:
            return float(value)
        return int(value)
    except ValueError:
        # Remove quotes if present
        return value.strip('"')


def display_string_table(data: Dict[str, str]) -> None:
    """Display string status in a formatted table."""
    # Organize data by string
    strings = {}
    for field, value in data.items():
        # Skip non-string fields
        if not field.startswith('string_'):
            continue
        
        # Parse field name like "string_a_voltage_v" or "string_stringa_connected"
        parts = field.split('_')
        if len(parts) < 3:
            continue
        
        # Handle both "string_a_..." and "string_stringa_..." formats
        if parts[1].startswith('string'):
            # Format: string_stringa_connected
            string_name = parts[1].replace('string', '').upper()
            metric_name = '_'.join(parts[2:])
        else:
            # Format: string_a_voltage_v
            string_name = parts[1].upper()
            metric_name = '_'.join(parts[2:])
        
        if string_name not in strings:
            strings[string_name] = {}
        
        strings[string_name][metric_name] = parse_value(value)
    
    if not strings:
        print("No string data found in InfluxDB")
        return
    
    # Print header
    print("\n" + "=" * 100)
    print("POWERWALL SOLAR STRING STATUS")
    print("=" * 100)
    
    # Column headers
    headers = ["String", "Connected", "State", "Voltage (V)", "Current (A)", "Power (W)"]
    col_widths = [10, 12, 20, 14, 14, 12]
    
    header_line = ""
    for header, width in zip(headers, col_widths):
        header_line += f"{header:<{width}}"
    print(header_line)
    print("-" * 100)
    
    # Print data for each string
    for string_name in sorted(strings.keys()):
        string_data = strings[string_name]
        
        connected = string_data.get('connected', False)
        state = string_data.get('state', 'Unknown')
        voltage = string_data.get('voltage_v', 0.0)
        current = string_data.get('current_a', 0.0)
        power = string_data.get('power_w', 0.0)
        
        # Format connected status with color (if terminal supports it)
        connected_str = "✓ Yes" if connected else "✗ No"
        
        row = f"{'String ' + string_name:<{col_widths[0]}}"
        row += f"{connected_str:<{col_widths[1]}}"
        row += f"{state:<{col_widths[2]}}"
        row += f"{voltage:>{col_widths[3]-1}.1f} "
        row += f"{current:>{col_widths[4]-1}.2f} "
        row += f"{power:>{col_widths[5]-1}.1f} "
        
        print(row)
    
    print("=" * 100)
    
    # Summary
    total_strings = len(strings)
    connected_count = sum(1 for s in strings.values() if s.get('connected', False))
    total_power = sum(s.get('power_w', 0.0) for s in strings.values())
    
    print(f"\nSummary: {connected_count}/{total_strings} strings connected | Total Power: {total_power:.1f} W")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Query and display Powerwall string status from InfluxDB")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Path to .env file with InfluxDB credentials",
    )
    args = parser.parse_args()
    
    # Load environment variables
    if args.env_file:
        load_env_file(args.env_file)
    
    # Get InfluxDB configuration from environment
    influx_url = os.environ.get("INFLUX_URL", "http://influxdb.home:8086")
    influx_org = os.environ.get("INFLUX_ORG", "home")
    influx_bucket = os.environ.get("INFLUX_BUCKET", "powerwall")
    influx_token = os.environ.get("INFLUX_TOKEN", "")
    influx_verify_tls = os.environ.get("INFLUX_VERIFY_TLS", "true").lower() in {"true", "1", "yes"}
    
    if not influx_token:
        print("Error: INFLUX_TOKEN not set. Use --env-file or set environment variable.", file=sys.stderr)
        return 1
    
    # Query InfluxDB
    print("Querying InfluxDB for latest string status...")
    result = query_influx(influx_url, influx_org, influx_bucket, influx_token, influx_verify_tls)
    
    if not result:
        print("No data retrieved from InfluxDB", file=sys.stderr)
        return 1
    
    # Display results
    if result.get("time"):
        print(f"Data as of: {result['time']}")
    
    display_string_table(result["data"])
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
