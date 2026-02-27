"""
Pure Storage REST API client (v1.19).
Handles authentication, session management, and raw API calls.
Reused by all Pure collector types.
"""

import logging
import urllib3
import requests
from typing import Optional, Any, Dict

from app.services.keepass import get_credentials
from app.core.config import get_settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("usm.pure.client")
settings = get_settings()
API_VERSION = "1.19"


class PureClient:
    """Thin session wrapper around the Pure Storage REST API."""

    def __init__(self, array_name: str):
        self.array_name = array_name
        self.base_url = f"https://{array_name}/api/{API_VERSION}"
        self.session: Optional[requests.Session] = None

    def authenticate(self) -> bool:
        api_token = self._get_api_token()
        if not api_token:
            logger.error(f"[{self.array_name}] No API token from KeePass")
            return False

        session = requests.Session()
        session.verify = False
        session.headers["Content-Type"] = "application/json"

        try:
            resp = session.post(
                f"{self.base_url}/auth/session",
                json={"api_token": api_token},
                timeout=10,
            )
            if resp.status_code == 200:
                self.session = session
                return True
            logger.error(f"[{self.array_name}] Auth HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[{self.array_name}] Auth error: {e}")
        return False

    def get(self, endpoint: str, params: Dict = None) -> Optional[Any]:
        """GET request, returns parsed JSON or None on failure."""
        if not self.session:
            return None
        try:
            resp = self.session.get(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                params=params,
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"[{self.array_name}] GET {endpoint} → HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[{self.array_name}] GET {endpoint} error: {e}")
        return None

    def disconnect(self):
        if self.session:
            try:
                self.session.delete(f"{self.base_url}/auth/session", timeout=5)
            except Exception:
                pass
            self.session = None

    def _get_api_token(self) -> Optional[str]:
        """Fetch per-array API token from KeePass."""
        try:
            creds = get_credentials(f"PureStorage_API_{self.array_name}")
            return creds.get("password") or creds.get("Password")
        except Exception as e:
            logger.error(f"[{self.array_name}] KeePass error: {e}")
            return None

    def __enter__(self):
        self.authenticate()
        return self

    def __exit__(self, *args):
        self.disconnect()
