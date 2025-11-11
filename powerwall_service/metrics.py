"""Metric extraction and helper functions for Powerwall data."""

from typing import Dict, Iterable, Optional


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
