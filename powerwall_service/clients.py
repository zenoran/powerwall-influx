"""Clients used by the Powerwall service (Powerwall, InfluxDB, MQTT)."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional

import pypowerwall
import requests
from requests import exceptions as requests_exceptions
from urllib3 import exceptions as urllib3_exceptions

from .config import ServiceConfig
from .connect_wifi import (
    WiFiConnectionError,
    _check_nmcli_available,
    connect_to_wifi,
)

LOGGER = logging.getLogger("powerwall_service.clients")

try:  # pragma: no cover - optional dependency
    import paho.mqtt.client as mqtt

    MQTT_AVAILABLE = True
except ImportError:  # pragma: no cover
    MQTT_AVAILABLE = False
    mqtt = None  # type: ignore[assignment]


class PowerwallUnavailableError(RuntimeError):
    """Raised when the Powerwall gateway cannot be reached."""


def _check_exception_chain(exc: BaseException, condition: Callable[[BaseException], bool]) -> bool:
    """Walk the exception chain and check if any exception matches the condition.
    
    This helper reduces duplication between _is_connection_error() and _is_auth_error()
    by providing a generic way to recursively check exception chains.
    
    Args:
        exc: The exception to check
        condition: A function that returns True if an exception matches
        
    Returns:
        True if exc or any exception in its chain matches the condition
    """
    if condition(exc):
        return True
    
    # Check __cause__ chain
    cause = getattr(exc, "__cause__", None)
    if cause and cause is not exc and _check_exception_chain(cause, condition):
        return True
    
    # Check __context__ chain
    context = getattr(exc, "__context__", None)
    if context and context is not exc and _check_exception_chain(context, condition):
        return True
    
    return False


def _is_connection_error(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` (or its causes) represent a network failure."""
    def is_network_exception(e: BaseException) -> bool:
        return isinstance(
            e,
            (
                requests_exceptions.RequestException,
                urllib3_exceptions.HTTPError,
                ConnectionError,
                OSError,
            ),
        )
    
    return _check_exception_chain(exc, is_network_exception)


