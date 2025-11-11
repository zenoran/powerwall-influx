"""Integration tests for InfluxDB writer and metric extraction."""

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from powerwall_service.config import ServiceConfig
from powerwall_service.influx_writer import InfluxWriter
from powerwall_service.metrics import extract_snapshot_metrics, to_float, _extract_float


def create_test_config(**kwargs):
    """Helper to create ServiceConfig with defaults for testing."""
    defaults = {
        'host': '192.168.1.100',
        'gateway_password': 'test',
        'influx_url': 'http://localhost:8086',
        'influx_token': 'test_token',
        'influx_org': 'test_org',
        'influx_bucket': 'test_bucket',
        'measurement': 'powerwall',
        'influx_timeout': 10.0,
        'influx_verify_tls': False,
        'poll_interval': 5.0,
        'timezone_name': 'UTC',
        'cache_expire': 5,
        'request_timeout': 10,
        'wifi_ssid': None,
        'wifi_password': None,
        'wifi_interface': None,
        'connect_wifi': False,
        'customer_email': None,
        'customer_password': None,
        'log_level': 'INFO',
        'mqtt_enabled': False,
        'mqtt_host': 'localhost',
        'mqtt_port': 1883,
        'mqtt_username': None,
        'mqtt_password': None,
        'mqtt_topic_prefix': 'powerwall',
        'mqtt_qos': 1,
        'mqtt_retain': True,
        'mqtt_metrics': set(),
        'mqtt_health_enabled': False,
        'mqtt_health_host': 'localhost',
        'mqtt_health_port': 1883,
        'mqtt_health_username': None,
        'mqtt_health_password': None,
        'mqtt_health_topic_prefix': 'powerwall',
        'mqtt_health_interval': 60.0,
        'mqtt_health_qos': 1,
    }
    defaults.update(kwargs)
    return ServiceConfig(**defaults)


