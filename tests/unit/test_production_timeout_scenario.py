"""
Test the exact scenario from production logs: repeated timeout errors.

This test simulates the production behavior to understand why the service
didn't recover during the 12+ hour outage.
"""

import asyncio
import unittest
from unittest.mock import Mock, MagicMock, patch
import time
from powerwall_service.config import ServiceConfig
from powerwall_service.clients import PowerwallUnavailableError


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


class TestProductionTimeoutScenario(unittest.TestCase):
    """Simulate exactly what happened in production."""

    def setUp(self):
        self.config = create_test_config()

    @patch('powerwall_service.service.maybe_connect_wifi')
    @patch('powerwall_service.powerwall_client.pypowerwall.Powerwall')
    def test_continuous_timeout_errors_like_production(self, mock_powerwall_class, mock_wifi):
        """
        Simulate production scenario:
        - Service starts fine
        - Connection drops (WiFi issue)
        - Every poll attempt times out
        - Service should call WiFi reconnection
        - Service should keep trying
        
        Production logs showed:
        - Continuous timeout errors
        - NO WiFi reconnection attempts (THIS IS THE BUG!)
        - Service never recovered
        """
        async def run_test():
            from powerwall_service.service import PowerwallService
            import socket
            
            # Simulate:
            # - First connection succeeds
            # - Subsequent calls timeout (simulating network drop)
            mock_pw = MagicMock()
            mock_pw.is_connected.return_value = True
            
            # First fetch_snapshot works
            call_count = [0]
            def power_side_effect():
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call succeeds
                    return {'battery': 100, 'load': 50, 'solar': 75, 'grid': -25}
                else:
                    # All subsequent calls timeout
                    raise socket.timeout("Timed out")
            
            mock_pw.power.side_effect = power_side_effect
            mock_pw.site_name.return_value = "Test Site"
            mock_pw.version.return_value = "1.0"
            mock_pw.din.return_value = "123"
            mock_pw.level.return_value = 100
            mock_pw.status.return_value = {}
            mock_pw.strings.return_value = []
            mock_pw.vitals.return_value = {}
            mock_pw.temps.return_value = {}
            mock_pw.alerts.return_value = []
            
            mock_powerwall_class.return_value = mock_pw
            
            service = PowerwallService(self.config)
            
            try:
                # First poll should succeed
                await service.poll_once()
                self.assertEqual(call_count[0], 1, "First poll should succeed")
                
                # Now simulate multiple failed polls like in production
                for i in range(5):  # Simulate 5 failures
                    try:
                        await service.poll_once()
                    except PowerwallUnavailableError:
                        pass  # Expected
                
                # CRITICAL CHECK: Did WiFi reconnection run?
                # Production logs showed ZERO WiFi attempts!
                print(f"\nWiFi reconnection called {mock_wifi.call_count} times")
                print(f"WiFi calls: {mock_wifi.call_args_list}")
                
                # This is the key assertion - WiFi SHOULD have been called
                self.assertGreater(mock_wifi.call_count, 0,
                                 "WiFi reconnection should have been attempted after failures!")
                
            finally:
                await service.stop()
        
        # Run the async test
        asyncio.run(run_test())


if __name__ == '__main__':
    unittest.main()
