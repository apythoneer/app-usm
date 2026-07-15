"""
NetApp ONTAP REST API client.
Handles Basic Auth, auto-pagination (HAL _links.next), and raw API calls.
Reused by all NetApp collector types.
"""

import logging
import urllib3
import requests
from typing import Optional, Any, Dict, List

from app.services.keepass import get_credentials
from app.core.config import get_settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("usm.netapp.client")
settings = get_settings()


class NetAppClient:
    """Thin session wrapper around the NetApp ONTAP REST API."""

    def __init__(
        self,
        array_name: str,
        cred_key: str,
        fqdn: Optional[str] = None,
        mgmt_ip: Optional[str] = None,
    ):
        self.array_name = array_name
        self.cred_key = cred_key
        # Prefer a resolvable FQDN, then management IP, then fall back to the
        # array name. Short array names (e.g. cloud CVO instances) are often not
        # DNS-resolvable from the monitoring host, which caused connection
        # failures — using array_fqdn / mgmt_ip from inventory avoids that.
        self.host = (fqdn or "").strip() or (mgmt_ip or "").strip() or array_name
        self.base_url = f"https://{self.host}/api"
        self.session: Optional[requests.Session] = None


    def authenticate(self) -> bool:
        """Authenticate using Basic Auth from KeePass credentials."""
        try:
            creds = get_credentials(self.cred_key)
            username = creds.get("username") or ""
            password = creds.get("password") or ""
            if not username or not password:
                logger.error(f"[{self.array_name}] Missing username/password from KeePass key '{self.cred_key}'")
                return False
        except Exception as e:
            logger.error(f"[{self.array_name}] KeePass error for '{self.cred_key}': {e}")
            return False

        session = requests.Session()
        session.auth = (username, password)
        session.verify = False
        session.headers["Accept"] = "application/hal+json"

        # Test connectivity with a lightweight call.
        #
        # Distinguish "cannot reach the box" from "the box rejected us". Both used
        # to log "Auth error: ..." and surface as base.py's generic
        # RuntimeError("Authentication failed"), so a DNS typo and a bad password
        # were indistinguishable in the logs. That cost real time: the June 2026
        # perf review attributed ~3k failures/day to "stale KeePass entries /
        # cred_key mapping" and proposed auditing the vault, when the actual cause
        # for every failing array was that array_fqdn does not resolve.
        try:
            resp = session.get(f"{self.base_url}/cluster", timeout=15)
            if resp.status_code == 200:
                self.session = session
                return True
            if resp.status_code in (401, 403):
                logger.error(
                    f"[{self.array_name}] Credentials rejected (HTTP {resp.status_code}) "
                    f"by {self.host} — check KeePass entry '{self.cred_key}'"
                )
            else:
                logger.error(
                    f"[{self.array_name}] Unexpected HTTP {resp.status_code} from {self.host} "
                    f"(reachable, but /cluster did not return 200)"
                )
        except requests.exceptions.ConnectionError as e:
            # Covers DNS resolution failures (socket.gaierror) and refused/unroutable
            # TCP. NOT a credentials problem — do not send the reader to the vault.
            reason = "name does not resolve" if "gaierror" in repr(e) or "Name or service" in str(e) \
                else "host unreachable"
            logger.error(
                f"[{self.array_name}] Cannot reach '{self.host}' ({reason}) — this is a "
                f"connectivity/inventory problem, not credentials. Check array_fqdn / "
                f"mgmt_ip in managed_arrays, or disable the array if decommissioned."
            )
        except requests.exceptions.Timeout:
            logger.error(
                f"[{self.array_name}] Timed out after 15s connecting to '{self.host}' — "
                f"reachable-but-slow or filtered; not a credentials problem."
            )
        except Exception as e:
            logger.error(f"[{self.array_name}] Unexpected error contacting '{self.host}': {e}")
        return False

    def get(self, endpoint: str, params: Dict = None) -> Optional[Any]:
        """GET request, returns parsed JSON or None on failure."""
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

    def get_all(self, endpoint: str, params: Dict = None, max_records: int = 5000) -> List[Dict]:
        """
        Auto-paginated GET — follows HAL _links.next.href until all records are fetched.
        Returns the combined list of 'records' from all pages.
        """
        if not self.session:
            return []

        all_records: List[Dict] = []
        p = dict(params or {})
        if "max_records" not in p:
            p["max_records"] = str(min(max_records, 1000))

        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        while url and len(all_records) < max_records:
            try:
                resp = self.session.get(url, params=p, timeout=30)
                if resp.status_code != 200:
                    logger.warning(f"[{self.array_name}] GET {endpoint} page → HTTP {resp.status_code}")
                    break
                data = resp.json()
                all_records.extend(data.get("records", []))
                # Follow pagination
                next_link = data.get("_links", {}).get("next", {}).get("href")
                if next_link:
                    # next_link is a relative path like /api/storage/volumes?start.uuid=...
                    url = f"https://{self.host}{next_link}"

                    p = {}  # params are already in the next URL
                else:
                    break
            except Exception as e:
                logger.error(f"[{self.array_name}] GET {endpoint} pagination error: {e}")
                break

        return all_records

    def disconnect(self):
        """No-op — ONTAP Basic Auth is stateless."""
        self.session = None

    def __enter__(self):
        self.authenticate()
        return self

    def __exit__(self, *args):
        self.disconnect()
