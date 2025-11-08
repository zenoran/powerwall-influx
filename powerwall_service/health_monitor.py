"""Independent health monitoring and MQTT publishing.

This module provides a separate health monitoring system that publishes
service health metrics to MQTT for Home Assistant. It runs independently
from the main service polling loop to ensure health status is published
even when the Powerwall or InfluxDB services are experiencing failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, Optional

LOGGER = logging.getLogger("powerwall_service.health_monitor")

try:  # pragma: no cover - optional dependency
    import paho.mqtt.client as mqtt

    MQTT_AVAILABLE = True
except ImportError:  # pragma: no cover
    MQTT_AVAILABLE = False
    mqtt = None  # type: ignore[assignment]


class HealthMonitor:
    """Independent health monitoring with dedicated MQTT connection.
    
    This monitor runs in a separate async task with its own MQTT client,
    completely isolated from the main service operations. This ensures
    that health metrics continue to be published even when the Powerwall
    gateway is unreachable or InfluxDB is down.
    """

    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
        health_getter: Callable,
        mqtt_username: Optional[str] = None,
        mqtt_password: Optional[str] = None,
        topic_prefix: str = "homeassistant/sensor/powerwall_health",
        device_name: str = "Powerwall Service",
        publish_interval: float = 60.0,
        qos: int = 1,
    ) -> None:
        """Initialize the health monitor.
        
        Args:
            mqtt_host: MQTT broker hostname
            mqtt_port: MQTT broker port
            health_getter: Callable that returns HealthReport
            mqtt_username: Optional MQTT username
            mqtt_password: Optional MQTT password
            topic_prefix: Base topic for health sensors
            device_name: Device name for Home Assistant
            publish_interval: Seconds between health publishes
            qos: MQTT QoS level
        """
        if not MQTT_AVAILABLE:
            raise RuntimeError("paho-mqtt is not installed; health monitoring unavailable")
        
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._health_getter = health_getter
        self._mqtt_username = mqtt_username
        self._mqtt_password = mqtt_password
        self._topic_prefix = topic_prefix.rstrip("/")
        self._device_name = device_name
        self._publish_interval = publish_interval
        self._qos = qos
        
        self._client: Optional[Any] = None
        self._connected = False
        self._background_task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        
        self._device_id = "powerwall_influx_service"
        self._discovery_sent = False

    # Connection management --------------------------------------------
    
    def _setup_mqtt_client(self) -> None:
        """Create and configure the MQTT client."""
        if self._client is not None:
            return
        
        assert mqtt is not None  # for type checkers
        client = mqtt.Client(client_id=f"{self._device_id}_health_monitor")
        self._client = client
        
        if self._mqtt_username and self._mqtt_password:
            client.username_pw_set(self._mqtt_username, self._mqtt_password)
        
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        
        try:
            LOGGER.info(
                "Health monitor connecting to MQTT broker at %s:%d",
                self._mqtt_host,
                self._mqtt_port,
            )
            client.connect(self._mqtt_host, self._mqtt_port, 60)
            client.loop_start()
        except Exception as exc:
            LOGGER.error("Health monitor failed to connect to MQTT broker: %s", exc)
            self._client = None
            raise
    
    def _on_connect(self, client, userdata, flags, rc):  # pragma: no cover - callback
        """MQTT connection callback."""
        if rc == 0:
            self._connected = True
            LOGGER.info("Health monitor connected to MQTT broker")
            # Send discovery messages on (re)connect
            self._discovery_sent = False
        else:
            self._connected = False
            LOGGER.error("Health monitor MQTT connection failed with code %d", rc)
    
    def _on_disconnect(self, client, userdata, rc):  # pragma: no cover - callback
        """MQTT disconnection callback."""
        self._connected = False
        if rc != 0:
            LOGGER.warning("Health monitor disconnected from MQTT broker (code %d)", rc)
    
    # Publishing -------------------------------------------------------
    
    def _publish(self, topic: str, payload: str, retain: bool = False) -> None:
        """Publish a message to MQTT."""
        if not self._client or not self._connected:
            LOGGER.debug("Cannot publish to %s: MQTT not connected", topic)
            return
        
        try:
            result = self._client.publish(topic, payload, qos=self._qos, retain=retain)
            assert mqtt is not None  # for type checkers
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                LOGGER.warning("Failed to publish to %s: rc=%d", topic, result.rc)
        except Exception as exc:
            LOGGER.warning("Exception publishing to %s: %s", topic, exc)
    
    def _send_discovery_messages(self) -> None:
        """Send Home Assistant MQTT discovery messages for all health sensors."""
        if self._discovery_sent:
            return
        
        base_discovery_topic = "homeassistant"
        device_info = {
            "identifiers": [self._device_id],
            "name": self._device_name,
            "manufacturer": "Tesla",
            "model": "Powerwall Service Monitor",
        }
        
        # Overall health binary sensor
        overall_config = {
            "name": "Overall Health",
            "unique_id": f"{self._device_id}_overall_health",
            "state_topic": f"{self._topic_prefix}/overall/state",
            "device_class": "connectivity",
            "payload_on": "connected",
            "payload_off": "disconnected",
            "device": device_info,
        }
        self._publish(
            f"{base_discovery_topic}/binary_sensor/{self._device_id}/overall_health/config",
            json.dumps(overall_config),
            retain=True,
        )
        
        # Component health binary sensors
        components = ["powerwall", "influxdb", "mqtt", "wifi"]
        for component in components:
            component_config = {
                "name": f"{component.title()} Health",
                "unique_id": f"{self._device_id}_{component}_health",
                "state_topic": f"{self._topic_prefix}/{component}/state",
                "json_attributes_topic": f"{self._topic_prefix}/{component}/attributes",
                "device_class": "connectivity",
                "payload_on": "connected",
                "payload_off": "disconnected",
                "device": device_info,
            }
            self._publish(
                f"{base_discovery_topic}/binary_sensor/{self._device_id}/{component}_health/config",
                json.dumps(component_config),
                retain=True,
            )
        
        # Consecutive failures sensor
        failures_config = {
            "name": "Consecutive Failures",
            "unique_id": f"{self._device_id}_consecutive_failures",
            "state_topic": f"{self._topic_prefix}/consecutive_failures/state",
            "state_class": "measurement",
            "device": device_info,
        }
        self._publish(
            f"{base_discovery_topic}/sensor/{self._device_id}/consecutive_failures/config",
            json.dumps(failures_config),
            retain=True,
        )
        
        # Last poll time sensor
        last_poll_config = {
            "name": "Last Poll Time",
            "unique_id": f"{self._device_id}_last_poll_time",
            "state_topic": f"{self._topic_prefix}/last_poll_time/state",
            "device_class": "timestamp",
            "device": device_info,
        }
        self._publish(
            f"{base_discovery_topic}/sensor/{self._device_id}/last_poll_time/config",
            json.dumps(last_poll_config),
            retain=True,
        )
        
        # Last success time sensor
        last_success_config = {
            "name": "Last Success Time",
            "unique_id": f"{self._device_id}_last_success_time",
            "state_topic": f"{self._topic_prefix}/last_success_time/state",
            "device_class": "timestamp",
            "device": device_info,
        }
        self._publish(
            f"{base_discovery_topic}/sensor/{self._device_id}/last_success_time/config",
            json.dumps(last_success_config),
            retain=True,
        )
        
        # Background task running binary sensor
        task_config = {
            "name": "Background Task Running",
            "unique_id": f"{self._device_id}_background_task",
            "state_topic": f"{self._topic_prefix}/background_task/state",
            "device_class": "running",
            "payload_on": "running",
            "payload_off": "stopped",
            "device": device_info,
        }
        self._publish(
            f"{base_discovery_topic}/binary_sensor/{self._device_id}/background_task/config",
            json.dumps(task_config),
            retain=True,
        )
        
        self._discovery_sent = True
        LOGGER.info("Home Assistant discovery messages sent")
    
    def _publish_health_status(self) -> None:
        """Fetch and publish current health status."""
        try:
            health_report = self._health_getter()
        except Exception as exc:
            LOGGER.error("Failed to get health report: %s", exc)
            return
        
        # Send discovery messages if not yet sent
        self._send_discovery_messages()
        
        # Publish overall health
        overall_state = "connected" if health_report.overall else "disconnected"
        self._publish(f"{self._topic_prefix}/overall/state", overall_state, retain=False)
        
        # Publish component health
        for component_name, component in health_report.components.items():
            state = "connected" if component.healthy else "disconnected"
            self._publish(
                f"{self._topic_prefix}/{component_name}/state",
                state,
                retain=False,
            )
            
            # Publish component attributes (detail, last_success, last_error)
            attributes: Dict[str, Any] = {}
            if component.detail:
                attributes["detail"] = component.detail
            if component.last_success:
                attributes["last_success"] = component.last_success.isoformat()
            if component.last_error:
                attributes["last_error"] = component.last_error
            
            if attributes:
                self._publish(
                    f"{self._topic_prefix}/{component_name}/attributes",
                    json.dumps(attributes),
                    retain=False,
                )
        
        # Publish consecutive failures
        self._publish(
            f"{self._topic_prefix}/consecutive_failures/state",
            str(health_report.consecutive_failures),
            retain=False,
        )
        
        # Publish timestamps
        if health_report.last_poll_time:
            self._publish(
                f"{self._topic_prefix}/last_poll_time/state",
                health_report.last_poll_time.isoformat(),
                retain=False,
            )
        
        if health_report.last_success_time:
            self._publish(
                f"{self._topic_prefix}/last_success_time/state",
                health_report.last_success_time.isoformat(),
                retain=False,
            )
        
        # Publish background task status
        task_state = "running" if health_report.background_task_running else "stopped"
        self._publish(
            f"{self._topic_prefix}/background_task/state",
            task_state,
            retain=False,
        )
        
        LOGGER.debug("Published health status: overall=%s", overall_state)
        
    def _publish_offline_status(self) -> None:
        """Publish offline/disconnected status for all sensors."""
        self._publish(f"{self._topic_prefix}/overall/state", "disconnected", retain=False)
    
    # Lifecycle --------------------------------------------------------
    
    async def start(self) -> None:
        """Start the health monitoring background task."""
        if self._background_task and not self._background_task.done():
            LOGGER.warning("Health monitor already running")
            return
        
        # Setup MQTT connection in a thread to avoid blocking
        await asyncio.to_thread(self._setup_mqtt_client)
        
        self._stop_event.clear()
        self._background_task = asyncio.create_task(
            self._run_loop(),
            name="health-monitor",
        )
        LOGGER.info("Health monitor started (interval=%ss)", self._publish_interval)
    
    async def stop(self) -> None:
        """Stop the health monitoring background task."""
        if self._background_task is None:
            await asyncio.to_thread(self._shutdown)
            return
        
        self._stop_event.set()
        self._background_task.cancel()
        try:
            await self._background_task
        except asyncio.CancelledError:  # pragma: no cover - expected during shutdown
            pass
        finally:
            self._background_task = None
            self._stop_event = asyncio.Event()
            await asyncio.to_thread(self._shutdown)
        
        LOGGER.info("Health monitor stopped")
    
    async def _run_loop(self) -> None:
        """Background loop that periodically publishes health status."""
        # Initial publish after a short delay to let services initialize
        await asyncio.sleep(5.0)
        
        while not self._stop_event.is_set():
            start = time.monotonic()
            try:
                await asyncio.to_thread(self._publish_health_status)
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.exception("Error publishing health status: %s", exc)
            
            elapsed = time.monotonic() - start
            sleep_for = max(0.0, self._publish_interval - elapsed)
            
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                continue
    
    def _shutdown(self) -> None:
        """Shutdown the MQTT client."""
        if self._client:
            try:
                # Publish offline status before disconnecting
                self._publish_offline_status()
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                LOGGER.debug("Error closing health monitor MQTT connection: %s", exc)
            self._client = None
            self._connected = False


__all__ = ["HealthMonitor"]