def _is_auth_error(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` represents an authentication failure (403/401)."""
    def is_auth_exception(e: BaseException) -> bool:
        # Check if it's an HTTP error with 403 or 401 status
        if isinstance(e, requests_exceptions.HTTPError):
            if hasattr(e, 'response') and e.response is not None:
                return e.response.status_code in (401, 403)
        
        # Check exception message for authentication indicators
        exc_str = str(e).lower()
        auth_indicators = ['403', '401', 'forbidden', 'unauthorized', 'authentication']
        return any(indicator in exc_str for indicator in auth_indicators)
    
    return _check_exception_chain(exc, is_auth_exception)


class PowerwallPoller:
    """Thin wrapper around :mod:`pypowerwall` with connection caching."""

    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._powerwall: Optional[pypowerwall.Powerwall] = None
        self._consecutive_auth_failures = 0
        self._max_auth_failures = 3  # Force full reconnect after this many 403s
        self._consecutive_connection_failures = 0
        self._last_connection_attempt = 0.0  # Timestamp of last connection attempt
        self._backoff_base = 30.0  # Base backoff time in seconds (30s)
        self._backoff_max = 300.0  # Maximum backoff time (5 minutes)

    def close(self) -> None:
        if self._powerwall and getattr(self._powerwall, "client", None):
            try:
                self._powerwall.client.close_session()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                LOGGER.debug("Failed to close Powerwall session: %s", exc)
        self._powerwall = None
        self._consecutive_auth_failures = 0
        # Don't reset connection failures here - we want to track them across close/reopen

    def _ensure_connection(self, force_reconnect: bool = False) -> None:
        """Ensure we have a valid connection to the Powerwall.
        
        Args:
            force_reconnect: If True, close existing connection and create a new one
        """
        if force_reconnect:
            LOGGER.info("Forcing full reconnection to Powerwall (auth failures: %d)", 
                       self._consecutive_auth_failures)
            self.close()
        elif self._powerwall and self._powerwall.is_connected():
            return
        
        if not self._powerwall:
            self.close()  # Ensure clean state
        
        # Implement exponential backoff instead of hard circuit breaker
        # This allows automatic recovery but with increasing delays
        if self._consecutive_connection_failures > 0:
            now = time.monotonic()
            time_since_last_attempt = now - self._last_connection_attempt
            
            # Calculate exponential backoff: base * 2^(failures-1), capped at max
            backoff_time = min(
                self._backoff_base * (2 ** (self._consecutive_connection_failures - 1)),
                self._backoff_max
            )
            
            if time_since_last_attempt < backoff_time:
                remaining = backoff_time - time_since_last_attempt
                LOGGER.info(
                    "Exponential backoff active after %d failures: waiting %.0fs before retry (%.0fs remaining)",
                    self._consecutive_connection_failures,
                    backoff_time,
                    remaining
                )
                raise PowerwallUnavailableError(
                    f"Backoff active after {self._consecutive_connection_failures} failures. "
                    f"Will retry in {remaining:.0f}s. Powerwall at {self._config.host} may be offline."
                )
            else:
                LOGGER.info(
                    "Backoff period expired after %d failures (%.0fs elapsed), attempting reconnection",
                    self._consecutive_connection_failures,
                    time_since_last_attempt
                )
            
        gw_pwd = (
            self._config.gateway_password
            or self._config.wifi_password
            or self._config.customer_password
        )
        email = self._config.customer_email or "nobody@nowhere.com"
        password = self._config.customer_password or ""
        
        # Determine connection mode based on configuration
        # If we have gw_pwd, use local mode with TEDAPI
        # Don't use auto_select to avoid trying cloud/fleetapi modes we haven't configured
        use_local_mode = bool(self._config.host)
        
        LOGGER.debug(
            "Connecting to Powerwall host=%s email=%s mode=%s (attempt after %d failures)",
            self._config.host,
            email,
            "local" if use_local_mode else "cloud",
            self._consecutive_connection_failures,
        )
        try:
            # Disable auto_select and retry_modes to fail fast and avoid trying unconfigured modes
            self._powerwall = pypowerwall.Powerwall(
                host=self._config.host,
                password=password,
                email=email,
                timezone=self._config.timezone_name,
                pwcacheexpire=self._config.cache_expire,
                timeout=self._config.request_timeout,
                gw_pwd=gw_pwd,
                cloudmode=not use_local_mode,  # Only use cloud if no host specified
                auto_select=False,  # Changed: Don't auto-select modes, be explicit
                retry_modes=False,  # Changed: Don't let pypowerwall retry - we handle it
            )
            # Success! Reset both failure counters
            if self._consecutive_connection_failures > 0 or self._consecutive_auth_failures > 0:
                LOGGER.info(
                    "Successfully connected to Powerwall after %d connection failures, %d auth failures",
                    self._consecutive_connection_failures,
                    self._consecutive_auth_failures
                )
            self._consecutive_auth_failures = 0
            self._consecutive_connection_failures = 0
        except Exception as exc:
            self._consecutive_connection_failures += 1
            self._last_connection_attempt = time.monotonic()  # CRITICAL: Set timestamp when connection fails
            # Calculate next backoff time for logging
            next_backoff = min(
                self._backoff_base * (2 ** (self._consecutive_connection_failures - 1)),
                self._backoff_max
            )
            LOGGER.warning(
                "Connection attempt failed (failure %d, next retry in %.0fs): %s",
                self._consecutive_connection_failures,
                next_backoff,
                exc
            )
            if _is_connection_error(exc):
                raise PowerwallUnavailableError(
                    f"Failed to connect to Powerwall gateway at {self._config.host}"
                ) from exc
            raise

    def _fetch_with_auth_retry(
        self, 
        fetch_func: Callable[[], Any], 
        data_type: str
    ) -> Any:
        """Fetch data with authentication error handling and retry logic.
        
        This helper reduces duplication in fetch_snapshot() by centralizing
        the auth error handling pattern used for power, status, and vitals.
        
        Args:
            fetch_func: Function to call to fetch the data
            data_type: Description of data being fetched (for logging)
            
        Returns:
            The fetched data, or None if a recoverable error occurs
            
        Raises:
            PowerwallUnavailableError: If auth fails repeatedly or connection fails
        """
        try:
            return fetch_func()
        except Exception as exc:
            if _is_auth_error(exc):
                self._consecutive_auth_failures += 1
                LOGGER.warning(
                    "Authentication error fetching %s (failure %d/%d): %s",
                    data_type,
                    self._consecutive_auth_failures,
                    self._max_auth_failures,
                    exc,
                )
                if self._consecutive_auth_failures >= self._max_auth_failures:
                    raise PowerwallUnavailableError(
                        f"Authentication failed {self._consecutive_auth_failures} times, "
                        f"unable to authenticate with Powerwall at {self._config.host}"
                    ) from exc
                return None
            elif _is_connection_error(exc):
                raise PowerwallUnavailableError(
                    f"Unable to retrieve {data_type} from Powerwall at {self._config.host}"
                ) from exc
            else:
                LOGGER.debug("Failed to fetch %s: %s", data_type, exc)
                return None

    def _fetch_power_metrics(self) -> Optional[Dict[str, float]]:
        """Fetch power metrics with auth retry logic."""
        assert self._powerwall is not None
        power_values = self._fetch_with_auth_retry(
            self._powerwall.power,
            "power metrics"
        )
        
        # If auth failed once, try reconnecting
        if power_values is None and self._consecutive_auth_failures > 0:
            if self._consecutive_auth_failures < self._max_auth_failures:
                LOGGER.info("Attempting reconnection to recover from auth error")
                self.close()
                self._ensure_connection(force_reconnect=True)
                power_values = self._powerwall.power()
        
        return power_values

    def _fetch_status_data(self) -> Optional[Dict[str, Any]]:
        """Fetch status data with auth retry logic."""
        assert self._powerwall is not None
        return self._fetch_with_auth_retry(
            self._powerwall.status,
            "status"
        )

    def _fetch_vitals_data(self) -> Optional[Dict[str, Any]]:
        """Fetch vitals data with auth retry logic."""
        assert self._powerwall is not None
        return self._fetch_with_auth_retry(
            self._powerwall.vitals,
            "vitals"
        )

    def _build_snapshot(
        self,
        power_values: Optional[Dict[str, float]],
        status: Optional[Dict[str, Any]],
        vitals: Optional[Dict[str, Any]]
    ) -> Dict[str, object]:
        """Build snapshot dictionary from fetched data.
        
        Args:
            power_values: Power metrics from powerwall.power()
            status: Status dict from powerwall.status()
            vitals: Vitals dict from powerwall.vitals()
            
        Returns:
            Complete snapshot dictionary
        """
        assert self._powerwall is not None
        powerwall = self._powerwall
        
        # Build basic snapshot
        snapshot = {
            "timestamp": datetime.now(timezone.utc),
            "site_name": self._safe_call(powerwall.site_name),
            "firmware": self._safe_call(powerwall.version),
            "din": self._safe_call(powerwall.din),
            "battery_percentage": self._safe_call(powerwall.level),
            "power": power_values,
            "grid_status": self._safe_call(
                lambda: powerwall.grid_status("string") if hasattr(powerwall, "grid_status") else None
            ),
        }

        # Process status data
        if isinstance(status, dict):
            alerts = status.get("control", {}).get("alerts", {}).get("active", [])
            snapshot["alerts"] = alerts
            system_status = status.get("control", {}).get("systemStatus", {})
            if system_status:
                snapshot["system_status"] = system_status
        else:
            snapshot["alerts"] = []

        # Process vitals data
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

    def fetch_snapshot(self) -> Dict[str, object]:
        """Fetch a complete snapshot of Powerwall metrics.
        
        This method implements robust error handling:
        - Detects network failures and raises PowerwallUnavailableError
        - Detects authentication failures and forces reconnection after threshold
        - Automatically retries with full session recreation on auth errors
        
        Returns:
            Dictionary containing all Powerwall metrics
            
        Raises:
            PowerwallUnavailableError: When the Powerwall is unreachable or auth fails repeatedly
        """
        # Check if we need to force reconnect due to repeated auth failures
        force_reconnect = self._consecutive_auth_failures >= self._max_auth_failures
        
        try:
            self._ensure_connection(force_reconnect=force_reconnect)
            assert self._powerwall is not None

            # Fetch all data using helper methods
            power_values = self._fetch_power_metrics()
            status = self._fetch_status_data()
            vitals = self._fetch_vitals_data()

            # Build and return snapshot
            snapshot = self._build_snapshot(power_values, status, vitals)
            
            # Success! Reset auth failure counter
            if self._consecutive_auth_failures > 0:
                LOGGER.info("Successfully recovered from previous auth failures")
                self._consecutive_auth_failures = 0
            
            return snapshot
            
        except PowerwallUnavailableError:
            # Already a PowerwallUnavailableError, just close and re-raise
            self.close()
            raise
        except Exception as exc:
            # Unexpected error - check if it's connection-related
            if _is_connection_error(exc):
                self.close()
                raise PowerwallUnavailableError(
                    f"Unable to communicate with Powerwall gateway at {self._config.host}"
                ) from exc
            elif _is_auth_error(exc):
                self._consecutive_auth_failures += 1
                self.close()
                raise PowerwallUnavailableError(
                    f"Authentication failed with Powerwall at {self._config.host}"
                ) from exc
            raise

    def _safe_call(self, func, default=None):
        """Safely call a function, returning default on any exception."""
        try:
            return func()
        except Exception as exc:
            LOGGER.debug("Safe call failed: %s", exc)
            return default


class InfluxWriter:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._session = requests.Session()
        self._write_url = f"{config.influx_url.rstrip('/')}/api/v2/write"

    @staticmethod
    def _escape(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace(",", "\\,")
            .replace(" ", "\\ ")
            .replace("=", "\\=")
        )

    @staticmethod
    def _escape_str_field(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\"", "\\\"")

    def build_line(self, snapshot: Dict[str, object]) -> Optional[str]:
        """Build an InfluxDB line protocol string from a snapshot.
        
        Uses the shared extract_snapshot_metrics() function to parse the snapshot,
        then formats the metrics into InfluxDB line protocol.
        """
        measurement = self._escape(self._config.measurement)
        tags = {
            "site": snapshot.get("site_name") or "unknown"
        }
        tags_part = ",".join(f"{self._escape(k)}={self._escape(str(v))}" for k, v in tags.items())

        fields_parts: list[str] = []

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

        # Use shared metric extraction logic
        metrics = extract_snapshot_metrics(snapshot)
        for metric_name, value in metrics.items():
            add_field(metric_name, value)

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


class MQTTPublisher:
    """Publish metrics to MQTT for Home Assistant instant sensors."""

    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._client: Optional[Any] = None
        self._connected = False
        self._last_error: Optional[str] = None

        if not MQTT_AVAILABLE:
            LOGGER.warning("paho-mqtt not available, MQTT publishing disabled")
            return

        if not config.mqtt_enabled:
            return

        assert mqtt is not None  # for type checkers
        client = mqtt.Client(client_id="powerwall_influx_service")
        self._client = client

        if config.mqtt_username and config.mqtt_password:
            client.username_pw_set(config.mqtt_username, config.mqtt_password)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect

        try:
            LOGGER.info(
                "Connecting to MQTT broker at %s:%d", config.mqtt_host, config.mqtt_port
            )
            client.connect(config.mqtt_host, config.mqtt_port, 60)
            client.loop_start()
        except Exception as exc:  # pragma: no cover - depends on environment
            self._last_error = str(exc)
            LOGGER.error("Failed to connect to MQTT broker: %s", exc)
            self._client = None

    # Callback handlers -------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):  # pragma: no cover - callback
        if rc == 0:
            self._connected = True
            self._last_error = None
            LOGGER.info("Connected to MQTT broker")
        else:
            self._connected = False
            self._last_error = f"connect rc={rc}"
            LOGGER.error("MQTT connection failed with code %d", rc)

    def _on_disconnect(self, client, userdata, rc):  # pragma: no cover - callback
        self._connected = False
        if rc != 0:
            self._last_error = f"disconnect rc={rc}"
            LOGGER.warning("Disconnected from MQTT broker (code %d)", rc)

    # Public API --------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self._client)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def publish_availability(self, online: bool, status_message: Optional[str] = None) -> None:
        if not self.enabled:
            return
        payload = "online" if online else "offline"
        try:
            self._client.publish(  # type: ignore[union-attr]
                f"{self._config.mqtt_topic_prefix}/availability",
                payload,
                qos=self._config.mqtt_qos,
                retain=True,
            )
            LOGGER.debug("Published MQTT availability=%s", payload)
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.warning("Failed to publish MQTT availability: %s", exc)
        if status_message:
            try:
                self._client.publish(  # type: ignore[union-attr]
                    f"{self._config.mqtt_topic_prefix}/status",
                    status_message,
                    qos=self._config.mqtt_qos,
                    retain=False,
                )
            except Exception as exc:
                LOGGER.debug("Failed to publish MQTT status message: %s", exc)

    def publish(self, snapshot: Dict[str, object]) -> None:
        """Publish metrics from snapshot to MQTT.
        
        Uses the shared extract_snapshot_metrics() function and filters
        based on configuration.
        """
        if not self.enabled or not self._connected:
            return

        # Use shared metric extraction logic
        metrics = extract_snapshot_metrics(snapshot)
        
        # Filter to configured metrics if specified
        if self._config.mqtt_metrics:
            metrics = {k: v for k, v in metrics.items() if k in self._config.mqtt_metrics}

        for metric_name, value in metrics.items():
            if value is None:
                continue
            topic = f"{self._config.mqtt_topic_prefix}/{metric_name}/state"
            if isinstance(value, bool):
                payload = "ON" if value else "OFF"
            elif isinstance(value, float):
                payload = f"{value:.2f}"
            else:
                payload = str(value)
            try:
                self._client.publish(  # type: ignore[union-attr]
                    topic,
                    payload,
                    qos=self._config.mqtt_qos,
                    retain=self._config.mqtt_retain,
                )
                LOGGER.debug("Published %s = %s to MQTT", metric_name, payload)
            except Exception as exc:
                self._last_error = str(exc)
                LOGGER.warning("Failed to publish %s to MQTT: %s", metric_name, exc)

    def _extract_metrics(self, snapshot: Dict[str, object]) -> Dict[str, object]:
        """DEPRECATED: Use extract_snapshot_metrics() instead.
        
        Kept for backward compatibility but delegates to shared function.
        """
        return extract_snapshot_metrics(snapshot)

    def close(self) -> None:
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                LOGGER.debug("Error closing MQTT connection: %s", exc)
            self._client = None
            self._connected = False


def maybe_connect_wifi(config: ServiceConfig) -> None:
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


def to_float(value: object, default: Optional[float] = None) -> Optional[float]:
    """Convert a value to float with robust error handling.
    
    This consolidated helper replaces _as_float() and provides a simpler interface.
    Handles None, numeric types, and string conversions gracefully.
    
    Args:
        value: The value to convert to float
        default: Value to return if conversion fails (defaults to None)
        
    Returns:
        Float value, or default if conversion fails or value is None
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_float(payload: Dict[str, object], path: Iterable[str]) -> Optional[float]:
    """Extract a float value from a nested dictionary path.
    
    Args:
        payload: Dictionary to extract from
        path: Sequence of keys to traverse
        
    Returns:
        Float value if found and convertible, None otherwise
    """
    current: object = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return to_float(current)


# Keep _as_float for backward compatibility (deprecated)
def _as_float(value: object) -> Optional[float]:
    """DEPRECATED: Use to_float() instead.
    
    Kept for backward compatibility.
    """
    return to_float(value)


def extract_snapshot_metrics(snapshot: Dict[str, object]) -> Dict[str, object]:
    """Extract all metrics from a Powerwall snapshot.
    
    This shared function is used by both InfluxWriter and MQTTPublisher
    to ensure consistency in metric extraction and reduce code duplication.
    
    Args:
        snapshot: Raw snapshot dictionary from PowerwallPoller
        
    Returns:
        Dictionary of metric names to values (float, int, str, or bool)
    """
    metrics: Dict[str, object] = {}
    
    # Basic metrics
    metrics["battery_percentage"] = to_float(snapshot.get("battery_percentage"))
    
    # Power metrics
    power = snapshot.get("power")
    if isinstance(power, dict):
        metrics["site_power_w"] = to_float(power.get("site"))
        metrics["solar_power_w"] = to_float(power.get("solar"))
        metrics["battery_power_w"] = to_float(power.get("battery"))
        metrics["load_power_w"] = to_float(power.get("load"))
    
    # Battery energy metrics
    metrics["battery_nominal_energy_remaining_wh"] = to_float(
        snapshot.get("battery_nominal_energy_remaining")
    )
    metrics["battery_nominal_full_energy_wh"] = to_float(
        snapshot.get("battery_nominal_full_energy")
    )
    
    # Alerts
    alerts = snapshot.get("alerts")
    if isinstance(alerts, list):
        metrics["alerts_count"] = len(alerts)
        if alerts:
            metrics["alerts"] = ";".join(sorted(str(a) for a in alerts))
    
    # Grid status and device ID
    metrics["grid_status"] = snapshot.get("grid_status")
    metrics["din"] = snapshot.get("din")
    
    # Vitals - String metrics
    vitals = snapshot.get("vitals")
    if isinstance(vitals, dict):
        din = snapshot.get("din")
        if din:
            # PVS (PhotoVoltaic System) string connection status
            pvs_key = f"PVS--{din}"
            if pvs_key in vitals:
                pvs = vitals[pvs_key]
                for string_name in ["StringA", "StringB", "StringC", "StringD", "StringE", "StringF"]:
                    key = f"PVS_{string_name}_Connected"
                    if key in pvs:
                        metrics[f"string_{string_name.lower()}_connected"] = pvs[key]
            
            # PVAC (PhotoVoltaic AC) string detailed metrics
            pvac_key = f"PVAC--{din}"
            if pvac_key in vitals:
                pvac = vitals[pvac_key]
                for string_letter in ["A", "B", "C", "D", "E", "F"]:
                    state_key = f"PVAC_PvState_{string_letter}"
                    voltage_key = f"PVAC_PVMeasuredVoltage_{string_letter}"
                    current_key = f"PVAC_PVCurrent_{string_letter}"
                    power_key = f"PVAC_PVMeasuredPower_{string_letter}"
                    prefix = f"string_{string_letter.lower()}"
                    
                    if state_key in pvac:
                        metrics[f"{prefix}_state"] = pvac[state_key]
                    if voltage_key in pvac:
                        metrics[f"{prefix}_voltage_v"] = to_float(pvac[voltage_key])
                    if current_key in pvac:
                        metrics[f"{prefix}_current_a"] = to_float(pvac[current_key])
                    if power_key in pvac:
                        metrics[f"{prefix}_power_w"] = to_float(pvac[power_key])
    
    return metrics
