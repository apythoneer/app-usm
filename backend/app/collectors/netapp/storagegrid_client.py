"""
NetApp StorageGrid Management API client.
Token-based auth via POST /api/v3/authorize on port 443.
Only admin nodes expose the management API.
"""

import logging
import urllib3
import requests
from typing import Optional, Any, Dict

from app.services.keepass import get_credentials
from app.core.config import get_settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("usm.storagegrid.client")
settings = get_settings()


class StorageGridClient:
    """Client for NetApp StorageGrid Management API v3."""

    def __init__(self, array_name: str, cred_key: str, fqdn: Optional[str] = None,
                 mgmt_ip: Optional[str] = None):
        self.array_name = array_name
        self.cred_key = cred_key
        self.host = fqdn or mgmt_ip or array_name
        self.base_url = f"https://{self.host}:443/api/v3"
        self.session: Optional[requests.Session] = None
        self.token: Optional[str] = None

    def authenticate(self) -> bool:
        """Authenticate via bearer token."""
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

        try:
            resp = requests.post(
                f"{self.base_url}/authorize",
                json={"username": username, "password": password, "cookie": False, "csrfToken": False},
                verify=False,
                timeout=15,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                self.token = resp.json().get("data", "")
                if self.token:
                    self.session = requests.Session()
                    self.session.verify = False
                    self.session.headers.update({
                        "Authorization": f"Bearer {self.token}",
                        "Accept": "application/json",
                    })
                    return True
            logger.error(f"[{self.array_name}] Auth HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[{self.array_name}] Auth error: {e}")
        return False

    def get(self, endpoint: str, params: Dict = None, timeout: int = 30) -> Optional[Any]:
        """GET request to the grid API. Returns parsed JSON or None."""
        if not self.session:
            return None
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"[{self.array_name}] GET {endpoint} → HTTP {resp.status_code}")
        except requests.Timeout:
            logger.warning(f"[{self.array_name}] GET {endpoint} timed out ({timeout}s)")
        except Exception as e:
            logger.error(f"[{self.array_name}] GET {endpoint} error: {e}")
        return None

    def disconnect(self):
        """Revoke the auth token."""
        if self.session and self.token:
            try:
                self.session.delete(f"{self.base_url}/authorize", timeout=5)
            except Exception:
                pass
        self.session = None
        self.token = None

    def __enter__(self):
        self.authenticate()
        return self

    def __exit__(self, *args):
        self.disconnect()