class TestMetricsExtraction(unittest.TestCase):
    """Test metric extraction functions."""

    def test_to_float_with_none(self):
        """Test to_float handles None correctly."""
        self.assertIsNone(to_float(None))
        self.assertEqual(to_float(None, default=0.0), 0.0)

    def test_to_float_with_numbers(self):
        """Test to_float handles numeric types."""
        self.assertEqual(to_float(42), 42.0)
        self.assertEqual(to_float(3.14), 3.14)
        self.assertEqual(to_float(0), 0.0)

    def test_to_float_with_string(self):
        """Test to_float handles string conversions."""
        self.assertEqual(to_float("42.5"), 42.5)
        self.assertEqual(to_float("100"), 100.0)

    def test_to_float_with_invalid_string(self):
        """Test to_float handles invalid strings."""
        self.assertIsNone(to_float("not_a_number"))
        self.assertEqual(to_float("invalid", default=99.9), 99.9)

    def test_to_float_with_invalid_type(self):
        """Test to_float handles invalid types."""
        self.assertIsNone(to_float({}))
        self.assertIsNone(to_float([]))
        self.assertEqual(to_float(object(), default=1.0), 1.0)

    def test_extract_float_simple_path(self):
        """Test _extract_float with simple path."""
        data = {"key1": {"key2": "42.5"}}
        result = _extract_float(data, ["key1", "key2"])
        self.assertEqual(result, 42.5)

    def test_extract_float_missing_key(self):
        """Test _extract_float with missing key."""
        data = {"key1": {"key2": "42.5"}}
        result = _extract_float(data, ["key1", "missing"])
        self.assertIsNone(result)

    def test_extract_float_not_dict(self):
        """Test _extract_float when path leads to non-dict."""
        data = {"key1": "not_a_dict"}
        result = _extract_float(data, ["key1", "key2"])
        self.assertIsNone(result)

    def test_extract_snapshot_metrics_basic(self):
        """Test extract_snapshot_metrics with basic snapshot."""
        snapshot = {
            "battery_percentage": 85.5,
            "power": {
                "site": 1000,
                "solar": 5000,
                "battery": -2000,
                "load": 4000
            },
            "alerts": [],
            "grid_status": "UP",
            "din": "ABC123"
        }

        metrics = extract_snapshot_metrics(snapshot)

        self.assertEqual(metrics["battery_percentage"], 85.5)
        self.assertEqual(metrics["site_power_w"], 1000.0)
        self.assertEqual(metrics["solar_power_w"], 5000.0)
        self.assertEqual(metrics["battery_power_w"], -2000.0)
        self.assertEqual(metrics["load_power_w"], 4000.0)
        self.assertEqual(metrics["alerts_count"], 0)
        self.assertEqual(metrics["grid_status"], "UP")
        self.assertEqual(metrics["din"], "ABC123")

    def test_extract_snapshot_metrics_with_alerts(self):
        """Test extract_snapshot_metrics with alerts."""
        snapshot = {
            "alerts": ["INVERTER_FAULT", "BATTERY_LOW"]
        }

        metrics = extract_snapshot_metrics(snapshot)

        self.assertEqual(metrics["alerts_count"], 2)
        self.assertIn("BATTERY_LOW", metrics["alerts"])
        self.assertIn("INVERTER_FAULT", metrics["alerts"])

    def test_extract_snapshot_metrics_with_vitals(self):
        """Test extract_snapshot_metrics with string vitals."""
        snapshot = {
            "din": "TEST123",
            "vitals": {
                "PVS--TEST123": {
                    "PVS_StringA_Connected": True,
                    "PVS_StringB_Connected": False,
                },
                "PVAC--TEST123": {
                    "PVAC_PvState_A": "PV_Active",
                    "PVAC_PVMeasuredVoltage_A": 120.5,
                    "PVAC_PVCurrent_A": 8.2,
                    "PVAC_PVMeasuredPower_A": 988.1,
                    "PVAC_PvState_B": "PV_Inactive",
                }
            }
        }

        metrics = extract_snapshot_metrics(snapshot)

        # Check string A metrics
        self.assertEqual(metrics["string_stringa_connected"], True)
        self.assertEqual(metrics["string_a_state"], "PV_Active")
        self.assertEqual(metrics["string_a_voltage_v"], 120.5)
        self.assertEqual(metrics["string_a_current_a"], 8.2)
        self.assertEqual(metrics["string_a_power_w"], 988.1)

        # Check string B metrics
        self.assertEqual(metrics["string_stringb_connected"], False)
        self.assertEqual(metrics["string_b_state"], "PV_Inactive")

    def test_extract_snapshot_metrics_with_battery_energy(self):
        """Test extract_snapshot_metrics with battery energy data."""
        snapshot = {
            "battery_nominal_energy_remaining": 13500,
            "battery_nominal_full_energy": 27000
        }

        metrics = extract_snapshot_metrics(snapshot)

        self.assertEqual(metrics["battery_nominal_energy_remaining_wh"], 13500.0)
        self.assertEqual(metrics["battery_nominal_full_energy_wh"], 27000.0)

    def test_extract_snapshot_metrics_empty_snapshot(self):
        """Test extract_snapshot_metrics with empty snapshot."""
        snapshot = {}
        metrics = extract_snapshot_metrics(snapshot)

        # Should have None values but not crash
        self.assertIsNone(metrics.get("battery_percentage"))
        self.assertIsNone(metrics.get("grid_status"))


