"""Asynchronous background service orchestrating Powerwall polling."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .clients import (
    InfluxWriter,
    MQTTPublisher,
    PowerwallPoller,
    PowerwallUnavailableError,
    maybe_connect_wifi,
)
from .config import ServiceConfig
from .health_monitor import HealthMonitor

LOGGER = logging.getLogger("powerwall_service.service")


@dataclass
class ComponentHealth:
    name: str
    healthy: bool
    detail: Optional[str] = None
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None


@dataclass
class PollingResult:
    timestamp: datetime
    duration: float
    snapshot: Optional[Dict[str, Any]]
    powerwall_error: Optional[str] = None
    influx_error: Optional[str] = None
    mqtt_error: Optional[str] = None
    pushed_influx: bool = False
    published_mqtt: bool = False

    @property
    def success(self) -> bool:
        return self.snapshot is not None and self.powerwall_error is None


@dataclass
class HealthReport:
    overall: bool
    components: Dict[str, ComponentHealth]
    last_poll_time: Optional[datetime]
    last_success_time: Optional[datetime]
    consecutive_failures: int
    background_task_running: bool


class PowerwallService:
    """Manage a background polling loop plus ad-hoc REST-triggered polls."""

    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._poller = PowerwallPoller(config)
        self._writer = InfluxWriter(config)
        self._mqtt = MQTTPublisher(config) if config.mqtt_enabled else None

        self._poll_lock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()

        self._last_result: Optional[PollingResult] = None
        self._last_success_at: Optional[datetime] = None
        self._consecutive_failures = 0
        self._last_powerwall_error: Optional[str] = None
        self._last_influx_error: Optional[str] = None
        self._last_mqtt_error: Optional[str] = None
        self._last_influx_success: Optional[datetime] = None
        self._last_mqtt_success: Optional[datetime] = None
        self._last_wifi_error: Optional[str] = None

        self._wifi_retry_seconds = 300
        self._last_wifi_attempt = 0.0
        
        # Initialize independent health monitor
        self._health_monitor: Optional[HealthMonitor] = None
        if config.mqtt_health_enabled:
            try:
                self._health_monitor = HealthMonitor(
                    mqtt_host=config.mqtt_health_host,
                    mqtt_port=config.mqtt_health_port,
                    health_getter=self.get_health_report,
                    mqtt_username=config.mqtt_health_username,
                    mqtt_password=config.mqtt_health_password,
                    topic_prefix=config.mqtt_health_topic_prefix,
                    device_name="Powerwall Service",
                    publish_interval=config.mqtt_health_interval,
                    qos=config.mqtt_health_qos,
                )
            except Exception as exc:
                LOGGER.error("Failed to initialize health monitor: %s", exc)
                self._health_monitor = None

    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._background_task and not self._background_task.done():
            return
        self._stop_event.clear()
        await asyncio.to_thread(self._maybe_join_wifi)
        
        # Start health monitor first so it can track service startup
        if self._health_monitor:
            try:
                await self._health_monitor.start()
            except Exception as exc:
                LOGGER.error("Failed to start health monitor: %s", exc)
        
        self._background_task = asyncio.create_task(self._run_loop(), name="powerwall-poller")

    async def stop(self) -> None:
        if self._background_task is None:
            await asyncio.to_thread(self._shutdown_clients)
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
            await asyncio.to_thread(self._shutdown_clients)
        
        # Stop health monitor last so it can report final status
        if self._health_monitor:
            try:
                await self._health_monitor.stop()
            except Exception as exc:
                LOGGER.error("Failed to stop health monitor: %s", exc)

    # ------------------------------------------------------------------
    async def poll_once(
        self,
        *,
        push_to_influx: bool = True,
        publish_mqtt: Optional[bool] = None,
        store_result: bool = True,
    ) -> PollingResult:
        if publish_mqtt is None:
            publish_mqtt = push_to_influx
        async with self._poll_lock:
            result = await asyncio.to_thread(
                self._poll_once_blocking,
                push_to_influx,
                publish_mqtt,
            )
            if store_result:
                self._update_state(result)
            return result

    async def live_snapshot(self, *, push: bool = False, publish: bool = False) -> PollingResult:
        return await self.poll_once(push_to_influx=push, publish_mqtt=publish, store_result=False)

    def get_latest_result(self) -> Optional[PollingResult]:
        return self._last_result

    def is_running(self) -> bool:
        return self._background_task is not None and not self._background_task.done()

    def get_health_report(self) -> HealthReport:
        components: Dict[str, ComponentHealth] = {}

        components["powerwall"] = ComponentHealth(
            name="powerwall",
            healthy=self._last_powerwall_error is None,
            detail=None if self._last_powerwall_error is None else self._last_powerwall_error,
            last_success=self._last_success_at,
            last_error=self._last_powerwall_error,
        )

        components["influxdb"] = ComponentHealth(
            name="influxdb",
            healthy=self._last_influx_error is None,
            detail=None if self._last_influx_error is None else self._last_influx_error,
            last_success=self._last_influx_success,
            last_error=self._last_influx_error,
        )

        if self._mqtt:
            healthy = self._last_mqtt_error is None and self._mqtt.connected
            detail = None
            if not healthy:
                detail = self._last_mqtt_error or "MQTT not connected"
            components["mqtt"] = ComponentHealth(
                name="mqtt",
                healthy=healthy,
                detail=detail,
                last_success=self._last_mqtt_success,
                last_error=self._last_mqtt_error,
            )
        else:
            components["mqtt"] = ComponentHealth(
                name="mqtt",
                healthy=not self._config.mqtt_enabled,
                detail=None if not self._config.mqtt_enabled else "MQTT disabled",
                last_success=self._last_mqtt_success,
                last_error=self._last_mqtt_error,
            )

        if self._config.connect_wifi:
            components["wifi"] = ComponentHealth(
                name="wifi",
                healthy=self._last_wifi_error is None,
                detail=None if self._last_wifi_error is None else self._last_wifi_error,
                last_success=None,  # Not tracking success time
                last_error=self._last_wifi_error,
            )
        else:
            components["wifi"] = ComponentHealth(
                name="wifi",
                healthy=True,
                detail="Wi-Fi auto-connect disabled",
                last_success=None,
                last_error=None,
            )

        overall = all(component.healthy for component in components.values())
        return HealthReport(
            overall=overall,
            components=components,
            last_poll_time=self._last_result.timestamp if self._last_result else None,
            last_success_time=self._last_success_at,
            consecutive_failures=self._consecutive_failures,
            background_task_running=self.is_running(),
        )

    # ------------------------------------------------------------------
    async def _run_loop(self) -> None:
        LOGGER.info("Starting background polling loop (interval=%ss)", self._config.poll_interval)
        try:
            while not self._stop_event.is_set():
                start = time.monotonic()
                try:
                    await self.poll_once()
                except Exception as exc:  # pragma: no cover - defensive
                    LOGGER.exception("Background poll failed: %s", exc)
                elapsed = time.monotonic() - start
                sleep_for = max(0.0, self._config.poll_interval - elapsed)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
                except asyncio.TimeoutError:
                    continue
        finally:
            LOGGER.info("Background polling loop stopped")

    def _poll_once_blocking(self, push_to_influx: bool, publish_mqtt: bool) -> PollingResult:
        start = time.monotonic()
        snapshot: Optional[Dict[str, Any]] = None
        powerwall_error: Optional[str] = None
        influx_error: Optional[str] = None
        mqtt_error: Optional[str] = None
        pushed_influx = False
        published = False

        try:
            snapshot = self._poller.fetch_snapshot()
        except PowerwallUnavailableError as exc:
            powerwall_error = str(exc)
            LOGGER.warning("Powerwall gateway unreachable: %s", exc)
            self._handle_powerwall_failure(powerwall_error)
        except Exception as exc:  # pragma: no cover - unexpected
            powerwall_error = f"unexpected error: {exc}"
            LOGGER.exception("Unexpected error fetching Powerwall snapshot: %s", exc)
            self._handle_powerwall_failure(powerwall_error)
        else:
            if push_to_influx:
                line = self._writer.build_line(snapshot)
                if line is None:
                    LOGGER.warning("No fields to write; skipping this cycle")
                else:
                    try:
                        self._writer.write(line)
                        pushed_influx = True
                        self._last_influx_error = None
                    except Exception as exc:
                        influx_error = str(exc)
                        LOGGER.warning("InfluxDB write failed: %s", exc)
                        self._last_influx_error = influx_error
            if publish_mqtt and self._mqtt:
                try:
                    self._mqtt.publish(snapshot)
                    self._mqtt.publish_availability(True)
                    published = True
                    self._last_mqtt_error = None
                except Exception as exc:
                    mqtt_error = str(exc)
                    LOGGER.warning("Failed to publish metrics to MQTT: %s", exc)
                    self._last_mqtt_error = mqtt_error

        if snapshot is None and powerwall_error is None:
            powerwall_error = "snapshot unavailable"

        duration = time.monotonic() - start
        return PollingResult(
            timestamp=datetime.now(timezone.utc),
            duration=duration,
            snapshot=snapshot,
            powerwall_error=powerwall_error,
            influx_error=influx_error,
            mqtt_error=mqtt_error,
            pushed_influx=pushed_influx,
            published_mqtt=published,
        )

    def _update_state(self, result: PollingResult) -> None:
        self._last_result = result
        if result.success:
            self._last_success_at = result.timestamp
            self._consecutive_failures = 0
            self._last_powerwall_error = None
        else:
            self._consecutive_failures += 1
            self._last_powerwall_error = result.powerwall_error

        if result.pushed_influx:
            self._last_influx_success = result.timestamp
        if result.published_mqtt:
            self._last_mqtt_success = result.timestamp
        if result.influx_error:
            self._last_influx_error = result.influx_error
        if result.mqtt_error:
            self._last_mqtt_error = result.mqtt_error

        if not result.success and self._mqtt:
            self._mqtt.publish_availability(False, status_message=result.powerwall_error)

    def _handle_powerwall_failure(self, message: str) -> None:
        self._last_powerwall_error = message
        if self._mqtt:
            self._mqtt.publish_availability(False, status_message=message[:512])
        if self._config.connect_wifi:
            now = time.monotonic()
            if now - self._last_wifi_attempt >= self._wifi_retry_seconds:
                try:
                    maybe_connect_wifi(self._config)
                    self._last_wifi_error = None  # Success
                except Exception as exc:  # pragma: no cover - environment dependent
                    self._last_wifi_error = str(exc)
                    LOGGER.warning("Wi-Fi reconnection attempt failed: %s", exc)
                finally:
                    self._last_wifi_attempt = time.monotonic()

    def _maybe_join_wifi(self) -> None:
        try:
            maybe_connect_wifi(self._config)
            self._last_wifi_error = None
        except Exception as exc:
            self._last_wifi_error = str(exc)
            LOGGER.warning("Initial Wi-Fi connection failed: %s", exc)

    def _shutdown_clients(self) -> None:
        self._poller.close()
        if self._mqtt:
            self._mqtt.close()


__all__ = [
    "ComponentHealth",
    "HealthReport",
    "PollingResult",
    "PowerwallService",
]
