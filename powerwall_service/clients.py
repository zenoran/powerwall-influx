"""Clients used by the Powerwall service (Powerwall, InfluxDB, MQTT).

This module provides backward-compatible access to client classes that have been
refactored into separate modules for better organization.

For new code, consider importing directly from:
- powerwall_service.powerwall_client (PowerwallPoller, PowerwallUnavailableError)
- powerwall_service.influx_writer (InfluxWriter)
- powerwall_service.mqtt_publisher (MQTTPublisher)
- powerwall_service.metrics (extract_snapshot_metrics, to_float)
- powerwall_service.helpers (maybe_connect_wifi)
"""

# Re-export main classes for backward compatibility
from .powerwall_client import PowerwallPoller, PowerwallUnavailableError
from .influx_writer import InfluxWriter
from .mqtt_publisher import MQTTPublisher
from .metrics import extract_snapshot_metrics, to_float, _extract_float
from .helpers import maybe_connect_wifi

__all__ = [
    "PowerwallPoller",
    "PowerwallUnavailableError",
    "InfluxWriter",
    "MQTTPublisher",
    "extract_snapshot_metrics",
    "to_float",
    "_extract_float",
    "maybe_connect_wifi",
]
