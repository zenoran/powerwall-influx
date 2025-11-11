# Connection Error Handling Improvements

## Problem Summary

The Powerwall service was experiencing connectivity issues that manifested as:

1. **Network timeouts** when the Powerwall gateway (192.168.91.1) became unreachable
2. **403 Forbidden errors** after network restoration, indicating authentication session expiration
3. **No automatic recovery** - the service would continue polling with an expired session
4. **Excessive retries** from urllib3 (5 retries with exponential backoff) masking the actual issue
5. **Long blocking delays** during connection attempts when `retry_modes=True` caused pypowerwall to retry multiple modes

### Root Cause

When using TEDAPI mode, the pypowerwall library:
- Creates a session with Basic Authentication
- Does NOT automatically re-authenticate on 403/401 errors
- Unlike local mode which has retry logic for auth failures, TEDAPI just returns None
- With `retry_modes=True`, connection attempts could take 60+ seconds trying local → fleetapi → cloud modes

This meant after network interruptions, the TEDAPI session would be invalid but the service would keep trying with the expired credentials.

## Solution Implemented

### 1. Enhanced Error Detection

Added a new `_is_auth_error()` function that detects authentication failures by:
- Checking HTTP response status codes (401, 403)
- Inspecting exception messages for auth-related keywords
- Following exception chains (cause/context) to find nested auth errors

```python
def _is_auth_error(exc: BaseException) -> bool:
    """Return True if exc represents an authentication failure (403/401)."""
    # Checks HTTP status codes, exception messages, and nested exceptions
```

### 2. Dual Failure Tracking

Added tracking of **two types** of failures:

1. **Authentication failures** - Session expired, needs re-auth
   ```python
   self._consecutive_auth_failures = 0
   self._max_auth_failures = 3  # Force full reconnect after this many 403s
   ```

2. **Connection failures** - Network down, can't reach gateway
   ```python
   self._consecutive_connection_failures = 0
   self._max_connection_failures = 2  # Don't retry connection creation too many times
   ```

This implements a **circuit breaker pattern** to prevent:
- Infinite retries with bad credentials
- Long blocking delays when network is down

### 3. Fast-Fail Connection Strategy

Modified connection to **fail fast** instead of retrying internally:
```python
self._powerwall = pypowerwall.Powerwall(
    ...
    auto_select=True,
    retry_modes=False,  # Changed: Don't let pypowerwall retry - we handle it
)
```

**Benefits:**
- Connection attempts fail in ~10 seconds instead of 60+ seconds
- Service remains responsive even when Powerwall is offline
- We control retry logic instead of pypowerwall

### 4. Circuit Breaker for Connection Attempts

After 2 consecutive connection failures, stop trying until:
- Next poll cycle (allows time for network recovery)
- 5 consecutive poll failures (service layer resets counter)
- Successful connection (resets counter)

```python
if self._consecutive_connection_failures >= self._max_connection_failures:
    LOGGER.debug("Skipping connection attempt (%d consecutive failures). Will retry on next poll.")
    raise PowerwallUnavailableError("Too many consecutive connection failures")
```

### 5. Enhanced `_ensure_connection()` Method

Modified to support forced reconnection and track failures:
```python
def _ensure_connection(self, force_reconnect: bool = False) -> None:
    if force_reconnect:
        LOGGER.info("Forcing full reconnection...")
        
    # Circuit breaker check
    if self._consecutive_connection_failures >= self._max_connection_failures:
        raise PowerwallUnavailableError("Too many consecutive connection failures")
    
    try:
        # Create connection with retry_modes=False
        ...
        # Success! Reset both counters
        self._consecutive_auth_failures = 0
        self._consecutive_connection_failures = 0
    except Exception as exc:
        self._consecutive_connection_failures += 1
        LOGGER.warning("Connection attempt failed (%d/%d)", ...)
```

### 6. Comprehensive Error Handling in `fetch_snapshot()`

Completely rewrote the snapshot fetching logic with:

#### A. Proactive Reconnection
```python
force_reconnect = self._consecutive_auth_failures >= self._max_auth_failures
self._ensure_connection(force_reconnect=force_reconnect)
```