class TestInfluxWriter(unittest.TestCase):
    """Test InfluxDB writer functionality."""

    def setUp(self):
        """Set up test configuration."""
        self.config = create_test_config()
        self.writer = InfluxWriter(self.config)

    def test_escape_special_characters(self):
        """Test line protocol escaping."""
        # Test comma escaping
        self.assertEqual(self.writer._escape("test,value"), "test\\,value")
        # Test space escaping
        self.assertEqual(self.writer._escape("test value"), "test\\ value")
        # Test equals escaping
        self.assertEqual(self.writer._escape("test=value"), "test\\=value")
        # Test backslash escaping
        self.assertEqual(self.writer._escape("test\\value"), "test\\\\value")

    def test_escape_string_field(self):
        """Test string field escaping."""
        self.assertEqual(self.writer._escape_str_field('test"value'), 'test\\"value')
        self.assertEqual(self.writer._escape_str_field('test\\value'), 'test\\\\value')

    def test_build_line_basic_metrics(self):
        """Test building line protocol with basic metrics."""
        snapshot = {
            "timestamp": datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            "site_name": "Home",
            "battery_percentage": 85.5,
            "power": {
                "site": 1000,
                "solar": 5000,
                "battery": -2000,
                "load": 4000
            },
            "grid_status": "UP",
            "alerts": []
        }

        line = self.writer.build_line(snapshot)

        # Verify structure: measurement,tags fields timestamp
        self.assertIsNotNone(line)
        self.assertIn("powerwall,site=Home", line)
        self.assertIn("battery_percentage=85.5", line)
        self.assertIn("site_power_w=1000", line)
        self.assertIn("solar_power_w=5000", line)
        self.assertIn("battery_power_w=-2000", line)
        self.assertIn("load_power_w=4000", line)
        self.assertIn('grid_status="UP"', line)

    def test_build_line_with_integers(self):
        """Test building line protocol with integer values."""
        snapshot = {
            "site_name": "Home",
            "alerts": ["ALERT1", "ALERT2"]
        }

        line = self.writer.build_line(snapshot)

        # Integers should have 'i' suffix
        self.assertIn("alerts_count=2i", line)

    def test_build_line_with_booleans(self):
        """Test building line protocol with boolean values."""
        snapshot = {
            "site_name": "Home",
            "din": "TEST123",
            "vitals": {
                "PVS--TEST123": {
                    "PVS_StringA_Connected": True,
                    "PVS_StringB_Connected": False,
                }
            }
        }

        line = self.writer.build_line(snapshot)

        # Booleans should be true/false (lowercase)
        self.assertIn("string_stringa_connected=true", line)
        self.assertIn("string_stringb_connected=false", line)

    def test_build_line_filters_nan_and_inf(self):
        """Test that NaN and Inf values are filtered out."""
        snapshot = {
            "site_name": "Home",
            "battery_percentage": float('nan'),
            "power": {
                "solar": float('inf')
            }
        }

        line = self.writer.build_line(snapshot)

        # NaN and Inf should result in None (no valid metrics)
        self.assertIsNone(line)

    def test_build_line_empty_snapshot(self):
        """Test building line protocol with empty snapshot returns None."""
        snapshot = {"site_name": "Home"}
        line = self.writer.build_line(snapshot)

        # Should return None if no fields to write
        self.assertIsNone(line)

    @patch('powerwall_service.influx_writer.requests.Session.post')
    def test_write_success(self, mock_post):
        """Test successful write to InfluxDB."""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        line = "powerwall,site=Home battery_percentage=85.5 1704110400000000000"
        self.writer.write(line)

        # Verify POST was called with correct parameters
        mock_post.assert_called_once()
        call_args = mock_post.call_args

        self.assertEqual(call_args[1]['headers']['Authorization'], 'Token test_token')
        self.assertEqual(call_args[1]['params']['org'], 'test_org')
        self.assertEqual(call_args[1]['params']['bucket'], 'test_bucket')
        self.assertEqual(call_args[1]['params']['precision'], 'ns')

    @patch('powerwall_service.influx_writer.requests.Session.post')
    def test_write_failure(self, mock_post):
        """Test failed write to InfluxDB."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"
        mock_post.return_value = mock_response

        line = "powerwall,site=Home battery_percentage=85.5 1704110400000000000"

        with self.assertRaises(RuntimeError) as ctx:
            self.writer.write(line)

        self.assertIn("InfluxDB write failed", str(ctx.exception))
        self.assertIn("400", str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
