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
        # Candidate hosts in preference order: FQDN, then management IP, then the
        # array name. Many cloud CVO instances have an FQDN that does NOT resolve
        # from the monitoring host (and DimStorageFinance sometimes maps a wrong or
        # duplicate FQDN), while the mgmt_ip is reachable. The old
        # `fqdn or mgmt_ip or name` bound a SINGLE host at construction and never
        # fell back, so those arrays stayed permanently unreachable. authenticate()
        # now tries each until one works (same approach as the StorageGrid client).
        seen = set()
        self._hosts = [
            h for h in ((fqdn or "").strip(), (mgmt_ip or "").strip(), array_name)
            if h and not (h in seen or seen.add(h))
        ]
        self.host = self._hosts[0] if self._hosts else array_name
        self.base_url = f"https://{self.host}/api"
        self.session: Optional[requests.Session] = None


    def authenticate(self) -> bool:
        """Authenticate using Basic Auth, trying each candidate host in turn."""
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

        last_reason = None
        for host in self._hosts:
            base_url = f"https://{host}/api"
            session = requests.Session()
            session.auth = (username, password)
            session.verify = False
            session.headers["Accept"] = "application/hal+json"
            try:
                resp = session.get(f"{base_url}/cluster", timeout=15)
                if resp.status_code == 200:
                    self.host = host
                    self.base_url = base_url
                    self.session = session
                    if host != self._hosts[0]:
                        logger.info(f"[{self.array_name}] Reached via fallback host {host}")
                    return True
                if resp.status_code in (401, 403):
                    # Credentials rejected — the box is reachable, so trying other
                    # hosts with the same creds is pointless. Stop here.
                    logger.error(
                        f"[{self.array_name}] Credentials rejected (HTTP {resp.status_code}) "
                        f"by {host} — check KeePass entry '{self.cred_key}'"
                    )
                    return False
                last_reason = f"HTTP {resp.status_code} from {host}"
            except requests.exceptions.ConnectionError as e:
                last_reason = ("DNS did not resolve" if "gaierror" in repr(e)
                               or "Name or service" in str(e) else "host unreachable") + f" ({host})"
                continue  # try the next candidate host
            except requests.exceptions.Timeout:
                last_reason = f"timeout ({host})"
                continue
            except Exception as e:
                last_reason = f"{str(e)[:50]} ({host})"
                continue
        logger.error(
            f"[{self.array_name}] Cannot reach any host {self._hosts} ({last_reason}) — "
            f"connectivity/inventory problem, not credentials. Check array_fqdn / mgmt_ip."
        )
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
