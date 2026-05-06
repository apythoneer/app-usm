"""
KeePass credential service — retrieves credentials via KeePass REST API.

Cache strategy
--------------
* All credentials are cached in memory with a configurable TTL (default 3600 s).
* A background daemon thread proactively refreshes credentials 5 minutes
  before they expire, so collector threads never block waiting on KeePass.
* If KeePass is unreachable during a refresh, stale credentials are kept
  for an additional 30 minutes and a warning is logged.
* Thread-safe: an RLock guards all cache reads and writes.
* Call prefetch_credentials() at startup to warm the cache sequentially
  before parallel collectors run, avoiding a startup stampede.
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

import requests

from app.core.config import get_settings

logger = logging.getLogger("usm.keepass")
settings = get_settings()

# ---------------------------------------------------------------------------
# Cache configuration
# ---------------------------------------------------------------------------
_CACHE_TTL: int = 86400      # 24 hours — credentials rarely change, refresh at midnight
_REFRESH_BEFORE: int = 3600  # proactively refresh 1 hour before expiry
_STALE_EXTEND: int = 14400   # keep stale creds 4 hours if KeePass is unreachable
_FETCH_TIMEOUT: int = 15     # HTTP timeout per KeePass request (seconds)

# Cache entry: { key -> (creds_dict, expires_at_monotonic) }
_cache: Dict[str, Tuple[Dict[str, str], float]] = {}
_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_from_keepass(key: str) -> Dict[str, str]:
    """Single blocking HTTP GET to KeePass. Raises on any error."""
    url = f"{settings.keepass_url}/{key}"
    resp = requests.get(url, timeout=_FETCH_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"KeePass returned non-dict for '{key}': {str(data)[:100]}")
    return {
        "username": data.get("UserName") or data.get("Username") or "",
        "password": data.get("Password") or "",
    }


def _store(key: str, creds: Dict[str, str]) -> None:
    """Write (or overwrite) a cache entry with a fresh TTL."""
    with _lock:
        _cache[key] = (creds, time.monotonic() + _CACHE_TTL)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_credentials(key: str, force_refresh: bool = False) -> Dict[str, str]:
    """
    Return credentials for *key*.

    - Cache hit, not expired → immediate return (no network call).
    - Expired or force_refresh → fetches from KeePass, re-caches, returns fresh.
    - KeePass unreachable during refresh → returns stale credentials (if any)
      with TTL extended by _STALE_EXTEND; raises CredentialError only when
      no cached value exists at all.
    """
    with _lock:
        entry = _cache.get(key)
        if not force_refresh and entry:
            creds, expires_at = entry
            if time.monotonic() < expires_at:
                return creds

    # Cache miss or expired — fetch outside the lock to avoid blocking others
    try:
        creds = _fetch_from_keepass(key)
        _store(key, creds)
        logger.info(f"KeePass credentials cached for '{key}' (TTL {_CACHE_TTL}s)")
        return creds
    except Exception as exc:
        logger.error(f"KeePass fetch failed for '{key}': {exc}")
        with _lock:
            entry = _cache.get(key)
        if entry:
            stale_creds, _ = entry
            with _lock:
                _cache[key] = (stale_creds, time.monotonic() + _STALE_EXTEND)
            logger.warning(
                f"Using stale credentials for '{key}' "
                f"(KeePass unreachable — will retry in {_STALE_EXTEND // 60} min)"
            )
            return stale_creds
        from app.core.exceptions import CredentialError
        raise CredentialError(f"Cannot retrieve credentials for '{key}': {exc}")


def prefetch_credentials(keys: List[str]) -> None:
    """
    Pre-warm the cache for every key, fetching sequentially.

    Call this once at startup (before collector threads begin) so that all
    parallel collector threads hit the in-memory cache rather than racing
    to KeePass simultaneously.  Per-key errors are logged but do not abort.
    """
    logger.info(f"Pre-fetching {len(keys)} KeePass credential(s) at startup…")
    ok = 0
    for key in keys:
        try:
            get_credentials(key)
            ok += 1
        except Exception:
            pass  # already logged inside get_credentials
    logger.info(f"Credential pre-fetch complete: {ok}/{len(keys)} succeeded")


def invalidate_cache(key: Optional[str] = None) -> None:
    """Remove one or all cache entries (call after a credential rotation)."""
    with _lock:
        if key:
            _cache.pop(key, None)
            logger.info(f"Credential cache invalidated for '{key}'")
        else:
            _cache.clear()
            logger.info("Entire credential cache cleared")


def cache_summary() -> Dict[str, str]:
    """Return human-readable cache status (for /health endpoint)."""
    now = time.monotonic()
    with _lock:
        return {
            k: f"expires_in={max(0, int(exp - now))}s"
            for k, (_, exp) in _cache.items()
        }


# ---------------------------------------------------------------------------
# Background refresh thread — proactively refreshes before TTL expires
# ---------------------------------------------------------------------------

def _background_refresh_loop() -> None:
    """
    Wakes every 60 s, finds credentials whose TTL will expire within
    _REFRESH_BEFORE seconds, and refreshes them proactively.
    Runs as a daemon thread so it never blocks process shutdown.
    """
    while True:
        time.sleep(60)
        now = time.monotonic()
        with _lock:
            keys_due = [
                k for k, (_, exp) in _cache.items()
                if exp - now <= _REFRESH_BEFORE
            ]
        for key in keys_due:
            logger.debug(f"Background credential refresh: '{key}'")
            try:
                creds = _fetch_from_keepass(key)
                _store(key, creds)
                logger.info(f"Background credential refresh OK for '{key}'")
            except Exception as exc:
                logger.warning(f"Background refresh failed for '{key}': {exc}")
                # Stale credentials remain in cache; the stale-fallback path
                # in get_credentials() will extend TTL on the next access.


_refresh_thread = threading.Thread(
    target=_background_refresh_loop,
    name="keepass-refresh",
    daemon=True,
)
_refresh_thread.start()


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def refresh_all_cached() -> int:
    """
    Force-refresh all currently cached credential keys from KeePass.
    Called by the midnight cron job. Returns count of successfully refreshed keys.
    """
    with _lock:
        keys = list(_cache.keys())
    if not keys:
        logger.info("Credential refresh: no cached keys to refresh")
        return 0

    logger.info(f"Credential refresh: refreshing {len(keys)} cached keys...")
    ok = 0
    for key in keys:
        try:
            creds = _fetch_from_keepass(key)
            _store(key, creds)
            ok += 1
        except Exception as exc:
            logger.warning(f"Credential refresh failed for '{key}': {exc}")
    logger.info(f"Credential refresh complete: {ok}/{len(keys)} succeeded")
    return ok


def browse_entries() -> Dict[str, List[str]]:
    """
    List all KeePass groups and entry names (no passwords).
    Returns {group_name: [entry_name, ...], ...}
    """
    try:
        url = settings.keepass_url.rstrip("/").rsplit("/", 1)[0]  # strip /keepass → base url
        # The KeePass Flask root returns the full group/entry tree
        resp = requests.get(f"{url}/", timeout=_FETCH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.error(f"KeePass browse failed: {e}")
    return {}


def get_sql_credentials() -> Dict[str, str]:
    return get_credentials(settings.sql_cred_key)


def get_pure_credentials() -> Dict[str, str]:
    return get_credentials(settings.pure_cred_key)
