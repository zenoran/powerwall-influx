"""Shared test fixtures and configuration."""

import pytest
from powerwall_service.config import ServiceConfig


@pytest.fixture
def test_config(**kwargs):
    """Create a ServiceConfig with sensible defaults for testing.
    
    Args:
        **kwargs: Override any config parameters
        
    Returns:
        ServiceConfig instance with test defaults
    """
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
