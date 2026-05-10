"""
Dell EMC Unity Unisphere REST API client.
Session-based auth with CSRF token on port 443.
"""

import logging
import urllib3
import requests
from typing import Optional, Any, Dict, List

from app.services.keepass import get_credentials
from app.core.config import get_settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("usm.dell.client")
settings = get_settings()


class DellUnityClient:
    """Thin session wrapper around the Dell EMC Unity Unisphere REST API."""

    def __init__(self, array_name: str, cred_key: str, fqdn: Optional[str] = None,
                 mgmt_ip: Optional[str] = None):
        self.array_name = array_name
        self.cred_key = cred_key
        self.host = fqdn or mgmt_ip or array_name
        self.base_url = f"https://{self.host}:443/api"
        self.session: Optional[requests.Session] = None

    def authenticate(self) -> bool:
        """Authenticate via Unity session login with CSRF token."""
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
        session.verify = False
        session.headers.update({
            "X-EMC-REST-CLIENT": "true",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

        try:
            # Unity login — GET with Basic Auth returns CSRF token
            resp = session.get(
                f"{self.base_url}/types/loginSessionInfo/instances",
                auth=(username, password),
                timeout=15,
            )
            if resp.status_code == 200:
                csrf = resp.headers.get("EMC-CSRF-TOKEN", "")
                if csrf:
                    session.headers["EMC-CSRF-TOKEN"] = csrf
                self.session = session
                return True
            logger.error(f"[{self.array_name}] Auth HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[{self.array_name}] Auth error: {e}")
        return False

    def get(self, resource_type: str, fields: str = "", params: Dict = None,
            timeout: int = 30) -> Optional[Any]:
        """GET Unity resource instances. Returns parsed JSON or None."""
        if not self.session:
            return None
        url = f"{self.base_url}/types/{resource_type}/instances"
        p = dict(params or {})
        if fields:
            p["fields"] = fields
        try:
            resp = self.session.get(url, params=p, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"[{self.array_name}] GET {resource_type} → HTTP {resp.status_code}")
        except requests.Timeout:
            logger.warning(f"[{self.array_name}] GET {resource_type} timed out ({timeout}s)")
        except Exception as e:
            logger.error(f"[{self.array_name}] GET {resource_type} error: {e}")
        return None

    def get_instance(self, resource_type: str, instance_id: str, fields: str = "",
                     timeout: int = 30) -> Optional[Any]:
        """GET a single Unity resource instance by ID."""
        if not self.session:
            return None
        url = f"{self.base_url}/instances/{resource_type}/{instance_id}"
        p = {}
        if fields:
            p["fields"] = fields
        try:
            resp = self.session.get(url, params=p, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"[{self.array_name}] GET {resource_type}/{instance_id}: {e}")
        return None

    def disconnect(self):
        """Logout from Unity session."""
        if self.session:
            try:
                self.session.post(
                    f"{self.base_url}/types/loginSessionInfo/action/logout",
                    json={},
                    timeout=5,
                )
            except Exception:
                pass
        self.session = None

    def __enter__(self):
        self.authenticate()
        return self

    def __exit__(self, *args):
        self.disconnect()
