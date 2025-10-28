"""Clients used by the Powerwall service (Powerwall, InfluxDB, MQTT)."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

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


def _is_connection_error(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` (or its causes) represent a network failure."""

    if isinstance(
        exc,
        (
            requests_exceptions.RequestException,
            urllib3_exceptions.HTTPError,
            ConnectionError,
            OSError,
        ),
    ):
        return True

    cause = getattr(exc, "__cause__", None)
    if cause and cause is not exc and _is_connection_error(cause):
        return True

    context = getattr(exc, "__context__", None)
    if context and context is not exc and _is_connection_error(context):
        return True

    return False


class PowerwallPoller:
    """Thin wrapper around :mod:`pypowerwall` with connection caching."""

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
            "Connecting to Powerwall host=%s email=%s auto_select=True",
            self._config.host,
            email,
        )
        try:
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
        except Exception as exc:
            if _is_connection_error(exc):
                raise PowerwallUnavailableError(
                    f"Failed to connect to Powerwall gateway at {self._config.host}"
                ) from exc
            raise

    def fetch_snapshot(self) -> Dict[str, object]:
        try:
            self._ensure_connection()
            assert self._powerwall is not None
            powerwall = self._powerwall

            try:
                power_values = powerwall.power()
            except Exception as exc:
                if _is_connection_error(exc):
                    raise PowerwallUnavailableError(
                        f"Unable to retrieve power metrics from Powerwall at {self._config.host}"
                    ) from exc
                LOGGER.debug("power() failed; skipping aggregates this cycle: %s", exc)
                power_values = None

            snapshot = {
                "timestamp": datetime.now(timezone.utc),
                "site_name": powerwall.site_name(),
                "firmware": powerwall.version(),
                "din": powerwall.din(),
                "battery_percentage": powerwall.level(),
                "power": power_values,
                "grid_status": powerwall.grid_status("string")
                if hasattr(powerwall, "grid_status")
                else None,
            }

            try:
                status = powerwall.status()
            except Exception as exc:
                if _is_connection_error(exc):
                    raise PowerwallUnavailableError(
                        f"Unable to retrieve status from Powerwall at {self._config.host}"
                    ) from exc
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
                if _is_connection_error(exc):
                    raise PowerwallUnavailableError(
                        f"Unable to retrieve vitals from Powerwall at {self._config.host}"
                    ) from exc
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
        except PowerwallUnavailableError:
            self.close()
            raise
        except Exception as exc:
            if _is_connection_error(exc):
                self.close()
                raise PowerwallUnavailableError(
                    f"Unable to communicate with Powerwall gateway at {self._config.host}"
                ) from exc
            raise


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
        measurement = self._escape(self._config.measurement)
        tags = {
            "site": snapshot.get("site_name") or "unknown",
            "firmware": snapshot.get("firmware") or "unknown",
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

        add_field("battery_percentage", _as_float(snapshot.get("battery_percentage")))
        power = snapshot.get("power")
        if isinstance(power, dict):
            add_field("site_power_w", _as_float(power.get("site")))
            add_field("solar_power_w", _as_float(power.get("solar")))
            add_field("battery_power_w", _as_float(power.get("battery")))
            add_field("load_power_w", _as_float(power.get("load")))
        add_field(
            "battery_nominal_energy_remaining_wh",
            _as_float(snapshot.get("battery_nominal_energy_remaining")),
        )
        add_field(
            "battery_nominal_full_energy_wh",
            _as_float(snapshot.get("battery_nominal_full_energy")),
        )
        alerts = snapshot.get("alerts")
        if isinstance(alerts, list):
            add_field("alerts_count", len(alerts))
            if alerts:
                add_field("alerts", ";".join(sorted(str(a) for a in alerts)))
        add_field("grid_status", snapshot.get("grid_status"))
        add_field("din", snapshot.get("din"))

        vitals = snapshot.get("vitals")
        if isinstance(vitals, dict):
            din = snapshot.get("din")
            if din:
                pvs_key = f"PVS--{din}"
                if pvs_key in vitals:
                    pvs = vitals[pvs_key]
                    for string_name in [
                        "StringA",
                        "StringB",
                        "StringC",
                        "StringD",
                        "StringE",
                        "StringF",
                    ]:
                        key = f"PVS_{string_name}_Connected"
                        if key in pvs:
                            add_field(f"string_{string_name.lower()}_connected", pvs[key])

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
                            add_field(
                                f"string_{string_letter.lower()}_voltage_v",
                                _as_float(pvac[voltage_key]),
                            )
                        if current_key in pvac:
                            add_field(
                                f"string_{string_letter.lower()}_current_a",
                                _as_float(pvac[current_key]),
                            )
                        if power_key in pvac:
                            add_field(
                                f"string_{string_letter.lower()}_power_w",
                                _as_float(pvac[power_key]),
                            )

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
        if not self.enabled or not self._connected:
            return

        metrics = self._extract_metrics(snapshot)
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
        metrics: Dict[str, object] = {}
        metrics["battery_percentage"] = _as_float(snapshot.get("battery_percentage"))
        power = snapshot.get("power")
        if isinstance(power, dict):
            metrics["site_power_w"] = _as_float(power.get("site"))
            metrics["solar_power_w"] = _as_float(power.get("solar"))
            metrics["battery_power_w"] = _as_float(power.get("battery"))
            metrics["load_power_w"] = _as_float(power.get("load"))
        metrics["battery_nominal_energy_remaining_wh"] = _as_float(
            snapshot.get("battery_nominal_energy_remaining")
        )
        metrics["battery_nominal_full_energy_wh"] = _as_float(
            snapshot.get("battery_nominal_full_energy")
        )
        alerts = snapshot.get("alerts")
        if isinstance(alerts, list):
            metrics["alerts_count"] = len(alerts)
        metrics["grid_status"] = snapshot.get("grid_status")
        vitals = snapshot.get("vitals")
        if isinstance(vitals, dict):
            din = snapshot.get("din")
            if din:
                pvs_key = f"PVS--{din}"
                if pvs_key in vitals:
                    pvs = vitals[pvs_key]
                    for string_name in [
                        "StringA",
                        "StringB",
                        "StringC",
                        "StringD",
                        "StringE",
                        "StringF",
                    ]:
                        key = f"PVS_{string_name}_Connected"
                        if key in pvs:
                            metrics[f"string_{string_name.lower()}_connected"] = pvs[key]
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
                            metrics[f"{prefix}_voltage_v"] = _as_float(pvac[voltage_key])
                        if current_key in pvac:
                            metrics[f"{prefix}_current_a"] = _as_float(pvac[current_key])
                        if power_key in pvac:
                            metrics[f"{prefix}_power_w"] = _as_float(pvac[power_key])
        return metrics

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


def _as_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
