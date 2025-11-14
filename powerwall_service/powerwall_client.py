"""Powerwall client for polling metrics and managing connections."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

import pypowerwall
from requests import exceptions as requests_exceptions
from urllib3 import exceptions as urllib3_exceptions

from .config import ServiceConfig

LOGGER = logging.getLogger("powerwall_service.powerwall_client")


class PowerwallUnavailableError(RuntimeError):
    """Raised when the Powerwall gateway cannot be reached."""


def _check_exception_chain(exc: BaseException, condition: Callable[[BaseException], bool]) -> bool:
    """Walk the exception chain and check if any exception matches the condition.
    
    This helper reduces duplication between _is_connection_error() and _is_auth_error()
    by providing a generic way to recursively check exception chains.
    
    Args:
        exc: The exception to check
        condition: A function that returns True if an exception matches
        
    Returns:
        True if exc or any exception in its chain matches the condition
    """
    if condition(exc):
        return True
    
    # Check __cause__ chain
    cause = getattr(exc, "__cause__", None)
    if cause and cause is not exc and _check_exception_chain(cause, condition):
        return True
    
    # Check __context__ chain
    context = getattr(exc, "__context__", None)
    if context and context is not exc and _check_exception_chain(context, condition):
        return True
    
    return False


def _is_connection_error(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` (or its causes) represent a network failure."""
    def is_network_exception(e: BaseException) -> bool:
        return isinstance(
            e,
            (
                requests_exceptions.RequestException,
                urllib3_exceptions.HTTPError,
                ConnectionError,
                OSError,
            ),
        )
    
    return _check_exception_chain(exc, is_network_exception)


