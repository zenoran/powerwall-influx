"""Integration tests for MQTT publisher."""

import unittest
from unittest.mock import Mock, patch, MagicMock, call

from powerwall_service.config import ServiceConfig
from powerwall_service.mqtt_publisher import MQTTPublisher, MQTT_AVAILABLE


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
        'mqtt_enabled': True,
        'mqtt_host': 'localhost',
        'mqtt_port': 1883,
        'mqtt_username': 'test_user',
        'mqtt_password': 'test_pass',
        'mqtt_topic_prefix': 'powerwall',
        'mqtt_qos': 1,
        'mqtt_retain': True,
        'mqtt_metrics': {'battery_percentage', 'site_power_w'},
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


@unittest.skipIf(not MQTT_AVAILABLE, "paho-mqtt not installed")
class TestMQTTPublisher(unittest.TestCase):
    """Test MQTT publisher functionality."""

    def setUp(self):
        """Set up test configuration."""
        self.config = create_test_config(
            mqtt_metrics={'battery_percentage', 'solar_power_w'}
        )

    @patch('powerwall_service.mqtt_publisher.mqtt.Client')
    def test_mqtt_initialization(self, mock_client_class):
        """Test MQTT client initialization."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(self.config)

        # Verify client was created
        mock_client_class.assert_called_once_with(client_id="powerwall_influx_service")

        # Verify credentials were set
        mock_client.username_pw_set.assert_called_once_with("test_user", "test_pass")

        # Verify connection was attempted
        mock_client.connect.assert_called_once_with("localhost", 1883, 60)
        mock_client.loop_start.assert_called_once()

    @patch('powerwall_service.mqtt_publisher.mqtt.Client')
    def test_mqtt_disabled(self, mock_client_class):
        """Test MQTT publisher when disabled."""
        config = create_test_config(mqtt_enabled=False)

        publisher = MQTTPublisher(config)

        # Client should not be created
        mock_client_class.assert_not_called()
        self.assertFalse(publisher.enabled)

    @patch('powerwall_service.mqtt_publisher.mqtt.Client')
    def test_publish_availability_online(self, mock_client_class):
        """Test publishing online availability status."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(self.config)
        publisher._connected = True

        publisher.publish_availability(True, "Service started")

        # Verify availability was published
        calls = mock_client.publish.call_args_list
        self.assertGreaterEqual(len(calls), 1)

        # Check that 'online' was published to availability topic
        availability_call = calls[0]
        self.assertIn("powerwall/availability", availability_call[0])
        self.assertEqual(availability_call[0][1], "online")

    @patch('powerwall_service.mqtt_publisher.mqtt.Client')
    def test_publish_availability_offline(self, mock_client_class):
        """Test publishing offline availability status."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(self.config)
        publisher._connected = True

        publisher.publish_availability(False)

        # Verify availability was published
        availability_call = mock_client.publish.call_args_list[0]
        self.assertIn("powerwall/availability", availability_call[0])
        self.assertEqual(availability_call[0][1], "offline")

    @patch('powerwall_service.mqtt_publisher.mqtt.Client')
    def test_publish_snapshot_metrics(self, mock_client_class):
        """Test publishing snapshot metrics to MQTT."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(self.config)
        publisher._connected = True

        snapshot = {
            "battery_percentage": 85.5,
            "power": {
                "site": 1000,
                "solar": 5000,
                "battery": -2000,
                "load": 4000
            },
            "grid_status": "UP"
        }

        publisher.publish(snapshot)

        # Verify metrics were published
        calls = mock_client.publish.call_args_list

        # Should publish battery_percentage and solar_power_w (configured metrics)
        topics = [call[0][0] for call in calls]

        self.assertIn("powerwall/battery_percentage/state", topics)
        self.assertIn("powerwall/solar_power_w/state", topics)

        # Should NOT publish unconfigured metrics
        self.assertNotIn("powerwall/site_power_w/state", topics)
        self.assertNotIn("powerwall/load_power_w/state", topics)

    @patch('powerwall_service.mqtt_publisher.mqtt.Client')
    def test_publish_all_metrics_when_not_filtered(self, mock_client_class):
        """Test publishing all metrics when no filter is configured."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Config without metric filter (empty set = publish all)
        config = create_test_config(mqtt_metrics=set())

        publisher = MQTTPublisher(config)
        publisher._connected = True

        snapshot = {
            "battery_percentage": 85.5,
            "power": {
                "site": 1000,
                "solar": 5000
            }
        }

        publisher.publish(snapshot)

        # Verify metrics were published
        calls = mock_client.publish.call_args_list
        topics = [call[0][0] for call in calls]

        # All metrics should be published
        self.assertIn("powerwall/battery_percentage/state", topics)
        self.assertIn("powerwall/site_power_w/state", topics)
        self.assertIn("powerwall/solar_power_w/state", topics)

    @patch('powerwall_service.mqtt_publisher.mqtt.Client')
    def test_publish_boolean_values(self, mock_client_class):
        """Test publishing boolean values as ON/OFF."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        config = create_test_config(mqtt_metrics=set())

        publisher = MQTTPublisher(config)
        publisher._connected = True

        snapshot = {
            "din": "TEST123",
            "vitals": {
                "PVS--TEST123": {
                    "PVS_StringA_Connected": True,
                    "PVS_StringB_Connected": False
                }
            }
        }

        publisher.publish(snapshot)

        # Find the boolean publishes
        calls = mock_client.publish.call_args_list
        published_values = {call[0][0]: call[0][1] for call in calls}

        self.assertEqual(published_values.get("powerwall/string_stringa_connected/state"), "ON")
        self.assertEqual(published_values.get("powerwall/string_stringb_connected/state"), "OFF")

    @patch('powerwall_service.mqtt_publisher.mqtt.Client')
    def test_publish_float_formatting(self, mock_client_class):
        """Test float values are formatted with 2 decimal places."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        config = create_test_config(mqtt_metrics=set())

        publisher = MQTTPublisher(config)
        publisher._connected = True

        snapshot = {
            "battery_percentage": 85.567
        }

        publisher.publish(snapshot)

        # Find the float publish
        calls = mock_client.publish.call_args_list
        battery_call = [c for c in calls if "battery_percentage" in c[0][0]][0]

        self.assertEqual(battery_call[0][1], "85.57")

    @patch('powerwall_service.mqtt_publisher.mqtt.Client')
    def test_publish_not_connected(self, mock_client_class):
        """Test publish does nothing when not connected."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(self.config)
        publisher._connected = False

        snapshot = {"battery_percentage": 85.5}
        publisher.publish(snapshot)

        # No publishes should occur
        mock_client.publish.assert_not_called()

    @patch('powerwall_service.mqtt_publisher.mqtt.Client')
    def test_close(self, mock_client_class):
        """Test MQTT client cleanup."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(self.config)
        publisher.close()

        # Verify cleanup
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()
        self.assertFalse(publisher.connected)


if __name__ == '__main__':
    unittest.main()
