#!/usr/bin/env python3
"""Powerwall polling service that pushes metrics to InfluxDB.

This helper is meant to be run under a process manager (systemd, launchd, etc.)
and will periodically connect to a Tesla Powerwall gateway, pull a metrics
snapshot, and write the results to an InfluxDB v2 bucket.

Configuration is provided via environment variables. A `.env` file (in the
same directory as this module or specified with ``--env-file``) may be used to
source variables before runtime. See ``powerwall_service/powerwall.env.example``
for a template.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

import requests

import pypowerwall

from .connect_wifi import (
    WiFiConnectionError,
    _check_nmcli_available,
    connect_to_wifi,
)

LOGGER = logging.getLogger("powerwall_service.influx_service")

DEFAULT_ENV_PATH = Path(__file__).resolve().parent / "powerwall.env"


@dataclass
class ServiceConfig:
    influx_url: str
    influx_org: str
    influx_bucket: str
    influx_token: str
    measurement: str
    influx_timeout: float
    influx_verify_tls: bool
    poll_interval: float
    host: str
    timezone_name: str
    cache_expire: int
    request_timeout: int
    wifi_ssid: Optional[str]
    wifi_password: Optional[str]
    wifi_interface: Optional[str]
    connect_wifi: bool
    gateway_password: Optional[str]
    customer_email: Optional[str]
    customer_password: Optional[str]
    log_level: str


def load_env_file(path: Path) -> None:
    """Populate os.environ with simple KEY=VALUE pairs from a .env style file."""
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


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        LOGGER.warning("Invalid float for %s: %s", name, value)
        return default


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        LOGGER.warning("Invalid integer for %s: %s", name, value)
        return default


def build_config() -> ServiceConfig:
    cfg = ServiceConfig(
        influx_url=os.environ.get("INFLUX_URL", "http://influxdb.home:8086"),
        influx_org=os.environ.get("INFLUX_ORG", "home"),
        influx_bucket=os.environ.get("INFLUX_BUCKET", "powerwall"),
        influx_token=os.environ.get("INFLUX_TOKEN", ""),
        measurement=os.environ.get("INFLUX_MEASUREMENT", "powerwall"),
        influx_timeout=env_float("INFLUX_TIMEOUT", 10.0),
        influx_verify_tls=env_bool("INFLUX_VERIFY_TLS", True),
        poll_interval=env_float("PW_POLL_INTERVAL", 60.0),
        host=os.environ.get("PW_HOST", "192.168.91.1"),
        timezone_name=os.environ.get("PW_TIMEZONE", "UTC"),
        cache_expire=env_int("PW_CACHE_EXPIRE", 5),
        request_timeout=env_int("PW_REQUEST_TIMEOUT", 10),
        wifi_ssid=os.environ.get("PW_WIFI_SSID"),
        wifi_password=os.environ.get("PW_WIFI_PASSWORD"),
        wifi_interface=os.environ.get("PW_WIFI_INTERFACE"),
        connect_wifi=env_bool("PW_CONNECT_WIFI", False),
        gateway_password=os.environ.get("PW_GATEWAY_PASSWORD"),
        customer_email=os.environ.get("PW_CUSTOMER_EMAIL"),
        customer_password=os.environ.get("PW_CUSTOMER_PASSWORD"),
        log_level=os.environ.get("PW_LOG_LEVEL", "INFO"),
    )
    if not cfg.influx_token:
        raise RuntimeError("INFLUX_TOKEN must be set (consider using a .env file).")
    if not cfg.influx_org:
        raise RuntimeError("INFLUX_ORG must be set.")
    if not cfg.influx_bucket:
        raise RuntimeError("INFLUX_BUCKET must be set.")
    if cfg.poll_interval <= 0:
        raise RuntimeError("PW_POLL_INTERVAL must be greater than zero.")
    return cfg


class PowerwallPoller:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._powerwall: Optional[pypowerwall.Powerwall] = None

    def close(self) -> None:
        if self._powerwall and getattr(self._powerwall, "client", None):
            try:
                self._powerwall.client.close_session()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                LOGGER.debug("Failed to close Powerwall session: %s", exc)
        self._powerwall = None

    def _ensure_connection(self) -> None:
        if self._powerwall and self._powerwall.is_connected():
            return
        self.close()
        gw_pwd = (
            self._config.gateway_password
            or self._config.wifi_password
            or self._config.customer_password
        )
        email = self._config.customer_email or "nobody@nowhere.com"
        password = self._config.customer_password or ""
        LOGGER.debug(
            "Connecting to Powerwall host=%s email=%s auto_select=True", self._config.host, email
        )
        self._powerwall = pypowerwall.Powerwall(
            host=self._config.host,
            password=password,
            email=email,
            timezone=self._config.timezone_name,
            pwcacheexpire=self._config.cache_expire,
            timeout=self._config.request_timeout,
            gw_pwd=gw_pwd,
            auto_select=True,
            retry_modes=True,
        )

    def fetch_snapshot(self) -> Dict[str, object]:
        self._ensure_connection()
        assert self._powerwall is not None
        powerwall = self._powerwall

        try:
            power_values = powerwall.power()
        except Exception as exc:
            LOGGER.debug("power() failed; skipping aggregates this cycle: %s", exc)
            power_values = None

        snapshot = {
            "timestamp": datetime.now(timezone.utc),
            "site_name": powerwall.site_name(),
            "firmware": powerwall.version(),
            "din": powerwall.din(),
            "battery_percentage": powerwall.level(),
            "power": power_values,
            "grid_status": powerwall.grid_status("string") if hasattr(powerwall, "grid_status") else None,
        }

        try:
            status = powerwall.status()
        except Exception as exc:
            LOGGER.debug("Unable to fetch status(): %s", exc)
            status = None

        if isinstance(status, dict):
            alerts = status.get("control", {}).get("alerts", {}).get("active", [])
            snapshot["alerts"] = alerts
            system_status = status.get("control", {}).get("systemStatus", {})
            if system_status:
                snapshot["system_status"] = system_status
        else:
            snapshot["alerts"] = []

        try:
            vitals = powerwall.vitals()
        except Exception as exc:
            LOGGER.debug("Unable to fetch vitals(): %s", exc)
            vitals = None

        if isinstance(vitals, dict):
            snapshot["vitals"] = vitals
            snapshot["battery_nominal_energy_remaining"] = _extract_float(
                vitals,
                ["TEPOD--%s" % snapshot["din"], "POD_nom_energy_remaining"],
            )
            snapshot["battery_nominal_full_energy"] = _extract_float(
                vitals,
                ["TEPOD--%s" % snapshot["din"], "POD_nom_full_pack_energy"],
            )
        return snapshot


def _extract_float(payload: Dict[str, object], path: Iterable[str]) -> Optional[float]:
    current: object = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    try:
        return float(current)
    except (TypeError, ValueError):
        return None


class InfluxWriter:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._session = requests.Session()
        self._write_url = f"{config.influx_url.rstrip('/')}/api/v2/write"

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")

    @staticmethod
    def _escape_str_field(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\"", "\\\"")

    def build_line(self, snapshot: Dict[str, object]) -> Optional[str]:
        measurement = self._escape(self._config.measurement)
        tags = {
            "site": snapshot.get("site_name") or "unknown",
            "firmware": snapshot.get("firmware") or "unknown",
        }
        tags_part = ",".join(f"{self._escape(k)}={self._escape(str(v))}" for k, v in tags.items())

        fields_parts = []

        def add_field(name: str, value: object) -> None:
            if value is None:
                return
            key = self._escape(name)
            if isinstance(value, bool):
                fields_parts.append(f"{key}={'true' if value else 'false'}")
            elif isinstance(value, int):
                fields_parts.append(f"{key}={value}i")
            elif isinstance(value, float):
                if math.isnan(value) or math.isinf(value):
                    return
                fields_parts.append(f"{key}={value}")
            else:
                fields_parts.append(f"{key}=\"{self._escape_str_field(str(value))}\"")

        add_field("battery_percentage", _as_float(snapshot.get("battery_percentage")))
        power = snapshot.get("power")
        if isinstance(power, dict):
            add_field("site_power_w", _as_float(power.get("site")))
            add_field("solar_power_w", _as_float(power.get("solar")))
            add_field("battery_power_w", _as_float(power.get("battery")))
            add_field("load_power_w", _as_float(power.get("load")))
        add_field("battery_nominal_energy_remaining_wh", _as_float(snapshot.get("battery_nominal_energy_remaining")))
        add_field("battery_nominal_full_energy_wh", _as_float(snapshot.get("battery_nominal_full_energy")))
        alerts = snapshot.get("alerts")
        if isinstance(alerts, list):
            add_field("alerts_count", len(alerts))
            if alerts:
                add_field("alerts", ";".join(sorted(str(a) for a in alerts)))
        add_field("grid_status", snapshot.get("grid_status"))
        add_field("din", snapshot.get("din"))

        # Export string connection status and per-string solar metrics
        vitals = snapshot.get("vitals")
        if isinstance(vitals, dict):
            din = snapshot.get("din")
            if din:
                # String connection status
                pvs_key = f"PVS--{din}"
                if pvs_key in vitals:
                    pvs = vitals[pvs_key]
                    for string_name in ["StringA", "StringB", "StringC", "StringD", "StringE", "StringF"]:
                        key = f"PVS_{string_name}_Connected"
                        if key in pvs:
                            add_field(f"string_{string_name.lower()}_connected", pvs[key])
                
                # Per-string solar metrics
                pvac_key = f"PVAC--{din}"
                if pvac_key in vitals:
                    pvac = vitals[pvac_key]
                    for string_letter in ["A", "B", "C", "D", "E", "F"]:
                        state_key = f"PVAC_PvState_{string_letter}"
                        voltage_key = f"PVAC_PVMeasuredVoltage_{string_letter}"
                        current_key = f"PVAC_PVCurrent_{string_letter}"
                        power_key = f"PVAC_PVMeasuredPower_{string_letter}"
                        
                        if state_key in pvac:
                            add_field(f"string_{string_letter.lower()}_state", pvac[state_key])
                        if voltage_key in pvac:
                            add_field(f"string_{string_letter.lower()}_voltage_v", _as_float(pvac[voltage_key]))
                        if current_key in pvac:
                            add_field(f"string_{string_letter.lower()}_current_a", _as_float(pvac[current_key]))
                        if power_key in pvac:
                            add_field(f"string_{string_letter.lower()}_power_w", _as_float(pvac[power_key]))

        if not fields_parts:
            return None

        timestamp = snapshot.get("timestamp")
        if isinstance(timestamp, datetime):
            ts_ns = int(timestamp.timestamp() * 1_000_000_000)
        else:
            ts_ns = int(time.time() * 1_000_000_000)
        return f"{measurement},{tags_part} {'/'.join(fields_parts)} {ts_ns}".replace("/", ",")

    def write(self, line: str) -> None:
        headers = {
            "Authorization": f"Token {self._config.influx_token}",
            "Content-Type": "text/plain; charset=utf-8",
        }
        params = {
            "org": self._config.influx_org,
            "bucket": self._config.influx_bucket,
            "precision": "ns",
        }
        response = self._session.post(
            self._write_url,
            headers=headers,
            params=params,
            data=line.encode("utf-8"),
            timeout=self._config.influx_timeout,
            verify=self._config.influx_verify_tls,
        )
        if response.status_code >= 300:
            raise RuntimeError(
                f"InfluxDB write failed: {response.status_code} {response.text.strip()}"
            )


def _as_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def configure_logging(level_name: str) -> None:
    numeric_level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(level=numeric_level, format="%(asctime)s %(levelname)s %(message)s")


def maybe_connect_wifi(config: ServiceConfig) -> None:
    if not config.connect_wifi:
        return
    if not config.wifi_ssid:
        LOGGER.warning("PW_CONNECT_WIFI is true but PW_WIFI_SSID is not set; skipping Wi-Fi join")
        return
    try:
        _check_nmcli_available()
        connect_to_wifi(config.wifi_ssid, config.wifi_password, config.wifi_interface, timeout=60)
    except WiFiConnectionError as exc:
        LOGGER.error("Wi-Fi connection failed: %s", exc)
        raise


def run_service(config: ServiceConfig, run_once: bool = False) -> None:
    poller = PowerwallPoller(config)
    writer = InfluxWriter(config)

    shutdown = False

    def handle_signal(signum, _frame) -> None:  # pragma: no cover - signal handling
        nonlocal shutdown
        LOGGER.info("Received signal %s - shutting down", signum)
        shutdown = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        maybe_connect_wifi(config)
    except WiFiConnectionError:
        LOGGER.warning("Continuing without Wi-Fi connection; ensure connectivity manually")

    try:
        while not shutdown:
            cycle_start = time.monotonic()
            try:
                snapshot = poller.fetch_snapshot()
                line = writer.build_line(snapshot)
                if line is None:
                    LOGGER.warning("No fields to write; skipping this cycle")
                else:
                    writer.write(line)
                    LOGGER.info("Wrote metrics for site=%s", snapshot.get("site_name"))
            except Exception as exc:
                LOGGER.exception("Polling cycle failed: %s", exc)
            if run_once:
                break
            elapsed = time.monotonic() - cycle_start
            sleep_for = max(0.0, config.poll_interval - elapsed)
            time.sleep(sleep_for)
    finally:
        poller.close()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to .env file with configuration values (default: ./powerwall.env if present)",
    )
    parser.add_argument("--once", action="store_true", help="Run a single polling cycle and exit")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    env_file = args.env_file or DEFAULT_ENV_PATH
    if env_file.exists():
        load_env_file(env_file)
    configure_logging(os.environ.get("PW_LOG_LEVEL", "INFO"))
    try:
        config = build_config()
    except Exception as exc:
        LOGGER.error("Invalid configuration: %s", exc)
        return 2
    try:
        run_service(config, run_once=args.once)
    except Exception as exc:
        LOGGER.error("Fatal error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
