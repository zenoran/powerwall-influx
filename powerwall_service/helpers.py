"""Helper functions for WiFi and utility operations."""

import logging

from .config import ServiceConfig
from .connect_wifi import (
    WiFiConnectionError,
    _check_nmcli_available,
    connect_to_wifi,
)

LOGGER = logging.getLogger("powerwall_service.helpers")


def maybe_connect_wifi(config: ServiceConfig) -> None:
    """Connect to WiFi if configured.
    
    Args:
        config: Service configuration
        
    Raises:
        WiFiConnectionError: If WiFi connection fails
    """
    if not config.connect_wifi:
        return
    if not config.wifi_ssid:
        LOGGER.warning(
            "PW_CONNECT_WIFI is true but PW_WIFI_SSID is not set; skipping Wi-Fi join"
        )
        return
    try:
        _check_nmcli_available()
        connect_to_wifi(
            config.wifi_ssid,
            config.wifi_password,
            config.wifi_interface,
            timeout=60,
        )
    except WiFiConnectionError as exc:
        LOGGER.error("Wi-Fi connection failed: %s", exc)
        raise
