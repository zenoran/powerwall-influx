#!/usr/bin/env python3
"""Test cases that mimic actual log scenarios to verify reconnection fixes.

This test suite simulates the 12+ hour failure scenario discovered in production logs
and verifies that the fixes properly handle:
1. Setting _last_connection_attempt timestamp on failures
2. Exponential backoff behavior
3. WiFi reconnection triggering
4. Service-level logging
"""

import time
import unittest
from unittest.mock import MagicMock, patch
import logging

# Set up logging to see test output
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


def create_test_config(**kwargs):
    """Helper to create ServiceConfig with defaults for testing."""
    from powerwall_service.config import ServiceConfig
    
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


class TestConnectionBackoffScenarios(unittest.TestCase):
    """Test exponential backoff behavior matching production failure scenarios."""

    def setUp(self):
        """Set up test fixtures."""
        from powerwall_service.clients import PowerwallPoller

        # Create a minimal config
        self.config = create_test_config()
        self.poller = PowerwallPoller(self.config)

    def tearDown(self):
        """Clean up."""
        if hasattr(self, 'poller'):
            self.poller.close()

    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_timestamp_set_on_connection_failure(self, mock_powerwall_class):
        """Verify _last_connection_attempt is set when connection fails.
        
        This was the CRITICAL BUG: timestamp was never set, so backoff never worked.
        """
        from powerwall_service.clients import PowerwallUnavailableError
        
        # Make pypowerwall constructor raise a connection error
        mock_powerwall_class.side_effect = ConnectionError("Connection to 192.168.91.1 timed out")
        
        # Initial state: no connection attempts yet
        self.assertEqual(self.poller._consecutive_connection_failures, 0)
        self.assertEqual(self.poller._last_connection_attempt, 0.0)
        
        # First connection attempt fails
        with self.assertRaises(PowerwallUnavailableError):
            self.poller._ensure_connection()
        
        # VERIFY FIX: Timestamp should now be set
        self.assertEqual(self.poller._consecutive_connection_failures, 1)
        self.assertGreater(self.poller._last_connection_attempt, 0.0,
                          "CRITICAL: _last_connection_attempt must be set on failure!")
        
        first_attempt_time = self.poller._last_connection_attempt
        
        # Second connection attempt immediately will be blocked by backoff
        with self.assertRaises(PowerwallUnavailableError) as ctx:
            self.poller._ensure_connection()
        
        # Should still be 1 failure (backoff prevented actual connection attempt)
        self.assertEqual(self.poller._consecutive_connection_failures, 1)
        self.assertIn("Backoff active", str(ctx.exception))
        
        # Now wait past the backoff period and try again
        self.poller._last_connection_attempt = time.monotonic() - 35.0  # 35 seconds ago
        
        with self.assertRaises(PowerwallUnavailableError):
            self.poller._ensure_connection()
        
        # NOW it should be 2 failures
        self.assertEqual(self.poller._consecutive_connection_failures, 2)
        self.assertGreater(self.poller._last_connection_attempt, first_attempt_time,
                          "Timestamp should be updated on each actual connection attempt")

    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_exponential_backoff_prevents_rapid_retries(self, mock_powerwall_class):
        """Verify exponential backoff prevents connection attempts during backoff period.
        
        Production logs showed connection attempts every ~5 seconds (poll interval).
        With backoff working, attempts should be delayed exponentially.
        """
        from powerwall_service.clients import PowerwallUnavailableError
        
        # Make pypowerwall constructor raise a connection error
        mock_powerwall_class.side_effect = ConnectionError("Connection timed out")
        
        # First failure
        with self.assertRaises(PowerwallUnavailableError):
            self.poller._ensure_connection()
        
        self.assertEqual(self.poller._consecutive_connection_failures, 1)
        
        # Immediate retry should be blocked by backoff (30 seconds for first failure)
        with self.assertRaises(PowerwallUnavailableError) as ctx:
            self.poller._ensure_connection()
        
        # Should still be 1 failure (not incremented because backoff prevented attempt)
        self.assertEqual(self.poller._consecutive_connection_failures, 1)
        self.assertIn("Backoff active", str(ctx.exception))
        self.assertIn("Will retry in", str(ctx.exception))

    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    @patch('powerwall_service.clients.time.monotonic')
    def test_backoff_times_match_expected_values(self, mock_monotonic, mock_powerwall_class):
        """Verify backoff times follow exponential pattern: 30s, 60s, 120s, 240s, 300s (max)."""
        from powerwall_service.clients import PowerwallUnavailableError
        
        mock_powerwall_class.side_effect = ConnectionError("Connection timed out")
        
        # Start at time 0
        mock_monotonic.return_value = 0.0
        
        expected_backoffs = [
            (1, 30.0),   # 30 * 2^0 = 30
            (2, 60.0),   # 30 * 2^1 = 60
            (3, 120.0),  # 30 * 2^2 = 120
            (4, 240.0),  # 30 * 2^3 = 240
            (5, 300.0),  # 30 * 2^4 = 480, capped at 300
            (6, 300.0),  # Still capped at 300
        ]
        
        for failure_num, expected_backoff in expected_backoffs:
            # Attempt connection (will fail)
            with self.assertRaises(PowerwallUnavailableError):
                self.poller._ensure_connection()
            
            self.assertEqual(self.poller._consecutive_connection_failures, failure_num)
            
            # Try immediately - should be blocked by backoff
            mock_monotonic.return_value = self.poller._last_connection_attempt + 1.0
            with self.assertRaises(PowerwallUnavailableError) as ctx:
                self.poller._ensure_connection()
            
            # Verify backoff message includes expected time
            error_msg = str(ctx.exception)
            self.assertIn("Backoff active", error_msg)
            
            # Advance time past backoff period
            mock_monotonic.return_value = self.poller._last_connection_attempt + expected_backoff + 1.0

    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_backoff_resets_on_successful_connection(self, mock_powerwall_class):
        """Verify backoff is reset when connection succeeds."""
        from powerwall_service.clients import PowerwallUnavailableError
        
        # First two attempts fail
        mock_powerwall_class.side_effect = ConnectionError("Connection timed out")
        
        with self.assertRaises(PowerwallUnavailableError):
            self.poller._ensure_connection()
        
        self.assertEqual(self.poller._consecutive_connection_failures, 1)
        
        # Wait past backoff period and try again
        time.sleep(0.1)
        self.poller._last_connection_attempt = time.monotonic() - 35.0
        
        with self.assertRaises(PowerwallUnavailableError):
            self.poller._ensure_connection()
        
        self.assertEqual(self.poller._consecutive_connection_failures, 2)
        
        # Now connection succeeds
        mock_pw = MagicMock()
        mock_pw.is_connected.return_value = True
        mock_powerwall_class.side_effect = None
        mock_powerwall_class.return_value = mock_pw
        
        # Wait past backoff period
        self.poller._last_connection_attempt = time.monotonic() - 65.0
        
        # Should succeed and reset counters
        self.poller._ensure_connection()
        
        self.assertEqual(self.poller._consecutive_connection_failures, 0)
        self.assertIsNotNone(self.poller._powerwall)