#### B. Per-API-Call Error Handling
Each API call (power(), status(), vitals()) now has individual error handling:

```python
try:
    power_values = powerwall.power()
except Exception as exc:
    if _is_auth_error(exc):
        self._consecutive_auth_failures += 1
        LOGGER.warning("Authentication error (failure %d/%d): %s", ...)
        
        # Attempt immediate recovery if under threshold
        if self._consecutive_auth_failures < self._max_auth_failures:
            LOGGER.info("Attempting reconnection to recover from auth error")
            self.close()
            self._ensure_connection(force_reconnect=True)
            powerwall = self._powerwall
            power_values = powerwall.power()  # Retry
        else:
            raise PowerwallUnavailableError(...)
    elif _is_connection_error(exc):
        raise PowerwallUnavailableError(...)
```

#### C. Automatic Recovery
On successful snapshot after previous failures:
```python
if self._consecutive_auth_failures > 0:
    LOGGER.info("Successfully recovered from previous auth failures")
    self._consecutive_auth_failures = 0
```

#### D. Safe API Calls
Added helper method for non-critical data:
```python
def _safe_call(self, func, default=None):
    """Safely call a function, returning default on any exception."""
    try:
        return func()
    except Exception as exc:
        LOGGER.debug("Safe call failed: %s", exc)
        return default
```

Used for site_name, firmware, din, etc. that shouldn't fail the entire snapshot.

## Behavior Changes

### Before
```
Network drops → Timeouts (5 retries) → WiFi reconnection
Network restored → 403 errors → Service logs errors but continues
... continues polling with expired session indefinitely ...
```

### After
```
Network drops → Timeouts → WiFi reconnection
Network restored → 403 error detected
  ↓
Auth failure counter: 1/3
Immediate reconnection attempt
  ↓
If successful: Reset counter, continue
If fails again: Counter 2/3, try again next poll
If fails 3rd time: Force full reconnection (counter >= 3)
  ↓
On next poll: Creates entirely new Powerwall instance
Success: Reset counter, log recovery
```

## Error Flow Diagram

```
┌─────────────────────────────────────────────────────────┐
│ Poll Attempt                                            │
├─────────────────────────────────────────────────────────┤
│ Check: consecutive_auth_failures >= 3?                  │
│   YES → Force full reconnect                            │
│   NO  → Use existing connection if valid                │
└─────────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────┐
│ Fetch power(), status(), vitals()                       │
├─────────────────────────────────────────────────────────┤
│ On each API call:                                       │
│   • Network error? → Raise PowerwallUnavailableError    │
│   • Auth error (403/401)?                               │
│     - Increment failure counter                         │
│     - Log warning with counter                          │
│     - If < threshold: Immediate reconnect + retry       │
│     - If >= threshold: Raise error, next poll will      │
│       force full reconnect                              │
│   • Success? → Reset counter if previously failing      │
└─────────────────────────────────────────────────────────┘
```

## Logging Improvements

New log messages help diagnose issues:

```
INFO: Forcing full reconnection to Powerwall (auth failures: 3)
WARNING: Authentication error fetching power metrics (failure 1/3): ...
INFO: Attempting reconnection to recover from auth error
INFO: Successfully recovered from previous auth failures
```

## Benefits

1. **Automatic recovery** from authentication failures without manual intervention
2. **Progressive escalation** - tries quick recovery first, full reconnect as fallback
3. **Clear visibility** into connection health through detailed logging
4. **Prevents infinite retry loops** with expired credentials
5. **Minimal disruption** to data collection - attempts recovery during same poll cycle
6. **Network failure detection** remains robust with existing WiFi reconnection logic

## Testing Recommendations

Monitor logs for:
- Auth failure warnings and recovery messages
- Frequency of forced reconnections (should be rare after initial stability)
- Time to recover after network interruptions

Expected behavior after network drop:
1. Initial timeouts and WiFi reconnection
2. Possible 1-2 auth failures as session restores
3. Automatic recovery within 1-3 poll cycles
4. Normal operation resumes

If you see repeated forced reconnections (every poll), that indicates a deeper issue with the Powerwall gateway or network configuration.
