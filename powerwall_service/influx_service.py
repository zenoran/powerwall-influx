#!/usr/bin/env python3
"""Deprecated entry point kept for backward compatibility.

This module now forwards to :mod:`powerwall_service.cli`. Prefer calling
``python -m powerwall_service.cli`` directly.
"""

from __future__ import annotations

import argparse
import warnings
from typing import List, Optional

from . import cli as _cli

_DEPRECATION_MESSAGE = (
    "powerwall_service.influx_service is deprecated; use "
    "`python -m powerwall_service.cli` instead."
)


def main(argv: Optional[List[str]] = None) -> int:
    warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", help="Path to .env file")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll cycle and exit",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Skip writing metrics to InfluxDB during --once",
    )
    parser.add_argument(
        "--publish-mqtt",
        action="store_true",
        help="Publish MQTT metrics during --once",
    )
    args, remainder = parser.parse_known_args(argv)

    cmd: List[str] = []
    if args.once:
        cmd.append("poll")
        if args.env_file:
            cmd.extend(["--env-file", args.env_file])
        if args.no_push:
            cmd.append("--no-push")
        if args.publish_mqtt:
            cmd.append("--publish-mqtt")
        cmd.extend(remainder)
    else:
        cmd.append("serve")
        if args.env_file:
            cmd.extend(["--env-file", args.env_file])
        cmd.extend(remainder)

    return _cli.main(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