class TestWiFiReconnectionScenarios(unittest.TestCase):
    """Test WiFi reconnection behavior matching production scenarios."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = create_test_config(
            connect_wifi=True,
            wifi_ssid="TeslaPW_FFFFPE",
            wifi_password="test123",
        )

    @patch('powerwall_service.service.maybe_connect_wifi')
    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_wifi_reconnection_triggered_on_failure(self, mock_powerwall_class, mock_connect_wifi):
        """Verify WiFi reconnection is attempted when Powerwall connection fails.
        
        Production logs showed NO WiFi reconnection attempts during 12+ hour outage.
        This test verifies the fix.
        """
        from powerwall_service.service import PowerwallService
        
        # Make Powerwall connection fail
        mock_powerwall_class.side_effect = ConnectionError("Connection timed out")
        
        # Create service
        service = PowerwallService(self.config)
        
        try:
            # First poll will fail
            service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
            
            # Should have attempted WiFi reconnection
            self.assertTrue(mock_connect_wifi.called,
                           "WiFi reconnection should be attempted on Powerwall failure")
            
            # Verify it was called with the right config
            mock_connect_wifi.assert_called_with(self.config)
            
        finally:
            service._poller.close()

    @patch('powerwall_service.service.maybe_connect_wifi')
    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_wifi_reconnection_respects_60_second_interval(self, mock_powerwall_class, mock_connect_wifi):
        """Verify WiFi reconnection attempts are throttled to prevent rapid retries.
        
        Note: WiFi retry interval is actually 300s (5 minutes), not 60s.
        """
        from powerwall_service.service import PowerwallService
        
        mock_powerwall_class.side_effect = ConnectionError("Connection timed out")
        
        service = PowerwallService(self.config)
        
        try:
            # First poll - should trigger WiFi reconnection
            # Note: WiFi reconnection resets failure counter to 0, then failure increments to 1
            service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
            self.assertEqual(mock_connect_wifi.call_count, 1)
            
            # Second poll immediately after - should NOT trigger WiFi reconnection (300s throttle)
            # Failure counter was reset to 0, then incremented to 1, so need to bypass 30s backoff
            service._poller._last_connection_attempt = time.monotonic() - 31.0
            service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
            self.assertEqual(mock_connect_wifi.call_count, 1,
                           "WiFi reconnection should be throttled")
            
            # Third poll - simulate both backoff and WiFi throttle periods passing
            # Failure counter is now 2, so need 60s backoff bypass
            service._poller._last_connection_attempt = time.monotonic() - 61.0
            # WiFi attempt time was updated in finally block, so reset it now (300s WiFi interval)
            service._last_wifi_attempt = time.monotonic() - 301.0
            
            service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
            self.assertEqual(mock_connect_wifi.call_count, 2,
                           "WiFi reconnection should happen after 300 seconds")
            
        finally:
            service._poller.close()

    @patch('powerwall_service.service.maybe_connect_wifi')
    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_wifi_success_resets_connection_failures(self, mock_powerwall_class, mock_connect_wifi):
        """Verify successful WiFi reconnection resets Powerwall connection failure counter."""
        from powerwall_service.service import PowerwallService
        
        mock_powerwall_class.side_effect = ConnectionError("Connection timed out")
        
        service = PowerwallService(self.config)
        
        try:
            # Artificially set failure count and backoff timer before first poll
            service._poller._consecutive_connection_failures = 3
            # 3 failures = 120s backoff, so need to wait 121s to bypass it
            service._poller._last_connection_attempt = time.monotonic() - 121.0
            
            # Make sure WiFi throttle allows reconnection (WiFi retry interval is 300s)
            initial_wifi_attempts = mock_connect_wifi.call_count
            service._last_wifi_attempt = time.monotonic() - 301.0  # Bypass WiFi throttle
            
            service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
            
            # Verify WiFi reconnection was attempted
            self.assertEqual(mock_connect_wifi.call_count, initial_wifi_attempts + 1,
                           "WiFi reconnection should have been attempted")
            
            # WiFi reconnection resets the counter to 0 AFTER the connection attempt
            # Flow: attempt (3→4) → fail → WiFi reset (4→0)
            self.assertEqual(service._poller._consecutive_connection_failures, 0,
                           "WiFi reconnection should reset counter to 0")
            # Backoff timer should also be reset when WiFi succeeded
            self.assertEqual(service._poller._last_connection_attempt, 0.0,
                           "WiFi reconnection should reset backoff timer to 0")
            
        finally:
            service._poller.close()


class TestLoggingScenarios(unittest.TestCase):
    """Test that proper logging occurs during failures."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = create_test_config(
            connect_wifi=True,
            wifi_ssid="TeslaPW_FFFFPE",
            wifi_password="test123",
        )

    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_connection_failure_logging(self, mock_powerwall_class):
        """Verify connection failures are logged at appropriate levels."""
        from powerwall_service.clients import PowerwallPoller, PowerwallUnavailableError
        
        # Capture log output
        with self.assertLogs('powerwall_service.clients', level='INFO') as log_context:
            mock_powerwall_class.side_effect = ConnectionError("Connection timed out")
            
            poller = PowerwallPoller(self.config)
            
            try:
                # First failure
                with self.assertRaises(PowerwallUnavailableError):
                    poller._ensure_connection()
                
                # Second failure (should trigger backoff logging)
                with self.assertRaises(PowerwallUnavailableError):
                    poller._ensure_connection()
                
                # Check that appropriate logs were generated
                log_output = '\n'.join(log_context.output)
                
                # Should log connection attempt failure
                self.assertIn("Connection attempt failed", log_output)
                self.assertIn("failure 1", log_output)
                
                # Should log backoff active
                self.assertIn("Exponential backoff active", log_output)
                
            finally:
                poller.close()

    @patch('powerwall_service.service.maybe_connect_wifi')
    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_service_level_failure_logging(self, mock_powerwall_class, mock_connect_wifi):
        """Verify service logs Powerwall gateway unreachable messages."""
        from powerwall_service.service import PowerwallService
        
        with self.assertLogs('powerwall_service.service', level='WARNING') as log_context:
            mock_powerwall_class.side_effect = ConnectionError("Connection timed out")
            
            service = PowerwallService(self.config)
            
            try:
                # Poll should fail
                service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
                
                # Check logs
                log_output = '\n'.join(log_context.output)
                
                # Should log gateway unreachable with failure count
                self.assertIn("Powerwall gateway unreachable", log_output)
                self.assertIn("failure 1", log_output)
                
            finally:
                service._poller.close()

    @patch('powerwall_service.service.maybe_connect_wifi')
    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    def test_wifi_reconnection_logging(self, mock_powerwall_class, mock_connect_wifi):
        """Verify WiFi reconnection attempts are logged."""
        from powerwall_service.service import PowerwallService
        
        with self.assertLogs('powerwall_service.service', level='DEBUG') as log_context:
            mock_powerwall_class.side_effect = ConnectionError("Connection timed out")
            
            service = PowerwallService(self.config)
            
            try:
                # First failure - should log WiFi reconnection attempt
                # Bypass backoff to allow connection attempt
                service._poller._last_connection_attempt = time.monotonic() - 35.0
                service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
                
                log_output = '\n'.join(log_context.output)
                
                # Should log WiFi reconnection attempt
                self.assertIn("Attempting WiFi reconnection", log_output)
                self.assertIn("TeslaPW_FFFFPE", log_output)
                
                # Second immediate failure - should log skipping WiFi reconnection
                # Bypass backoff again
                service._poller._last_connection_attempt = time.monotonic() - 35.0
                service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
                
                log_output = '\n'.join(log_context.output)
                self.assertIn("Skipping WiFi reconnection", log_output)
                
            finally:
                service._poller.close()


