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

        # Primary state - reduced redundancy by deriving most values from _last_result
        self._last_result: Optional[PollingResult] = None
        self._consecutive_failures = 0
        
        # Success timestamps (not stored in PollingResult for backward compat)
        self._last_success_at: Optional[datetime] = None
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
    # Properties that derive error state from _last_result (read-only)
    # This eliminates redundant state variables
    
    @property
    def _last_powerwall_error(self) -> Optional[str]:
        """Most recent Powerwall error from last polling result."""
        return self._last_result.powerwall_error if self._last_result else None
    
    @property
    def _last_influx_error(self) -> Optional[str]:
        """Most recent InfluxDB error from last polling result."""
        return self._last_result.influx_error if self._last_result else None
    
    @property
    def _last_mqtt_error(self) -> Optional[str]:
        """Most recent MQTT error from last polling result."""
        return self._last_result.mqtt_error if self._last_result else None
    
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
        """Get comprehensive health report for all service components.
        
        Uses a factory method to reduce repetition in component creation.
        """
        components: Dict[str, ComponentHealth] = {}

        # Powerwall component
        components["powerwall"] = ComponentHealth(
            name="powerwall",
            healthy=self._last_powerwall_error is None,
            detail=self._last_powerwall_error,
            last_success=self._last_success_at,
            last_error=self._last_powerwall_error,
        )

        # InfluxDB component
        components["influxdb"] = ComponentHealth(
            name="influxdb",
            healthy=self._last_influx_error is None,
            detail=self._last_influx_error,
            last_success=self._last_influx_success,
            last_error=self._last_influx_error,
        )

        # MQTT component (varies based on whether MQTT is enabled)
        if self._mqtt:
            mqtt_healthy = self._last_mqtt_error is None and self._mqtt.connected
            mqtt_detail = self._last_mqtt_error or ("MQTT not connected" if not mqtt_healthy else None)
        else:
            mqtt_healthy = not self._config.mqtt_enabled
            mqtt_detail = None if not self._config.mqtt_enabled else "MQTT disabled"
        
        components["mqtt"] = ComponentHealth(
            name="mqtt",
            healthy=mqtt_healthy,
            detail=mqtt_detail,
            last_success=self._last_mqtt_success,
            last_error=self._last_mqtt_error,
        )

        # WiFi component (varies based on whether WiFi auto-connect is enabled)
        if self._config.connect_wifi:
            wifi_healthy = self._last_wifi_error is None
            wifi_detail = self._last_wifi_error
        else:
            wifi_healthy = True
            wifi_detail = "Wi-Fi auto-connect disabled"
        
        components["wifi"] = ComponentHealth(
            name="wifi",
            healthy=wifi_healthy,
            detail=wifi_detail,
            last_success=None,  # Not tracking WiFi success time
            last_error=self._last_wifi_error,
        )

        # Overall health is true only if all components are healthy
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
            LOGGER.warning("Powerwall gateway unreachable (failure %d): %s", 
                          self._consecutive_failures + 1, exc)
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
                    except Exception as exc:
                        influx_error = str(exc)
                        LOGGER.warning("InfluxDB write failed: %s", exc)
            if publish_mqtt and self._mqtt:
                try:
                    self._mqtt.publish(snapshot)
                    self._mqtt.publish_availability(True)
                    published = True
                except Exception as exc:
                    mqtt_error = str(exc)
                    LOGGER.warning("Failed to publish metrics to MQTT: %s", exc)

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
        """Update service state from a polling result.
        
        Note: Error states are now derived from _last_result properties,
        so we don't need separate error variable assignments.
        """
        self._last_result = result
        if result.success:
            self._last_success_at = result.timestamp
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1

        if result.pushed_influx:
            self._last_influx_success = result.timestamp
        if result.published_mqtt:
            self._last_mqtt_success = result.timestamp

        if not result.success and self._mqtt:
            self._mqtt.publish_availability(False, status_message=result.powerwall_error)

    def _handle_powerwall_failure(self, message: str) -> None:
        """Handle Powerwall connection/polling failures.
        
        Note: Error state is stored in _last_result, not in a separate variable.
        This method handles WiFi reconnection logic when failures occur.
        """
        if self._mqtt:
            self._mqtt.publish_availability(False, status_message=message[:512])
        if self._config.connect_wifi:
            now = time.monotonic()
            time_since_last_wifi_attempt = now - self._last_wifi_attempt
            if time_since_last_wifi_attempt >= self._wifi_retry_seconds:
                LOGGER.info(
                    "Attempting WiFi reconnection to '%s' (%ds since last attempt)",
                    self._config.wifi_ssid,
                    int(time_since_last_wifi_attempt)
                )
                try:
                    wifi_reconnected = maybe_connect_wifi(self._config)
                    self._last_wifi_error = None  # Success
                    # CRITICAL FIX: Only reset backoff if WiFi actually reconnected
                    # Don't reset if we were already connected - that doesn't fix the gateway issue
                    if wifi_reconnected and hasattr(self._poller, '_consecutive_connection_failures'):
                        if self._poller._consecutive_connection_failures > 0:
                            LOGGER.info(
                                "WiFi reconnected successfully (was disconnected), resetting connection failure counter "
                                "(%d failures) to allow reconnection",
                                self._poller._consecutive_connection_failures
                            )
                            self._poller._consecutive_connection_failures = 0
                            self._poller._last_connection_attempt = 0.0  # Reset backoff timer
                    elif not wifi_reconnected:
                        LOGGER.debug(
                            "WiFi already connected - not resetting backoff counter (%d failures remain)",
                            self._poller._consecutive_connection_failures if hasattr(self._poller, '_consecutive_connection_failures') else 0
                        )
                except Exception as exc:  # pragma: no cover - environment dependent
                    self._last_wifi_error = str(exc)
                    LOGGER.warning("Wi-Fi reconnection attempt failed: %s", exc)
                finally:
                    self._last_wifi_attempt = time.monotonic()
            else:
                waiting_time = self._wifi_retry_seconds - time_since_last_wifi_attempt
                LOGGER.debug(
                    "Skipping WiFi reconnection (%.0fs until next attempt)",
                    waiting_time
                )

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