def _is_auth_error(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` represents an authentication failure (403/401)."""
    def is_auth_exception(e: BaseException) -> bool:
        # Check if it's an HTTP error with 403 or 401 status
        if isinstance(e, requests_exceptions.HTTPError):
            if hasattr(e, 'response') and e.response is not None:
                return e.response.status_code in (401, 403)
        
        # Check exception message for authentication indicators
        exc_str = str(e).lower()
        auth_indicators = ['403', '401', 'forbidden', 'unauthorized', 'authentication']
        return any(indicator in exc_str for indicator in auth_indicators)
    
    return _check_exception_chain(exc, is_auth_exception)


class PowerwallPoller:
    """Thin wrapper around :mod:`pypowerwall` with connection caching."""

    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._powerwall: Optional[pypowerwall.Powerwall] = None
        self._consecutive_auth_failures = 0
        self._max_auth_failures = 3  # Force full reconnect after this many 403s
        self._consecutive_connection_failures = 0
        self._last_connection_attempt = 0.0  # Timestamp of last connection attempt
        self._backoff_base = 30.0  # Base backoff time in seconds (30s)
        self._backoff_max = 300.0  # Maximum backoff time (5 minutes)
        self._client_error_count = 0  # Track errors on current client instance
        self._max_client_errors = 5  # Force new client after this many errors on same instance

    def close(self) -> None:
        """Close the Powerwall connection and reset state completely."""
        if self._powerwall and getattr(self._powerwall, "client", None):
            try:
                self._powerwall.client.close_session()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                LOGGER.debug("Failed to close Powerwall session: %s", exc)
        
        # CRITICAL: Always null out the client to force fresh object creation
        self._powerwall = None
        self._consecutive_auth_failures = 0
        self._client_error_count = 0  # Reset error count when we destroy the client
        # Don't reset connection failures here - we want to track them across close/reopen

    def _ensure_connection(self, force_reconnect: bool = False) -> None:
        """Ensure we have a valid connection to the Powerwall.
        
        Args:
            force_reconnect: If True, close existing connection and create a new one
        """
        if force_reconnect:
            LOGGER.info("Forcing full reconnection to Powerwall (auth failures: %d)", 
                       self._consecutive_auth_failures)
            self.close()
        elif self._powerwall:
            # Even if we have a client, it might be in a bad state
            # Don't trust is_connected() - it can lie when session is stale
            # Only skip reconnection if we successfully used it very recently
            return
        
        # Force clean slate - always null out before recreating
        if self._powerwall is not None:
            self.close()
        
        # Implement exponential backoff instead of hard circuit breaker
        # This allows automatic recovery but with increasing delays
        if self._consecutive_connection_failures > 0:
            now = time.monotonic()
            time_since_last_attempt = now - self._last_connection_attempt
            
            # Calculate exponential backoff: base * 2^(failures-1), capped at max
            backoff_time = min(
                self._backoff_base * (2 ** (self._consecutive_connection_failures - 1)),
                self._backoff_max
            )
            
            if time_since_last_attempt < backoff_time:
                remaining = backoff_time - time_since_last_attempt
                LOGGER.info(
                    "Exponential backoff active after %d failures: waiting %.0fs before retry (%.0fs remaining)",
                    self._consecutive_connection_failures,
                    backoff_time,
                    remaining
                )
                raise PowerwallUnavailableError(
                    f"Backoff active after {self._consecutive_connection_failures} failures. "
                    f"Will retry in {remaining:.0f}s. Powerwall at {self._config.host} may be offline."
                )
            else:
                LOGGER.info(
                    "Backoff period expired after %d failures (%.0fs elapsed), attempting reconnection",
                    self._consecutive_connection_failures,
                    time_since_last_attempt
                )
            
        gw_pwd = (
            self._config.gateway_password
            or self._config.wifi_password
            or self._config.customer_password
        )
        email = self._config.customer_email or "nobody@nowhere.com"
        password = self._config.customer_password or ""
        
        # Determine connection mode based on configuration
        # If we have gw_pwd, use local mode with TEDAPI
        # Don't use auto_select to avoid trying cloud/fleetapi modes we haven't configured
        use_local_mode = bool(self._config.host)
        
        LOGGER.debug(
            "Connecting to Powerwall host=%s email=%s mode=%s (attempt after %d failures)",
            self._config.host,
            email,
            "local" if use_local_mode else "cloud",
            self._consecutive_connection_failures,
        )
        try:
            # Disable auto_select and retry_modes to fail fast and avoid trying unconfigured modes
            self._powerwall = pypowerwall.Powerwall(
                host=self._config.host,
                password=password,
                email=email,
                timezone=self._config.timezone_name,
                pwcacheexpire=self._config.cache_expire,
                timeout=self._config.request_timeout,
                gw_pwd=gw_pwd,
                cloudmode=not use_local_mode,  # Only use cloud if no host specified
                auto_select=False,  # Changed: Don't auto-select modes, be explicit
                retry_modes=False,  # Changed: Don't let pypowerwall retry - we handle it
            )
            # Success! Reset both failure counters
            if self._consecutive_connection_failures > 0 or self._consecutive_auth_failures > 0:
                LOGGER.info(
                    "Successfully connected to Powerwall after %d connection failures, %d auth failures",
                    self._consecutive_connection_failures,
                    self._consecutive_auth_failures
                )
            self._consecutive_auth_failures = 0
            self._consecutive_connection_failures = 0
        except Exception as exc:
            self._consecutive_connection_failures += 1
            self._last_connection_attempt = time.monotonic()  # CRITICAL: Set timestamp when connection fails
            # Calculate next backoff time for logging
            next_backoff = min(
                self._backoff_base * (2 ** (self._consecutive_connection_failures - 1)),
                self._backoff_max
            )
            LOGGER.warning(
                "Connection attempt failed (failure %d, next retry in %.0fs): %s",
                self._consecutive_connection_failures,
                next_backoff,
                exc
            )
            if _is_connection_error(exc):
                raise PowerwallUnavailableError(
                    f"Failed to connect to Powerwall gateway at {self._config.host}"
                ) from exc
            raise

    def _fetch_with_auth_retry(
        self, 
        fetch_func: Callable[[], Any], 
        data_type: str
    ) -> Any:
        """Fetch data with authentication error handling and retry logic.
        
        This helper reduces duplication in fetch_snapshot() by centralizing
        the auth error handling pattern used for power, status, and vitals.
        
        Args:
            fetch_func: Function to call to fetch the data
            data_type: Description of data being fetched (for logging)
            
        Returns:
            The fetched data, or None if a recoverable error occurs
            
        Raises:
            PowerwallUnavailableError: If auth fails repeatedly or connection fails
        """
        try:
            return fetch_func()
        except Exception as exc:
            if _is_auth_error(exc):
                self._consecutive_auth_failures += 1
                LOGGER.warning(
                    "Authentication error fetching %s (failure %d/%d): %s",
                    data_type,
                    self._consecutive_auth_failures,
                    self._max_auth_failures,
                    exc,
                )
                if self._consecutive_auth_failures >= self._max_auth_failures:
                    raise PowerwallUnavailableError(
                        f"Authentication failed {self._consecutive_auth_failures} times, "
                        f"unable to authenticate with Powerwall at {self._config.host}"
                    ) from exc
                return None
            elif _is_connection_error(exc):
                raise PowerwallUnavailableError(
                    f"Unable to retrieve {data_type} from Powerwall at {self._config.host}"
                ) from exc
            else:
                LOGGER.debug("Failed to fetch %s: %s", data_type, exc)
                return None

    def _fetch_power_metrics(self) -> Optional[Dict[str, float]]:
        """Fetch power metrics with auth retry logic."""
        assert self._powerwall is not None
        power_values = self._fetch_with_auth_retry(
            self._powerwall.power,
            "power metrics"
        )
        
        # If auth failed once, IMMEDIATELY force full reconnect with new client object
        if power_values is None and self._consecutive_auth_failures > 0:
            if self._consecutive_auth_failures < self._max_auth_failures:
                LOGGER.info("Auth error detected - forcing full client recreation")
                # Close and null out client to force brand new object
                self.close()
                # This will create a completely new pypowerwall.Powerwall instance
                self._ensure_connection(force_reconnect=False)
                # Try again with fresh client
                if self._powerwall:
                    power_values = self._fetch_with_auth_retry(
                        self._powerwall.power,
                        "power metrics (after reconnect)"
                    )
        
        return power_values

    def _fetch_status_data(self) -> Optional[Dict[str, Any]]:
        """Fetch status data with auth retry logic."""
        assert self._powerwall is not None
        return self._fetch_with_auth_retry(
            self._powerwall.status,
            "status"
        )

    def _fetch_vitals_data(self) -> Optional[Dict[str, Any]]:
        """Fetch vitals data with auth retry logic."""
        assert self._powerwall is not None
        return self._fetch_with_auth_retry(
            self._powerwall.vitals,
            "vitals"
        )

    def _build_snapshot(
        self,
        power_values: Optional[Dict[str, float]],
        status: Optional[Dict[str, Any]],
        vitals: Optional[Dict[str, Any]]
    ) -> Dict[str, object]:
        """Build snapshot dictionary from fetched data.
        
        Args:
            power_values: Power metrics from powerwall.power()
            status: Status dict from powerwall.status()
            vitals: Vitals dict from powerwall.vitals()
            
        Returns:
            Complete snapshot dictionary
        """
        from .metrics import _extract_float
        
        assert self._powerwall is not None
        powerwall = self._powerwall
        
        # Build basic snapshot
        snapshot = {
            "timestamp": datetime.now(timezone.utc),
            "site_name": self._safe_call(powerwall.site_name),
            "firmware": self._safe_call(powerwall.version),
            "din": self._safe_call(powerwall.din),
            "battery_percentage": self._safe_call(powerwall.level),
            "power": power_values,
            "grid_status": self._safe_call(
                lambda: powerwall.grid_status("string") if hasattr(powerwall, "grid_status") else None
            ),
        }

        # Process status data
        if isinstance(status, dict):
            alerts = status.get("control", {}).get("alerts", {}).get("active", [])
            snapshot["alerts"] = alerts
            system_status = status.get("control", {}).get("systemStatus", {})
            if system_status:
                snapshot["system_status"] = system_status
        else:
            snapshot["alerts"] = []

        # Process vitals data
        if isinstance(vitals, dict):
            snapshot["vitals"] = vitals
            snapshot["battery_nominal_energy_remaining"] = _extract_float(
                vitals,
                ["TEPOD--%s" % snapshot["din"], "POD_nom_energy_remaining"],
            )
            snapshot["battery_nominal_full_energy"] = _extract_float(
                vitals,
                ["TEPOD--%s" % snapshot["din"], "POD_nom_full_pack_energy"],
            )
        
        return snapshot

    def fetch_snapshot(self) -> Dict[str, object]:
        """Fetch a complete snapshot of Powerwall metrics.
        
        This method implements robust error handling:
        - Detects network failures and raises PowerwallUnavailableError
        - Detects authentication failures and forces reconnection after threshold
        - Automatically retries with full session recreation on auth errors
        - Forces new client instance if same client produces too many errors
        
        Returns:
            Dictionary containing all Powerwall metrics
            
        Raises:
            PowerwallUnavailableError: When the Powerwall is unreachable or auth fails repeatedly
        """
        # CRITICAL: If this client instance has produced too many errors, kill it
        if self._client_error_count >= self._max_client_errors:
            LOGGER.warning(
                "Client instance has %d errors - forcing complete recreation",
                self._client_error_count
            )
            self.close()  # This will null out client and reset error count
        
        # Check if we need to force reconnect due to repeated auth failures
        force_reconnect = self._consecutive_auth_failures >= self._max_auth_failures
        
        try:
            self._ensure_connection(force_reconnect=force_reconnect)
            assert self._powerwall is not None

            # Fetch all data using helper methods
            power_values = self._fetch_power_metrics()
            status = self._fetch_status_data()
            vitals = self._fetch_vitals_data()

            # Build and return snapshot
            snapshot = self._build_snapshot(power_values, status, vitals)
            
            # Success! Reset ALL counters
            if self._consecutive_auth_failures > 0:
                LOGGER.info("Successfully recovered from previous auth failures")
            self._consecutive_auth_failures = 0
            self._client_error_count = 0  # Reset on success
            
            return snapshot
            
        except PowerwallUnavailableError:
            # Track that this client instance produced an error
            self._client_error_count += 1
            # Already a PowerwallUnavailableError, just close and re-raise
            self.close()
            raise
        except Exception as exc:
            # Track that this client instance produced an error
            self._client_error_count += 1
            # Unexpected error - check if it's connection-related
            if _is_connection_error(exc):
                self.close()
                raise PowerwallUnavailableError(
                    f"Unable to communicate with Powerwall gateway at {self._config.host}"
                ) from exc
            elif _is_auth_error(exc):
                self._consecutive_auth_failures += 1
                self.close()
                raise PowerwallUnavailableError(
                    f"Authentication failed with Powerwall at {self._config.host}"
                ) from exc
            raise

    def _safe_call(self, func, default=None):
        """Safely call a function, returning default on any exception."""
        try:
            return func()
        except Exception as exc:
            LOGGER.debug("Safe call failed: %s", exc)
            return default
