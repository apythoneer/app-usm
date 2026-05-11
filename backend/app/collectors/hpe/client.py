"""
HPE 3Par / Primera / Alletra WSAPI REST client.
Handles session-key auth, auto-port detection (8080 for 3Par, 443 for Primera/Alletra).
Reused by all HPE collector types.
"""

import logging
import urllib3
import requests
from typing import Optional, Any, Dict, List

from app.services.keepass import get_credentials
from app.core.config import get_settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("usm.hpe.client")
settings = get_settings()

# 3Par uses port 8080, Primera/Alletra use 443
_PORT_MAP = {
    "3par": 8080,
    "primera": 443,
    "alletra": 443,
}


class HPEClient:
    """Thin session wrapper around the HPE WSAPI REST API."""

    def __init__(self, array_name: str, cred_key: str, fqdn: Optional[str] = None,
                 model: Optional[str] = None, mgmt_ip: Optional[str] = None):
        self.array_name = array_name
        self.cred_key = cred_key
        self.host = fqdn or mgmt_ip or array_name
        self.model = (model or "").lower()
        self.session_key: Optional[str] = None
        self._session = requests.Session()
        self._session.verify = False
        self._session.headers["Content-Type"] = "application/json"
        self._session.headers["Accept"] = "application/json"

        # Determine port based on model
        self.port = 8080  # default for 3Par
        for key, port in _PORT_MAP.items():
            if key in self.model:
                self.port = port
                break

        self.base_url = f"https://{self.host}:{self.port}/api/v1"

    def authenticate(self) -> bool:
        """Authenticate using session-key auth from KeePass credentials."""
        try:
            creds = get_credentials(self.cred_key)
            username = creds.get("username") or ""
            password = creds.get("password") or ""
            if not username or not password:
                logger.error(f"[{self.array_name}] Missing credentials from KeePass key '{self.cred_key}'")
                return False
        except Exception as e:
            logger.error(f"[{self.array_name}] KeePass error for '{self.cred_key}': {e}")
            return False

        try:
            resp = self._session.post(
                f"{self.base_url}/credentials",
                json={"user": username, "password": password},
                timeout=15,
            )
            if resp.status_code == 201:
                self.session_key = resp.json().get("key", "")
                self._session.headers["X-HP3PAR-WSAPI-SessionKey"] = self.session_key
                logger.info(f"[{self.array_name}] WSAPI auth OK (port {self.port})")
                return True
            logger.error(f"[{self.array_name}] Auth HTTP {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            logger.error(f"[{self.array_name}] Auth error: {e}")
        return False

    def get(self, endpoint: str, params: Dict = None, timeout: int = 60) -> Optional[Any]:
        """GET request, returns parsed JSON or None on failure."""
        if not self.session_key:
            return None
        try:
            resp = self._session.get(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                params=params,
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"[{self.array_name}] GET {endpoint} → HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[{self.array_name}] GET {endpoint} error: {e}")
        return None

    def disconnect(self):
        """Delete the session key."""
        if self.session_key:
            try:
                self._session.delete(
                    f"{self.base_url}/credentials/{self.session_key}",
                    timeout=5,
                )
            except Exception:
                pass
            self.session_key = None

    def __enter__(self):
        self.authenticate()
        return self

    def __exit__(self, *args):
        self.disconnect()
