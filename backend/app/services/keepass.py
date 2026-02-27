"""
KeePass credential service — retrieves credentials via KeePass REST API.
Caches per key to avoid repeated HTTP calls.
"""

import logging
import requests
from functools import lru_cache
from typing import Dict

from app.core.config import get_settings

logger = logging.getLogger("usm.keepass")
settings = get_settings()

_cache: Dict[str, Dict[str, str]] = {}


def get_credentials(key: str) -> Dict[str, str]:
    """
    Fetch credentials from KeePass by key name.
    Returns dict with 'username' and 'password'.
    Raises CredentialError on failure.
    """
    if key in _cache:
        return _cache[key]

    try:
        url = f"{settings.keepass_url}/{key}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        creds = {
            "username": data.get("UserName") or data.get("Username") or "",
            "password": data.get("Password") or "",
        }
        _cache[key] = creds
        logger.info(f"Credentials fetched for key: {key}")
        return creds
    except Exception as e:
        logger.error(f"KeePass credential fetch failed for '{key}': {e}")
        from app.core.exceptions import CredentialError
        raise CredentialError(f"Cannot retrieve credentials for key '{key}': {e}")


def get_sql_credentials() -> Dict[str, str]:
    return get_credentials(settings.sql_cred_key)


def get_pure_credentials() -> Dict[str, str]:
    return get_credentials(settings.pure_cred_key)


def invalidate_cache(key: str = None):
    """Clear cached credentials (call after rotation)."""
    global _cache
    if key:
        _cache.pop(key, None)
    else:
        _cache.clear()
