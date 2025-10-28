"""Configuration helpers for the Powerwall Influx service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set

DEFAULT_ENV_PATH = Path(__file__).resolve().parent / "powerwall.env"


@dataclass
class ServiceConfig:
    """Configuration for the Powerwall background service."""

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
    mqtt_enabled: bool
    mqtt_host: str
    mqtt_port: int
    mqtt_username: Optional[str]
    mqtt_password: Optional[str]
    mqtt_topic_prefix: str
    mqtt_qos: int
    mqtt_retain: bool
    mqtt_metrics: Set[str]


def load_env_file(path: Path) -> None:
    """Populate :mod:`os.environ` with KEY=VALUE pairs from ``path``."""

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
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


REDACTED = "***redacted***"


def build_config() -> ServiceConfig:
    """Construct a :class:`ServiceConfig` from environment variables."""

    mqtt_metrics_raw = os.environ.get("MQTT_METRICS", "").strip()
    mqtt_metrics: Set[str] = set()
    if mqtt_metrics_raw:
        mqtt_metrics = {token.strip() for token in mqtt_metrics_raw.split(",") if token.strip()}

    cfg = ServiceConfig(
        influx_url=os.environ.get("INFLUX_URL", "http://influxdb.home:8086"),
        influx_org=os.environ.get("INFLUX_ORG", "home"),
        influx_bucket=os.environ.get("INFLUX_BUCKET", "powerwall"),
        influx_token=os.environ.get("INFLUX_TOKEN", ""),
        measurement=os.environ.get("INFLUX_MEASUREMENT", "powerwall"),
        influx_timeout=_env_float("INFLUX_TIMEOUT", 10.0),
        influx_verify_tls=_env_bool("INFLUX_VERIFY_TLS", True),
        poll_interval=_env_float("PW_POLL_INTERVAL", 60.0),
        host=os.environ.get("PW_HOST", "192.168.91.1"),
        timezone_name=os.environ.get("PW_TIMEZONE", "UTC"),
        cache_expire=_env_int("PW_CACHE_EXPIRE", 5),
        request_timeout=_env_int("PW_REQUEST_TIMEOUT", 10),
        wifi_ssid=os.environ.get("PW_WIFI_SSID"),
        wifi_password=os.environ.get("PW_WIFI_PASSWORD"),
        wifi_interface=os.environ.get("PW_WIFI_INTERFACE"),
        connect_wifi=_env_bool("PW_CONNECT_WIFI", False),
        gateway_password=os.environ.get("PW_GATEWAY_PASSWORD"),
        customer_email=os.environ.get("PW_CUSTOMER_EMAIL"),
        customer_password=os.environ.get("PW_CUSTOMER_PASSWORD"),
        log_level=os.environ.get("PW_LOG_LEVEL", "INFO"),
        mqtt_enabled=_env_bool("MQTT_ENABLED", False),
        mqtt_host=os.environ.get("MQTT_HOST", "mqtt.home"),
        mqtt_port=_env_int("MQTT_PORT", 1883),
        mqtt_username=os.environ.get("MQTT_USERNAME"),
        mqtt_password=os.environ.get("MQTT_PASSWORD"),
        mqtt_topic_prefix=os.environ.get("MQTT_TOPIC_PREFIX", "homeassistant/sensor/powerwall"),
        mqtt_qos=_env_int("MQTT_QOS", 1),
        mqtt_retain=_env_bool("MQTT_RETAIN", True),
        mqtt_metrics=mqtt_metrics,
    )

    if not cfg.influx_token:
        raise RuntimeError("INFLUX_TOKEN must be set (use a .env file or environment variable)")
    if not cfg.influx_org:
        raise RuntimeError("INFLUX_ORG must be set")
    if not cfg.influx_bucket:
        raise RuntimeError("INFLUX_BUCKET must be set")
    if cfg.poll_interval <= 0:
        raise RuntimeError("PW_POLL_INTERVAL must be greater than zero")

    return cfg


def redact_config(cfg: ServiceConfig) -> dict:
    """Return a sanitized view of ``cfg`` suitable for JSON responses."""

    return {
        "influx_url": cfg.influx_url,
        "influx_org": cfg.influx_org,
        "influx_bucket": cfg.influx_bucket,
        "measurement": cfg.measurement,
        "influx_timeout": cfg.influx_timeout,
        "influx_verify_tls": cfg.influx_verify_tls,
        "poll_interval": cfg.poll_interval,
        "host": cfg.host,
        "timezone_name": cfg.timezone_name,
        "cache_expire": cfg.cache_expire,
        "request_timeout": cfg.request_timeout,
        "wifi_ssid": cfg.wifi_ssid,
        "wifi_interface": cfg.wifi_interface,
        "connect_wifi": cfg.connect_wifi,
        "gateway_password": REDACTED if cfg.gateway_password else None,
        "customer_email": cfg.customer_email,
        "customer_password": REDACTED if cfg.customer_password else None,
        "mqtt_enabled": cfg.mqtt_enabled,
        "mqtt_host": cfg.mqtt_host,
        "mqtt_port": cfg.mqtt_port,
        "mqtt_username": cfg.mqtt_username,
        "mqtt_password": REDACTED if cfg.mqtt_password else None,
        "mqtt_topic_prefix": cfg.mqtt_topic_prefix,
        "mqtt_qos": cfg.mqtt_qos,
        "mqtt_retain": cfg.mqtt_retain,
        "mqtt_metrics": sorted(cfg.mqtt_metrics),
    }
