"""
Oracle ZFS Storage Appliance REST API client.
Handles Basic Auth on port 215, used by all Oracle collector types.
"""

import logging
import urllib3
import requests
from typing import Optional, Any, Dict

from app.services.keepass import get_credentials
from app.core.config import get_settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("usm.oracle.client")
settings = get_settings()


class OracleZFSClient:
    """Thin session wrapper around the Oracle ZFS REST API."""

    def __init__(self, array_name: str, cred_key: str, fqdn: Optional[str] = None,
                 mgmt_ip: Optional[str] = None):
        self.array_name = array_name
        self.cred_key = cred_key
        self.host = fqdn or mgmt_ip or array_name
        self.base_url = f"https://{self.host}:215/api"
        self.session: Optional[requests.Session] = None

    def authenticate(self) -> bool:
        """Authenticate using Basic Auth from KeePass credentials."""
        try:
            creds = get_credentials(self.cred_key)
            username = creds.get("username") or ""
            password = creds.get("password") or ""
            if not username or not password:
                logger.error(f"[{self.array_name}] Missing credentials from '{self.cred_key}'")
                return False
        except Exception as e:
            logger.error(f"[{self.array_name}] KeePass error: {e}")
            return False

        session = requests.Session()
        session.auth = (username, password)
        session.verify = False
        session.headers["Accept"] = "application/json"

        # Test connectivity
        try:
            resp = session.get(f"{self.base_url}/storage/v1", timeout=15)
            if resp.status_code == 200:
                self.session = session
                return True
            logger.error(f"[{self.array_name}] Auth HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[{self.array_name}] Auth error: {e}")
        return False

    def get(self, endpoint: str, params: Dict = None) -> Optional[Any]:
        """GET request, returns parsed JSON or None."""
        if not self.session:
            return None
        try:
            resp = self.session.get(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                params=params,
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"[{self.array_name}] GET {endpoint} → HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[{self.array_name}] GET {endpoint} error: {e}")
        return None

    def disconnect(self):
        self.session = None

    def __enter__(self):
        self.authenticate()
        return self

    def __exit__(self, *args):
        self.disconnect()
