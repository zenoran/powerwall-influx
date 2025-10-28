"""FastAPI application exposing Powerwall metrics management endpoints."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from .config import DEFAULT_ENV_PATH, build_config, load_env_file, redact_config
from .service import HealthReport, PollingResult, PowerwallService

LOGGER = logging.getLogger("powerwall_service.app")


class PollResponse(BaseModel):
    success: bool
    duration: float = Field(..., description="Duration of the polling cycle in seconds")
    timestamp: Optional[str]
    snapshot: Optional[Dict[str, Any]] = None
    powerwall_error: Optional[str] = None
    influx_error: Optional[str] = None
    mqtt_error: Optional[str] = None
    pushed_influx: bool
    published_mqtt: bool


class HealthComponentResponse(BaseModel):
    name: str
    healthy: bool
    detail: Optional[str] = None
    last_success: Optional[str] = None
    last_error: Optional[str] = None


class HealthResponse(BaseModel):
    overall: bool
    components: List[HealthComponentResponse]
    last_poll_time: Optional[str] = None
    last_success_time: Optional[str] = None
    consecutive_failures: int
    background_task_running: bool


class PollRequest(BaseModel):
    push_to_influx: bool = True
    publish_mqtt: Optional[bool] = None
    store_result: bool = True


def _poll_to_response(result: PollingResult) -> PollResponse:
    return PollResponse(
        success=result.success,
        duration=result.duration,
        timestamp=result.timestamp.isoformat() if result.timestamp else None,
        snapshot=result.snapshot,
        powerwall_error=result.powerwall_error,
        influx_error=result.influx_error,
        mqtt_error=result.mqtt_error,
        pushed_influx=result.pushed_influx,
        published_mqtt=result.published_mqtt,
    )


def _health_to_response(report: HealthReport) -> HealthResponse:
    components = [
        HealthComponentResponse(
            name=component.name,
            healthy=component.healthy,
            detail=component.detail,
            last_success=component.last_success.isoformat() if component.last_success else None,
            last_error=component.last_error,
        )
        for component in report.components.values()
    ]
    return HealthResponse(
        overall=report.overall,
        components=components,
        last_poll_time=report.last_poll_time.isoformat() if report.last_poll_time else None,
        last_success_time=report.last_success_time.isoformat() if report.last_success_time else None,
        consecutive_failures=report.consecutive_failures,
        background_task_running=report.background_task_running,
    )


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def _lifespan(app: FastAPI):
    env_file = os.environ.get("POWERWALL_ENV_FILE")
    candidates = []
    if env_file:
        candidates.append(Path(env_file))
    candidates.append(Path.cwd() / ".env")
    candidates.append(DEFAULT_ENV_PATH)
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            load_env_file(resolved)
            break
    config = build_config()
    _configure_logging(config.log_level)
    service = PowerwallService(config)
    await service.start()
    app.state.config = config
    app.state.service = service
    LOGGER.info("Powerwall service started")
    try:
        yield
    finally:
        LOGGER.info("Shutting down Powerwall service")
        await service.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Powerwall Influx Service", version="2.0.0", lifespan=_lifespan)

    @app.get("/", response_model=Dict[str, str])
    async def root() -> Dict[str, str]:
        return {"service": "powerwall-influx", "status": "ok"}

    def get_service(request: Request) -> PowerwallService:
        service = getattr(request.app.state, "service", None)
        if service is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service not ready")
        return service

    def get_config(request: Request):
        return getattr(request.app.state, "config", None)

    @app.get("/health", response_model=HealthResponse)
    async def health(service: PowerwallService = Depends(get_service)) -> HealthResponse:
        report = service.get_health_report()
        return _health_to_response(report)

    @app.get("/config", response_model=Dict[str, Any])
    async def config_endpoint(cfg=Depends(get_config)) -> Dict[str, Any]:
        if cfg is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Configuration unavailable")
        return redact_config(cfg)

    @app.get("/snapshot", response_model=PollResponse)
    async def get_snapshot(service: PowerwallService = Depends(get_service)) -> PollResponse:
        result = service.get_latest_result()
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No snapshot available yet")
        return _poll_to_response(result)

    @app.get("/snapshot/live", response_model=PollResponse)
    async def live_snapshot(
        service: PowerwallService = Depends(get_service),
        push_to_influx: bool = False,
        publish_mqtt: bool = False,
    ) -> PollResponse:
        result = await service.live_snapshot(push=push_to_influx, publish=publish_mqtt)
        return _poll_to_response(result)

    @app.post("/poll", response_model=PollResponse)
    async def trigger_poll(
        request: PollRequest,
        service: PowerwallService = Depends(get_service),
    ) -> PollResponse:
        publish_mqtt = (
            request.publish_mqtt
            if request.publish_mqtt is not None
            else request.push_to_influx
        )
        result = await service.poll_once(
            push_to_influx=request.push_to_influx,
            publish_mqtt=publish_mqtt,
            store_result=request.store_result,
        )
        return _poll_to_response(result)

    @app.get("/status", response_model=Dict[str, Any])
    async def status_endpoint(service: PowerwallService = Depends(get_service)) -> Dict[str, Any]:
        latest = service.get_latest_result()
        report = service.get_health_report()
        return {
            "running": service.is_running(),
            "last_poll": latest.timestamp.isoformat() if latest else None,
            "last_success": latest.timestamp.isoformat() if latest and latest.success else None,
            "consecutive_failures": report.consecutive_failures,
            "overall": report.overall,
        }

    return app


__all__ = ["create_app"]