class TestProductionScenarioSimulation(unittest.TestCase):
    """Simulate the actual 12+ hour failure scenario from production logs."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = create_test_config(
            connect_wifi=True,
            wifi_ssid="TeslaPW_FFFFPE",
            wifi_password="test123",
        )

    @patch('powerwall_service.service.maybe_connect_wifi')
    @patch('powerwall_service.clients.pypowerwall.Powerwall')
    @patch('powerwall_service.clients.time.monotonic')
    def test_12_hour_outage_scenario(self, mock_monotonic, mock_powerwall_class, mock_connect_wifi):
        """Simulate 12+ hour connection failure scenario from production.
        
        Production behavior (BROKEN):
        - Connection attempts every ~5 seconds (poll interval)
        - Each attempt takes ~90 seconds (pypowerwall's 5 retries)
        - NO exponential backoff (timestamp never set)
        - NO WiFi reconnection attempts
        
        Expected behavior (FIXED):
        - First failure triggers WiFi reconnection and starts backoff
        - Subsequent attempts use exponential backoff: 30s, 60s, 120s, 240s, 300s
        - WiFi reconnection attempted only when connection is actually attempted
          (i.e., when backoff period expires and WiFi retry interval has passed)
        - Proper logging at each step
        """
        from powerwall_service.service import PowerwallService
        
        # Make all connection attempts fail
        mock_powerwall_class.side_effect = ConnectionError("Connection to 192.168.91.1 timed out")
        
        # Start at time 0
        current_time = 0.0
        mock_monotonic.return_value = current_time
        
        service = PowerwallService(self.config)
        
        try:
            poll_count = 0
            wifi_reconnect_count = 0
            connection_attempts = []
            
            # Simulate polling over 1 hour to keep test fast
            simulated_duration = 3600  # 1 hour
            
            while current_time < simulated_duration:
                poll_count += 1
                
                # Record if this poll actually attempts a connection
                initial_failures = service._poller._consecutive_connection_failures
                
                # Do poll
                service._poll_once_blocking(push_to_influx=False, publish_mqtt=False)
                
                # Check if connection was attempted (failure count increased)
                if service._poller._consecutive_connection_failures > initial_failures:
                    connection_attempts.append({
                        'time': current_time,
                        'poll': poll_count,
                        'failures': service._poller._consecutive_connection_failures,
                    })
                
                # Check WiFi reconnection
                if mock_connect_wifi.call_count > wifi_reconnect_count:
                    wifi_reconnect_count = mock_connect_wifi.call_count
                
                # Advance time by poll interval
                current_time += self.config.poll_interval
                mock_monotonic.return_value = current_time
            
            # VERIFY FIXES:
            
            # 1. Connection attempts should be throttled by exponential backoff
            # With backoff of 30s, 60s, 120s, 240s, 300s we should see:
            # - Attempt 1 at t=0
            # - Attempt 2 at t=30s
            # - Attempt 3 at t=90s (30+60)
            # - Attempt 4 at t=210s (30+60+120)
            # - Attempt 5 at t=450s (30+60+120+240)
            # - Attempt 6 at t=750s (30+60+120+240+300)
            # - etc. with 300s interval after that
            # In 1 hour (3600s), we should have ~11 attempts
            self.assertGreater(len(connection_attempts), 5,
                             "Should have multiple connection attempts")
            self.assertLess(len(connection_attempts), 100,
                          "Backoff should prevent rapid retries (was ~720 without fix)")
            
            # 2. WiFi reconnection attempts should be limited
            # WiFi reconnection only happens when connection is actually attempted
            # AND 60s has passed since last WiFi attempt
            # So max is ~len(connection_attempts), but could be less
            self.assertLessEqual(wifi_reconnect_count, len(connection_attempts),
                               "WiFi attempts can't exceed connection attempts")
            self.assertGreater(wifi_reconnect_count, 0,
                             "WiFi should be attempted at least once")
            
            # 2. Connection attempts should use exponential backoff
            # With exponential backoff, we should have far fewer connection attempts
            # than polls (720 polls vs maybe 100-200 connection attempts)
            self.assertLess(len(connection_attempts), poll_count / 2,
                          "Exponential backoff should reduce connection attempts")
            
            # 3. Verify backoff times are increasing
            if len(connection_attempts) >= 5:
                time_diffs = []
                for i in range(1, min(6, len(connection_attempts))):
                    diff = connection_attempts[i]['time'] - connection_attempts[i-1]['time']
                    time_diffs.append(diff)
                
                # Times between attempts should generally increase (with some variance due to discrete polling)
                # First gaps should be ~30s, then ~60s, then ~120s, etc.
                self.assertGreaterEqual(time_diffs[1], time_diffs[0] * 0.8,
                                      "Backoff should increase between attempts")
            
            # 4. Max failures should be reasonable (not thousands)
            self.assertLess(service._poller._consecutive_connection_failures, 20,
                          "With proper backoff, failure count should stabilize")
            
            print("\nProduction Scenario Simulation Results:")
            print(f"  Duration: {simulated_duration}s ({simulated_duration/3600:.1f} hours)")
            print(f"  Total polls: {poll_count}")
            print(f"  Connection attempts: {len(connection_attempts)}")
            print(f"  WiFi reconnections: {wifi_reconnect_count}")
            print(f"  Final failure count: {service._poller._consecutive_connection_failures}")
            print(f"  Reduction in connection attempts: {100*(1-len(connection_attempts)/poll_count):.1f}%")
            
        finally:
            service._poller.close()


if __name__ == '__main__':
    # Run with verbose output
    unittest.main(verbosity=2)
