"""Command line utilities for the Powerwall Influx service."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .app import _configure_logging  # reuse logging setup
from .config import DEFAULT_ENV_PATH, build_config, load_env_file
from .service import PowerwallService


def _load_environment(explicit: Optional[str]) -> None:
    env_file = explicit or os.environ.get("POWERWALL_ENV_FILE")
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


def _poll_command(args: argparse.Namespace) -> int:
    _load_environment(args.env_file)
    config = build_config()
    _configure_logging(config.log_level)

    async def _run() -> Dict[str, Any]:
        service = PowerwallService(config)
        try:
            result = await service.poll_once(
                push_to_influx=not args.no_push,
                publish_mqtt=args.publish_mqtt,
                store_result=False,
            )
            payload: Dict[str, Any] = {
                "success": result.success,
                "duration": result.duration,
                "timestamp": result.timestamp.isoformat(),
                "pushed_influx": result.pushed_influx,
                "published_mqtt": result.published_mqtt,
                "powerwall_error": result.powerwall_error,
                "influx_error": result.influx_error,
                "mqtt_error": result.mqtt_error,
            }
            if args.include_snapshot and result.snapshot is not None:
                payload["snapshot"] = result.snapshot
            return payload
        finally:
            await service.stop()

    payload = asyncio.run(_run())
    output = json.dumps(payload, indent=2 if args.pretty else None, default=str)
    print(output)
    return 0 if payload["success"] else 1


def _serve_command(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:  # pragma: no cover - user environment issue
        raise SystemExit("uvicorn is required for the 'serve' command. Install fastapi extras.")

    app_path = "powerwall_service.app:create_app"
    uvicorn.run(
        app_path,
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level.lower(),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Utilities for the Powerwall Influx service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    poll = subparsers.add_parser("poll", help="Execute a single polling cycle")
    poll.add_argument("--env-file", help="Path to .env file overriding defaults")
    poll.add_argument(
        "--no-push",
        action="store_true",
        help="Do not push results to InfluxDB",
    )
    poll.add_argument(
        "--publish-mqtt",
        action="store_true",
        help="Publish MQTT metrics even without Influx push",
    )
    poll.add_argument(
        "--include-snapshot",
        action="store_true",
        help="Include the raw snapshot payload in the output",
    )
    poll.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )
    poll.set_defaults(func=_poll_command)

    serve = subparsers.add_parser("serve", help="Run the FastAPI service with uvicorn")
    serve.add_argument("--host", default="0.0.0.0", help="Bind address for uvicorn")
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    serve.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (development only)",
    )
    serve.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Uvicorn log level",
    )
    serve.add_argument("--env-file", help="Path to .env file overriding defaults")

    def _serve_wrapper(args: argparse.Namespace) -> int:
        _load_environment(args.env_file)
        config = build_config()
        _configure_logging(config.log_level)
        return _serve_command(args)

    serve.set_defaults(func=_serve_wrapper)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # pragma: no cover - CLI surface
        logging.getLogger("powerwall_service.cli").exception("Command failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
