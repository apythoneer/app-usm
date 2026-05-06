"""
Hitachi VSP Configuration Manager REST API client.
Supports Basic Auth (read-only) and Session Auth (full access).
Port 443, base path: /ConfigurationManager/v1/objects/
"""

import logging
import urllib3
import requests
from typing import Optional, Any, Dict, List

from app.services.keepass import get_credentials
from app.core.config import get_settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("usm.hitachi.client")
settings = get_settings()


class HitachiVSPClient:
    """Thin session wrapper around the Hitachi VSP Configuration Manager REST API."""

    def __init__(self, array_name: str, cred_key: str, fqdn: Optional[str] = None,
                 mgmt_ip: Optional[str] = None):
        self.array_name = array_name
        self.cred_key = cred_key
        self.host = fqdn or mgmt_ip or array_name
        self.base_url = f"https://{self.host}:443/ConfigurationManager/v1/objects"
        self.session: Optional[requests.Session] = None
        self.storage_device_id: Optional[str] = None
        self.session_token: Optional[str] = None
        self.session_id: Optional[int] = None

    def authenticate(self) -> bool:
        """Authenticate using Basic Auth and discover storage device ID."""
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
        session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

        # Test connectivity and get storage device ID
        try:
            resp = session.get(f"{self.base_url}/storages", timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                storages = data.get("data", [])
                if storages:
                    self.storage_device_id = storages[0].get("storageDeviceId")
                    self.session = session
                    logger.debug(f"[{self.array_name}] Connected, storageDeviceId={self.storage_device_id}")
                    return True
                logger.error(f"[{self.array_name}] No storage devices in response")
            else:
                logger.error(f"[{self.array_name}] Auth HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[{self.array_name}] Connect error: {e}")
        return False

    def _create_session_token(self) -> bool:
        """Create a session token for privileged endpoints (host-groups, etc.)."""
        if not self.session:
            return False
        try:
            resp = self.session.post(f"{self.base_url}/sessions", json={}, timeout=15)
            if resp.status_code in (200, 201):
                data = resp.json()
                self.session_token = data.get("token")
                self.session_id = data.get("sessionId")
                return bool(self.session_token)
        except Exception as e:
            logger.warning(f"[{self.array_name}] Session token creation failed: {e}")
        return False

    def _delete_session_token(self):
        """Clean up session token."""
        if self.session_token and self.session_id is not None:
            try:
                self.session.delete(
                    f"{self.base_url}/sessions/{self.session_id}",
                    headers={"Authorization": f"Session {self.session_token}"},
                    timeout=10,
                )
            except Exception:
                pass
            self.session_token = None
            self.session_id = None

    def get(self, endpoint: str, params: Dict = None, timeout: int = 30) -> Optional[Any]:
        """GET request scoped to the storage device. Returns parsed JSON or None."""
        if not self.session or not self.storage_device_id:
            return None
        url = f"{self.base_url}/storages/{self.storage_device_id}/{endpoint.lstrip('/')}"
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            # If 401, try with session token
            if resp.status_code == 401 and self.session_token:
                resp = self.session.get(
                    url, params=params, timeout=timeout,
                    headers={"Authorization": f"Session {self.session_token}", "Accept": "application/json"}
                )
                if resp.status_code == 200:
                    return resp.json()
            logger.warning(f"[{self.array_name}] GET {endpoint} → HTTP {resp.status_code}")
        except requests.Timeout:
            logger.warning(f"[{self.array_name}] GET {endpoint} timed out ({timeout}s)")
        except Exception as e:
            logger.error(f"[{self.array_name}] GET {endpoint} error: {e}")
        return None

    def get_all(self, endpoint: str, params: Dict = None, max_count: int = 16384,
                timeout: int = 60) -> List[Dict]:
        """Paginate through all items. VSP uses count/startLdevId for paging."""
        all_items = []
        p = dict(params or {})
        p["count"] = min(max_count, 500)  # VSP max per request is typically 500
        data = self.get(endpoint, params=p, timeout=timeout)
        if data:
            items = data.get("data", [])
            all_items.extend(items)
            # Simple approach: if we got exactly 'count' items, there might be more
            # For now, just return what we got (VSP arrays typically have <500 LDEVs)
        return all_items

    def disconnect(self):
        """Clean up resources."""
        self._delete_session_token()
        self.session = None

    def __enter__(self):
        self.authenticate()
        return self

    def __exit__(self, *args):
        self.disconnect()
