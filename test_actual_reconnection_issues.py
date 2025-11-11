"""Tests for ACTUAL reconnection issues that prevent service recovery.

These tests focus on the real problems:
1. Stale connection objects not being recreated
2. is_connected() returning stale status
3. WiFi reconnection not triggering
4. Session state not being reset

NOT testing backoff logic - that's just rate limiting, not the core issue.
"""

import time
import unittest
from unittest.mock import Mock, patch, MagicMock
from powerwall_service.config import ServiceConfig


def create_test_config(**kwargs):
    """Helper to create ServiceConfig with defaults for testing."""
    defaults = {
        'host': '192.168.91.1',
        'gateway_password': 'test',
        'influx_url': 'http://localhost:8086',
        'influx_token': 'test',
        'influx_org': 'test',
        'influx_bucket': 'test',
        'measurement': 'powerwall',
        'influx_timeout': 10.0,
        'influx_verify_tls': False,
        'poll_interval': 5.0,
        'timezone_name': 'UTC',
        'cache_expire': 5,
        'request_timeout': 10,
        'wifi_ssid': 'TeslaPW_FFFFPE',
        'wifi_password': 'test123',
        'wifi_interface': None,
        'connect_wifi': True,
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


class TestStaleConnectionRecreation(unittest.TestCase):
    """Test that stale Powerwall connection objects are properly recreated."""

    def setUp(self):
        self.config = create_test_config()

    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_connection_object_recreated_after_failure(self, mock_powerwall_class):
        """CRITICAL: Verify we create a NEW Powerwall object after connection dies.
        
        This is the ROOT CAUSE of the 12-hour outage:
        - Initial connection succeeds
        - Connection dies (network issue, timeout, etc.)
        - is_connected() returns True (stale state)
        - We never create a new Powerwall() object
        - Service stuck forever with dead connection
        """
        from powerwall_service.clients import PowerwallPoller
        
        # First connection succeeds
        mock_pw_instance1 = MagicMock()
        mock_pw_instance1.is_connected.return_value = True
        mock_pw_instance1.power.return_value = {'battery': 1000}
        
        # Second instance for reconnection
        mock_pw_instance2 = MagicMock()
        mock_pw_instance2.is_connected.return_value = True
        mock_pw_instance2.power.return_value = {'battery': 2000}
        
        mock_powerwall_class.side_effect = [mock_pw_instance1, mock_pw_instance2]
        
        poller = PowerwallPoller(self.config)
        
        try:
            # First fetch succeeds
            snapshot1 = poller.fetch_snapshot()
            power1 = snapshot1.get('power', {})  # type: ignore
            self.assertEqual(power1.get('battery'), 1000)  # type: ignore
            self.assertEqual(mock_powerwall_class.call_count, 1, 
                           "Should create Powerwall object once")
            
            # Simulate connection death - is_connected() now returns False
            mock_pw_instance1.is_connected.return_value = False
            
            # Next fetch should detect dead connection and CREATE NEW OBJECT
            snapshot2 = poller.fetch_snapshot()
            power2 = snapshot2.get('power', {})  # type: ignore
            self.assertEqual(power2.get('battery'), 2000,  # type: ignore
                           "Should get data from NEW Powerwall object")
            self.assertEqual(mock_powerwall_class.call_count, 2,
                           "Should create NEW Powerwall object when connection dies")
            
        finally:
            poller.close()

    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_is_connected_stale_status_detected(self, mock_powerwall_class):
        """Verify we handle cases where is_connected() reports stale True status.
        
        pypowerwall's is_connected() might cache connection state and not
        detect when the underlying TCP connection has died. We need to
        handle actual connection failures even when is_connected() says True.
        """
        from powerwall_service.clients import PowerwallPoller, PowerwallUnavailableError
        
        mock_pw = MagicMock()
        # is_connected() lies - says True but connection is actually dead
        mock_pw.is_connected.return_value = True  
        # Actual API call fails with connection error
        mock_pw.power.side_effect = ConnectionError("Connection reset by peer")
        
        mock_powerwall_class.return_value = mock_pw
        
        poller = PowerwallPoller(self.config)
        
        try:
            # Should detect connection failure despite is_connected() = True
            with self.assertRaises(PowerwallUnavailableError) as ctx:
                poller.fetch_snapshot()
            
            self.assertIn("Unable to retrieve power metrics", str(ctx.exception),
                        "Should raise PowerwallUnavailableError for connection errors")
            
        finally:
            poller.close()

    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_force_reconnect_destroys_old_object(self, mock_powerwall_class):
        """Verify force_reconnect actually creates a fresh Powerwall object."""
        from powerwall_service.clients import PowerwallPoller
        
        mock_pw1 = MagicMock()
        mock_pw1.is_connected.return_value = True
        mock_pw1.client = MagicMock()
        mock_pw1.client.close_session = Mock()  # Track close calls
        
        mock_pw2 = MagicMock()
        mock_pw2.is_connected.return_value = True
        
        mock_powerwall_class.side_effect = [mock_pw1, mock_pw2]
        
        poller = PowerwallPoller(self.config)
        
        try:
            # First establish a connection
            poller._ensure_connection()
            self.assertEqual(mock_powerwall_class.call_count, 1)
            self.assertIs(poller._powerwall, mock_pw1)
            
            # Now force reconnection
            poller._ensure_connection(force_reconnect=True)
            
            # Verify old object's session was closed
            mock_pw1.client.close_session.assert_called_once()
            
            # Verify new object was created
            self.assertEqual(mock_powerwall_class.call_count, 2,
                           "Should create new Powerwall object on force reconnect")
            self.assertIs(poller._powerwall, mock_pw2,
                        "Should use NEW Powerwall instance")
            
        finally:
            poller.close()


class TestWiFiReconnectionTriggers(unittest.TestCase):
    """Test that WiFi reconnection actually triggers when it should."""

    def setUp(self):
        self.config = create_test_config()

    @patch('powerwall_service.service.maybe_connect_wifi')
    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_wifi_runs_on_first_failure(self, mock_powerwall_class, mock_wifi):
        """CRITICAL: WiFi reconnection MUST run on first failure.
        
        Production logs showed ZERO WiFi attempts during 12+ hour outage.
        This test verifies WiFi runs immediately when connection fails.
        """
        from powerwall_service.service import PowerwallService
        
        mock_powerwall_class.side_effect = ConnectionError("Connection timed out")
        
        service = PowerwallService(self.config)
        
        try:
            # First poll failure should trigger WiFi
            service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
            
            self.assertGreater(mock_wifi.call_count, 0,
                             "WiFi reconnection MUST be attempted on first failure")
            
        finally:
            service._poller.close()

    @patch('powerwall_service.service.maybe_connect_wifi')
    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_wifi_success_allows_immediate_retry(self, mock_powerwall_class, mock_wifi):
        """After WiFi reconnects, service should immediately retry Powerwall connection."""
        from powerwall_service.service import PowerwallService
        
        # First attempt fails, second succeeds (after WiFi reconnect)
        mock_pw = MagicMock()
        mock_pw.power.return_value = {'battery': 1000}
        mock_pw.is_connected.return_value = True
        
        call_count = [0]
        def connection_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("First attempt fails")
            return mock_pw
        
        mock_powerwall_class.side_effect = connection_side_effect
        
        service = PowerwallService(self.config)
        
        try:
            # First poll fails and triggers WiFi
            # Bypass WiFi throttle
            service._last_wifi_attempt = time.monotonic() - 301.0
            service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
            
            # WiFi ran
            self.assertGreater(mock_wifi.call_count, 0)
            
            # Next poll should be allowed immediately (no backoff)
            # because WiFi reset the failure counter
            service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
            
            # Should have tried connection twice
            self.assertEqual(call_count[0], 2,
                           "Should retry connection after WiFi reconnect")
            
        finally:
            service._poller.close()


class TestSessionStateReset(unittest.TestCase):
    """Test that connection session state is properly reset on reconnection."""

    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_close_cleans_up_powerwall_object(self, mock_powerwall_class):
        """Verify close() actually destroys the Powerwall object."""
        from powerwall_service.clients import PowerwallPoller
        
        mock_pw = MagicMock()
        mock_pw.is_connected.return_value = True
        mock_pw.client = MagicMock()
        mock_pw.client.close_session = Mock()
        
        mock_powerwall_class.return_value = mock_pw
        
        poller = PowerwallPoller(create_test_config())
        
        # Establish connection
        poller._ensure_connection()
        self.assertIsNotNone(poller._powerwall)
        
        # Close should destroy it
        poller.close()
        self.assertIsNone(poller._powerwall, 
                         "close() should set _powerwall to None")
        mock_pw.client.close_session.assert_called_once()

    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_reconnection_after_close_creates_fresh_object(self, mock_powerwall_class):
        """After close(), next connection should create completely fresh object."""
        from powerwall_service.clients import PowerwallPoller
        
        mock_pw1 = MagicMock()
        mock_pw1.is_connected.return_value = True
        
        mock_pw2 = MagicMock()
        mock_pw2.is_connected.return_value = True
        
        mock_powerwall_class.side_effect = [mock_pw1, mock_pw2]
        
        poller = PowerwallPoller(create_test_config())
        
        try:
            # First connection
            poller._ensure_connection()
            self.assertIs(poller._powerwall, mock_pw1)
            
            # Close it
            poller.close()
            
            # Reconnect - should be completely fresh object
            poller._ensure_connection()
            self.assertIs(poller._powerwall, mock_pw2,
                        "Should create NEW Powerwall object after close()")
            self.assertIsNot(poller._powerwall, mock_pw1,
                           "Should NOT reuse old Powerwall object")
            
        finally:
            poller.close()


if __name__ == '__main__':
    unittest.main()
