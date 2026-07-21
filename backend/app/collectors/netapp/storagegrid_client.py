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
        # Candidate hosts in preference order. The FQDN is tried first, but some
        # StorageGrid FQDNs (e.g. nalw1an01/namr1an01.corp.intranet) do NOT
        # resolve from the monitoring host while the mgmt_ip does — the old
        # `fqdn or mgmt_ip` picked the dead FQDN and never fell back, leaving
        # those nodes permanently stale. Try each in turn until auth succeeds.
        self._hosts = [h for h in (
            (fqdn or "").strip(), (mgmt_ip or "").strip(), array_name,
        ) if h]
        # de-dup while preserving order
        seen = set()
        self._hosts = [h for h in self._hosts if not (h in seen or seen.add(h))]
        self.host = self._hosts[0]
        self.base_url = f"https://{self.host}:443/api/v3"
        self.session: Optional[requests.Session] = None
        self.token: Optional[str] = None

    def authenticate(self) -> bool:
        """Authenticate via bearer token, trying each candidate host in turn."""
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

        last_err = None
        for host in self._hosts:
            base_url = f"https://{host}:443/api/v3"
            try:
                resp = requests.post(
                    f"{base_url}/authorize",
                    json={"username": username, "password": password, "cookie": False, "csrfToken": False},
                    verify=False,
                    timeout=15,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    self.token = resp.json().get("data", "")
                    if self.token:
                        self.host = host
                        self.base_url = base_url
                        self.session = requests.Session()
                        self.session.verify = False
                        self.session.headers.update({
                            "Authorization": f"Bearer {self.token}",
                            "Accept": "application/json",
                        })
                        if host != self._hosts[0]:
                            logger.info(f"[{self.array_name}] Reached via fallback host {host}")
                        return True
                # Credentials rejected — no point trying other hosts with the
                # same creds, and the box is clearly reachable.
                if resp.status_code in (401, 403):
                    logger.error(f"[{self.array_name}] Credentials rejected (HTTP {resp.status_code})")
                    return False
                last_err = f"HTTP {resp.status_code}"
            except requests.exceptions.ConnectionError as e:
                last_err = "unreachable" if "gaierror" not in repr(e) else "DNS did not resolve"
                continue  # try the next candidate host
            except Exception as e:
                last_err = str(e)[:60]
                continue
        logger.error(
            f"[{self.array_name}] Cannot reach any host {self._hosts} ({last_err}) — "
            f"connectivity/inventory issue, not credentials."
        )
        return False

    def metric_query(self, promql: str) -> Optional[float]:
        """Sum a StorageGrid Prometheus instant query via grid/metric-query.

        Capacity is NOT in grid/health/topology (that returns empty attributes on
        this StorageGrid version). It lives in the metrics API, and the endpoint
        is grid/metric-query (singular) — grid/metrics/query returns a plaintext
        '404 page not found', which is what made the old json() parse choke.
        """
        r = self.get("grid/metric-query", params={"query": promql})
        if not r or r.get("status") != "success":
            return None
        result = (r.get("data") or {}).get("result") or []
        total = 0.0
        for item in result:
            val = item.get("value", [None, None])[1]
            if val is not None:
                try:
                    total += float(val)
                except (TypeError, ValueError):
                    pass
        return total

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
