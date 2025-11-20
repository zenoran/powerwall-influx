#!/usr/bin/env python3
"""Connect to the Tesla Powerwall Wi-Fi access point and download gateway statistics.

This helper is designed for Linux systems that use NetworkManager (and expose the
``nmcli`` command). It automates joining the Powerwall's access point and then uses
``pypowerwall`` to pull a snapshot of status metrics, saving them to disk as JSON.

Typical usage::

    python3 -m powerwall_service.connect_wifi --ssid PW-123456 --wifi-pass mysecret \
        --gw-pass mysecret --output powerwall-stats.json

Depending on your Powerwall firmware you may only need the gateway password
(``--gw-pass``); on some setups it matches the Wi-Fi password printed on the
Gateway's label. For Powerwall 2/+ hybrid mode you can optionally provide
customer credentials via ``--email`` and ``--password`` in addition to the
gateway password.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pypowerwall

LOGGER = logging.getLogger("powerwall_service.connect_wifi")


class WiFiConnectionError(RuntimeError):
    """Raised when the script fails to establish the requested Wi-Fi connection."""


def _check_nmcli_available() -> None:
    if shutil.which("nmcli") is None:
        raise WiFiConnectionError(
            "The 'nmcli' command was not found. Install NetworkManager or adjust the script to use your Wi-Fi manager."
        )


def _run_nmcli(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["nmcli", *args]
    LOGGER.debug("Running command: %s", " ".join(cmd))
    try:
        return subprocess.run(cmd, check=check, capture_output=True, text=True)
    except FileNotFoundError as exc:  # pragma: no cover - handled earlier by _check_nmcli_available
        raise WiFiConnectionError("nmcli command is unavailable.") from exc
    except subprocess.CalledProcessError as exc:
        if check:
            stderr = exc.stderr.strip() if exc.stderr else exc.stdout.strip()
            raise WiFiConnectionError(f"nmcli failed: {stderr}") from exc
        raise


def _is_connected_to_ssid(ssid: str) -> bool:
    proc = _run_nmcli(["-t", "-f", "ACTIVE,SSID", "device", "wifi"], check=False)
    for line in proc.stdout.splitlines():
        if not line:
            continue
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        active, current_ssid = parts
        if active == "yes" and current_ssid == ssid:
            return True
    return False


def _active_connection_name(interface: Optional[str]) -> Optional[str]:
    proc = _run_nmcli(["-t", "-f", "DEVICE,STATE,CONNECTION", "device", "status"], check=False)
    for line in proc.stdout.splitlines():
        if not line:
            continue
        fields = line.split(":")
        if len(fields) != 3:
            continue
        device, state, connection = fields
        if interface and device != interface:
            continue
        if state == "connected":
            return connection or None
    return None


def _is_wifi_device(interface: str) -> bool:
    proc = _run_nmcli(["-t", "-f", "GENERAL.TYPE", "device", "show", interface], check=False)
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        if "wifi" in line.lower():
            return True
    return False


def _find_connection_by_ssid(ssid: str) -> Optional[str]:
    """Return the connection profile name for the given SSID, or None."""
    proc = _run_nmcli(["-t", "-f", "NAME,TYPE", "connection", "show"], check=False)
    if proc.returncode != 0:
        return None
    # Look for wifi connections and check if they match the SSID
    for line in proc.stdout.splitlines():
        if not line or ":wifi" not in line.lower():
            continue
        parts = line.split(":")
        if len(parts) >= 2:
            conn_name = parts[0]
            # Check if this connection profile is for our SSID
            detail_proc = _run_nmcli(["-t", "-f", "802-11-wireless.ssid", "connection", "show", conn_name], check=False)
            if detail_proc.returncode == 0:
                for detail_line in detail_proc.stdout.splitlines():
                    if detail_line.strip() and ssid in detail_line:
                        return conn_name
    return None


def connect_to_wifi(ssid: str, password: Optional[str], interface: Optional[str], timeout: int) -> bool:
    """Connect to the specified WiFi network.
    
    Args:
        ssid: The WiFi network SSID to connect to
        password: The WiFi password (optional)
        interface: The network interface to use (optional)
        timeout: Maximum time in seconds to wait for connection
        
    Returns:
        True if a new connection was established, False if already connected
        
    Raises:
        WiFiConnectionError: If connection fails
    """
    if _is_connected_to_ssid(ssid):
        LOGGER.info("Already connected to Wi-Fi SSID '%s'.", ssid)
        return False  # Already connected - no reconnection performed

    # First try to activate existing connection profile
    existing_connection = _find_connection_by_ssid(ssid)
    if existing_connection:
        LOGGER.info("Found existing connection profile '%s' for SSID '%s', attempting activation...", existing_connection, ssid)
        proc = _run_nmcli(["connection", "up", existing_connection], check=False)
        if proc.returncode == 0:
            # Wait briefly to confirm connection
            deadline = time.time() + min(timeout, 15)
            while time.time() < deadline:
                if _is_connected_to_ssid(ssid):
                    LOGGER.info("Successfully activated existing connection '%s'.", existing_connection)
                    return True  # Successfully reconnected
                time.sleep(1)
        else:
            LOGGER.debug("Failed to activate existing connection profile: %s", proc.stderr.strip())

    # Fall back to creating new connection
    args: list[str] = ["device", "wifi", "connect", ssid]
    if password:
        args.extend(["password", password])
    if interface:
        if _is_wifi_device(interface):
            args.extend(["ifname", interface])
        else:
            LOGGER.warning("Interface '%s' is not a Wi-Fi device; letting NetworkManager pick automatically.", interface)

    proc = _run_nmcli(args, check=False)
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "Unknown nmcli error"
        raise WiFiConnectionError(f"Failed to connect to '{ssid}': {message}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_connected_to_ssid(ssid):
            LOGGER.info("Successfully connected to '%s'.", ssid)
            return True  # Successfully reconnected
        time.sleep(1)

    active_name = _active_connection_name(interface)
    raise WiFiConnectionError(
        f"Timed out after {timeout}s waiting to join '{ssid}'. Last active connection: {active_name or 'none'}"
    )


def fetch_powerwall_stats(
    host: str,
    gateway_password: Optional[str],
    customer_password: Optional[str],
    email: Optional[str],
    timezone_name: str,
    cache_expire: int,
    timeout: int,
) -> dict:
    LOGGER.debug("Connecting to Powerwall host %s", host)
    gw_pwd = gateway_password or customer_password
    email_value = email or "nobody@nowhere.com"
    powerwall = pypowerwall.Powerwall(
        host=host,
        password=customer_password or "",
        email=email_value,
        timezone=timezone_name,
        pwcacheexpire=cache_expire,
        timeout=timeout,
        gw_pwd=gw_pwd,
        auto_select=True,
    )

    try:
        stats = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "site_name": powerwall.site_name(),
            "firmware": powerwall.version(),
            "din": powerwall.din(),
            "battery_percentage": powerwall.level(),
            "power": powerwall.power(),
            "vitals": powerwall.vitals(),
        }
    finally:
        try:
            powerwall.client.close_session()
        except Exception as exc:  # pragma: no cover - best effort cleanup
            LOGGER.debug("Failed to close Powerwall session cleanly: %s", exc)

    return stats


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssid", required=True, help="Powerwall Wi-Fi SSID to join")
    parser.add_argument("--wifi-pass", dest="wifi_pass", help="Wi-Fi password for the SSID")
    parser.add_argument(
        "--gw-pass",
        dest="gw_pass",
        help="Gateway password (defaults to Wi-Fi password if omitted). Required for TEDAPI vitals.",
    )
    parser.add_argument(
        "--host",
        default="192.168.91.1",
        help="Powerwall gateway address once connected (default: %(default)s)",
    )
    parser.add_argument(
        "--email",
        help="Customer login email (Powerwall 2/+ hybrid mode). Leave blank for TEDAPI-only mode.",
    )
    parser.add_argument(
        "--password",
        dest="customer_password",
        help="Customer login password for hybrid/local mode. Leave blank for TEDAPI-only mode.",
    )
    parser.add_argument(
        "--timezone",
        default="UTC",
        help="IANA timezone Powerwall should use (default: %(default)s)",
    )
    parser.add_argument(
        "--interface",
        help="Name of the Wi-Fi interface to use (e.g. wlan0). If omitted, NetworkManager picks one automatically.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Seconds to wait for the Wi-Fi connection to complete (default: %(default)s)",
    )
    parser.add_argument(
        "--cache-expire",
        type=int,
        default=5,
        help="Seconds before cached Powerwall API responses expire (default: %(default)s)",
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=10,
        help="HTTP timeout when talking to the Powerwall (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("powerwall_stats.json"),
        help="Path to write the JSON snapshot (default: %(default)s)",
    )
    parser.add_argument("--skip-wifi", action="store_true", help="Skip Wi-Fi join and assume connectivity is already in place.")
    parser.add_argument("--debug", action="store_true", help="Enable verbose logging.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(levelname)s %(message)s")

    LOGGER.info("Starting Powerwall Wi-Fi connection helper")
    if not args.skip_wifi:
        _check_nmcli_available()
        try:
            connect_to_wifi(args.ssid, args.wifi_pass, args.interface, args.timeout)
        except WiFiConnectionError as exc:
            LOGGER.error("Wi-Fi connection failed: %s", exc)
            return 2
    else:
        LOGGER.info("Skipping Wi-Fi connection as requested")

    try:
        stats = fetch_powerwall_stats(
            host=args.host,
            gateway_password=args.gw_pass or args.wifi_pass,
            customer_password=args.customer_password,
            email=args.email,
            timezone_name=args.timezone,
            cache_expire=args.cache_expire,
            timeout=args.request_timeout,
        )
    except Exception as exc:
        LOGGER.error("Failed to fetch Powerwall stats: %s", exc)
        return 3

    try:
        args.output.write_text(json.dumps(stats, indent=2, sort_keys=True))
    except OSError as exc:
        LOGGER.error("Could not write stats to %s: %s", args.output, exc)
        return 4

    LOGGER.info("Wrote Powerwall snapshot to %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
