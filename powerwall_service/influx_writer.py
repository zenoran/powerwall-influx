"""InfluxDB writer for Powerwall metrics."""

import math
import time
from datetime import datetime
from typing import Dict, Optional

import requests

from .config import ServiceConfig
from .metrics import extract_snapshot_metrics


class InfluxWriter:
    """Write Powerwall metrics to InfluxDB using line protocol."""
    
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._session = requests.Session()
        self._write_url = f"{config.influx_url.rstrip('/')}/api/v2/write"

    @staticmethod
    def _escape(value: str) -> str:
        """Escape special characters in InfluxDB line protocol."""
        return (
            value.replace("\\", "\\\\")
            .replace(",", "\\,")
            .replace(" ", "\\ ")
            .replace("=", "\\=")
        )

    @staticmethod
    def _escape_str_field(value: str) -> str:
        """Escape string field values in InfluxDB line protocol."""
        return value.replace("\\", "\\\\").replace("\"", "\\\"")

    def build_line(self, snapshot: Dict[str, object]) -> Optional[str]:
        """Build an InfluxDB line protocol string from a snapshot.
        
        Uses the shared extract_snapshot_metrics() function to parse the snapshot,
        then formats the metrics into InfluxDB line protocol.
        
        Args:
            snapshot: Powerwall snapshot dictionary
            
        Returns:
            InfluxDB line protocol string, or None if no fields to write
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
        """Write a line protocol string to InfluxDB.
        
        Args:
            line: InfluxDB line protocol string
            
        Raises:
            RuntimeError: If the write fails
        """
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
