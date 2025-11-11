"""MQTT publisher for Powerwall metrics."""

import logging
from typing import Any, Dict, Optional

from .config import ServiceConfig
from .metrics import extract_snapshot_metrics

LOGGER = logging.getLogger("powerwall_service.mqtt_publisher")

try:  # pragma: no cover - optional dependency
    import paho.mqtt.client as mqtt

    MQTT_AVAILABLE = True
except ImportError:  # pragma: no cover
    MQTT_AVAILABLE = False
    mqtt = None  # type: ignore[assignment]


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
        """Return True if MQTT publishing is enabled."""
        return bool(self._client)

    @property
    def connected(self) -> bool:
        """Return True if connected to MQTT broker."""
        return self._connected

    @property
    def last_error(self) -> Optional[str]:
        """Return the last error message, if any."""
        return self._last_error

    def publish_availability(self, online: bool, status_message: Optional[str] = None) -> None:
        """Publish availability status to MQTT.
        
        Args:
            online: Whether the service is online
            status_message: Optional status message to publish
        """
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
        
        Args:
            snapshot: Powerwall snapshot dictionary
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

    def close(self) -> None:
        """Close the MQTT connection."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                LOGGER.debug("Error closing MQTT connection: %s", exc)
            self._client = None
            self._connected = False
