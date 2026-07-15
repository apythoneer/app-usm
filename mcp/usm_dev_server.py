#!/usr/bin/env python3
"""
usm-dev MCP Server
==================
Developer productivity tools for the USM codebase.

Tools:
  - scaffold_vendor(vendor, api_type)  : generate the 4 collector files from templates
  - validate_collector(vendor)         : check a vendor collector conforms to the contract
  - lint_schema()                      : cross-check collector table refs vs known schema
  - check_env()                        : validate required env vars are present
  - list_endpoints()                   : list FastAPI v1 endpoints found in code

Run:
  python mcp/usm_dev_server.py
"""

import os
import re
import glob
from mcp.server.fastmcp import FastMCP

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTORS_DIR = os.path.join(REPO_ROOT, "backend", "app", "collectors")
API_DIR = os.path.join(REPO_ROOT, "backend", "app", "api", "v1")
CONFIG_FILE = os.path.join(REPO_ROOT, "backend", "app", "core", "config.py")

KNOWN_TABLES = {
    "messages", "metrics_current", "metrics_history", "daily_stats",
    "volumes_cache", "hosts_cache", "host_groups_cache",
    "protection_groups_cache", "managed_arrays", "app_settings",
}

mcp = FastMCP("usm-dev")


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"(error reading {path}: {e})"


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------- scaffolding

def _client_template(vendor: str, cls: str, api_type: str) -> str:
    return f'''"""
{vendor} API client ({api_type}).
"""
import logging
import requests
import urllib3
from app.services.keepass import get_credentials

urllib3.disable_warnings()
logger = logging.getLogger("usm.{vendor}.client")


class {cls}Client:
    def __init__(self, array_config):
        self.array = array_config
        self.host = array_config.array_fqdn or array_config.mgmt_ip or array_config.name
        self.session = requests.Session()
        self.session.verify = False
        self.base_url = f"https://{{self.host}}/api"
        self._token = None

    def authenticate(self) -> bool:
        cred_key = self.array.cred_key or f"{cls}_{{self.array.name}}"
        creds = get_credentials(cred_key)
        if not creds:
            logger.error(f"No credentials for {{cred_key}}")
            return False
        try:
            resp = self.session.post(
                f"{{self.base_url}}/login",
                json={{"username": creds["username"], "password": creds["password"]}},
                timeout=30,
            )
            resp.raise_for_status()
            self._token = resp.json().get("token")
            self.session.headers["Authorization"] = f"Bearer {{self._token}}"
            return True
        except Exception as e:
            logger.error(f"Auth failed for {{self.host}}: {{e}}")
            return False

    def get(self, path: str, params: dict = None) -> dict:
        resp = self.session.get(f"{{self.base_url}}/{{path}}", params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def disconnect(self):
        try:
            self.session.close()
        except Exception:
            pass
'''


def _metrics_template(vendor: str, cls: str) -> str:
    return f'''"""
{vendor} metrics collector — capacity & performance.
"""
from datetime import datetime
from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.{vendor}.client import {cls}Client
from app.db.session import get_db_cursor
from app.core.config import get_settings

SCHEMA = get_settings().db_schema


@CollectorRegistry.register("{vendor}", "metrics")
class {cls}MetricsCollector(BaseCollector):
    VENDOR = "{vendor}"
    COLLECTOR_TYPE = "metrics"

    def authenticate(self) -> bool:
        self.client = {cls}Client(self.array_config)
        return self.client.authenticate()

    def collect(self) -> dict:
        sys = self.client.get("system")
        return {{
            "array_name": self.array_name,
            "vendor": self.VENDOR,
            "capacity_total": sys.get("total_bytes", 0),
            "capacity_used": sys.get("used_bytes", 0),
            "capacity_used_pct": sys.get("used_pct", 0.0),
            "read_iops": sys.get("read_iops", 0),
            "write_iops": sys.get("write_iops", 0),
            "read_latency_us": sys.get("read_latency_us", 0),
            "write_latency_us": sys.get("write_latency_us", 0),
            "data_reduction": sys.get("data_reduction", 1.0),
            "array_status": "healthy" if sys.get("healthy") else "degraded",
            "purity_version": sys.get("firmware"),
            "collected_at": datetime.now().isoformat(),
        }}

    def save(self, data, result: CollectorResult) -> bool:
        with get_db_cursor() as cur:
            cur.execute(f"""
                MERGE {{SCHEMA}}.metrics_current AS t
                USING (SELECT ? AS array_name) AS s ON t.array_name = s.array_name
                WHEN MATCHED THEN UPDATE SET
                    vendor=?, capacity_total=?, capacity_used=?, capacity_used_pct=?,
                    read_iops=?, write_iops=?, read_latency_us=?, write_latency_us=?,
                    data_reduction=?, array_status=?, purity_version=?, collected_at=?
                WHEN NOT MATCHED THEN INSERT
                    (array_name, vendor, capacity_total, capacity_used, capacity_used_pct,
                     read_iops, write_iops, read_latency_us, write_latency_us,
                     data_reduction, array_status, purity_version, collected_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?);
            """, (
                data["array_name"],
                data["vendor"], data["capacity_total"], data["capacity_used"], data["capacity_used_pct"],
                data["read_iops"], data["write_iops"], data["read_latency_us"], data["write_latency_us"],
                data["data_reduction"], data["array_status"], data["purity_version"], data["collected_at"],
                data["array_name"], data["vendor"], data["capacity_total"], data["capacity_used"],
                data["capacity_used_pct"], data["read_iops"], data["write_iops"],
                data["read_latency_us"], data["write_latency_us"], data["data_reduction"],
                data["array_status"], data["purity_version"], data["collected_at"],
            ))
            cur.execute(f"""
                INSERT INTO {{SCHEMA}}.metrics_history
                (array_name, vendor, read_iops, write_iops, read_latency_us, write_latency_us,
                 capacity_total, capacity_used, capacity_used_pct, data_reduction)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                data["array_name"], data["vendor"], data["read_iops"], data["write_iops"],
                data["read_latency_us"], data["write_latency_us"], data["capacity_total"],
                data["capacity_used"], data["capacity_used_pct"], data["data_reduction"],
            ))
        result.records_saved = 1
        return True

    def disconnect(self):
        self.client.disconnect()
'''


def _volumes_template(vendor: str, cls: str) -> str:
    return f'''"""
{vendor} volumes & hosts collector.
"""
from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.{vendor}.client import {cls}Client


@CollectorRegistry.register("{vendor}", "volumes")
class {cls}VolumesCollector(BaseCollector):
    VENDOR = "{vendor}"
    COLLECTOR_TYPE = "volumes"

    def authenticate(self) -> bool:
        self.client = {cls}Client(self.array_config)
        return self.client.authenticate()

    def collect(self) -> dict:
        volumes, hosts = {{}}, {{}}
        for v in self.client.get("volumes").get("records", []):
            volumes[v["name"]] = {{
                "volume_name": v["name"],
                "size": v.get("size", 0),
                "used": v.get("used", 0),
                "data_reduction": v.get("data_reduction", 1.0),
                "snapshots": v.get("snapshot_count", 0),
                "serial": v.get("serial", ""),
                "hosts": v.get("hosts", []),
                "host_groups": [],
                "protection_groups": [],
            }}
        for h in self.client.get("hosts").get("records", []):
            hosts[h["name"]] = {{
                "host_name": h["name"],
                "wwn": h.get("wwn", ""),
                "iqn": h.get("iqn", ""),
                "nqn": h.get("nqn", ""),
                "host_group": h.get("host_group", ""),
                "volumes": h.get("volumes", []),
            }}
        return {{"volumes": volumes, "hosts": hosts}}

    def save(self, data, result: CollectorResult) -> bool:
        # The base class _write_to_cache handles SQLite; add SQL Server writes here.
        result.records_saved = len(data.get("volumes", {{}})) + len(data.get("hosts", {{}}))
        return True

    def disconnect(self):
        self.client.disconnect()
'''


def _alerts_template(vendor: str, cls: str) -> str:
    return f'''"""
{vendor} alerts collector.
"""
from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.{vendor}.client import {cls}Client
from app.db.session import get_db_cursor
from app.core.config import get_settings

SCHEMA = get_settings().db_schema

_SEVERITY_MAP = {{"error": "critical", "warning": "warning", "info": "info"}}


@CollectorRegistry.register("{vendor}", "alerts")
class {cls}AlertsCollector(BaseCollector):
    VENDOR = "{vendor}"
    COLLECTOR_TYPE = "alerts"

    def authenticate(self) -> bool:
        self.client = {cls}Client(self.array_config)
        return self.client.authenticate()

    def collect(self) -> dict:
        messages = []
        for a in self.client.get("alerts").get("records", []):
            messages.append({{
                "message_id": a.get("id", 0),
                "event": a.get("message", ""),
                "severity": _SEVERITY_MAP.get(a.get("severity", "").lower(), "info"),
                "component_type": a.get("component_type", ""),
                "component_name": a.get("component_name", ""),
                "opened": a.get("created", ""),
                "closed": a.get("resolved_at"),
                "resolved": bool(a.get("resolved")),
            }})
        return {{"messages": messages}}

    def save(self, data, result: CollectorResult) -> bool:
        msgs = data.get("messages", [])
        with get_db_cursor() as cur:
            for m in msgs:
                cur.execute(f"""
                    MERGE {{SCHEMA}}.messages AS t
                    USING (SELECT ? AS array_name, ? AS message_id) AS s
                        ON t.array_name = s.array_name AND t.message_id = s.message_id
                    WHEN MATCHED THEN UPDATE SET resolved=?, closed=?
                    WHEN NOT MATCHED THEN INSERT
                        (array_name, vendor, message_id, event, severity,
                         component_type, component_name, opened, closed, resolved)
                        VALUES (?,?,?,?,?,?,?,?,?,?);
                """, (
                    self.array_name, m["message_id"],
                    int(m["resolved"]), m.get("closed"),
                    self.array_name, self.VENDOR, m["message_id"], m["event"], m["severity"],
                    m["component_type"], m["component_name"], m["opened"], m.get("closed"),
                    int(m["resolved"]),
                ))
        result.records_saved = len(msgs)
        return True

    def disconnect(self):
        self.client.disconnect()
'''


@mcp.tool()
def scaffold_vendor(vendor: str, api_type: str = "REST") -> str:
    """
    Generate the 4 collector files for a new vendor under backend/app/collectors/<vendor>/.
    vendor: lowercase key (e.g. 'commvault'). api_type: descriptive (e.g. 'REST', 'WSAPI').
    Does NOT overwrite existing files.
    """
    vendor = vendor.lower().strip()
    if not re.match(r"^[a-z][a-z0-9_]*$", vendor):
        return f"Invalid vendor name '{vendor}'. Use lowercase letters/digits/underscore."
    cls = "".join(p.capitalize() for p in vendor.split("_"))
    vdir = os.path.join(COLLECTORS_DIR, vendor)

    files = {
        "__init__.py": (
            f"from app.collectors.{vendor} import metrics   # noqa: F401\n"
            f"from app.collectors.{vendor} import volumes   # noqa: F401\n"
            f"from app.collectors.{vendor} import alerts    # noqa: F401\n"
        ),
        "client.py": _client_template(vendor, cls, api_type),
        "metrics.py": _metrics_template(vendor, cls),
        "volumes.py": _volumes_template(vendor, cls),
        "alerts.py": _alerts_template(vendor, cls),
    }

    created, skipped = [], []
    for fname, content in files.items():
        path = os.path.join(vdir, fname)
        if os.path.exists(path):
            skipped.append(fname)
        else:
            _write(path, content)
            created.append(fname)

    return (
        f"Scaffolded vendor '{vendor}' (class prefix {cls}, api {api_type}).\n"
        f"Created: {', '.join(created) or 'none'}\n"
        f"Skipped (already exist): {', '.join(skipped) or 'none'}\n"
        f"Location: backend/app/collectors/{vendor}/\n\n"
        f"Next: customize collect()/save(), then add arrays with vendor='{vendor}' "
        f"to managed_arrays. Verify with usm-dev validate_collector."
    )


@mcp.tool()
def validate_collector(vendor: str) -> str:
    """Check that a vendor collector package conforms to the contract."""
    vendor = vendor.lower().strip()
    vdir = os.path.join(COLLECTORS_DIR, vendor)
    if not os.path.isdir(vdir):
        return f"Vendor package '{vendor}' not found."

    issues, ok = [], []
    for ctype in ("metrics", "volumes", "alerts"):
        path = os.path.join(vdir, f"{ctype}.py")
        if not os.path.exists(path):
            issues.append(f"missing {ctype}.py")
            continue
        src = _read(path)
        if f'@CollectorRegistry.register("{vendor}", "{ctype}")' not in src.replace("'", '"'):
            issues.append(f"{ctype}.py missing @CollectorRegistry.register(\"{vendor}\", \"{ctype}\")")
        for method in ("def authenticate", "def collect", "def save"):
            if method not in src:
                issues.append(f"{ctype}.py missing {method}()")
        if f'VENDOR = "{vendor}"' not in src.replace("'", '"'):
            issues.append(f"{ctype}.py missing VENDOR = \"{vendor}\"")
        if not issues:
            ok.append(ctype)

    init_path = os.path.join(vdir, "__init__.py")
    if not os.path.exists(init_path):
        issues.append("missing __init__.py")
    elif "import metrics" not in _read(init_path):
        issues.append("__init__.py does not import collector modules")

    if not issues:
        return f"✅ Vendor '{vendor}' is valid. Collectors: metrics, volumes, alerts."
    return f"⚠️ Vendor '{vendor}' has issues:\n" + "\n".join(f"  - {i}" for i in issues)


@mcp.tool()
def lint_schema() -> str:
    """Cross-check all SQL table references in collectors against the known USM schema."""
    pattern = re.compile(r"\{SCHEMA\}\.(\w+)")
    found = {}
    for path in glob.glob(os.path.join(COLLECTORS_DIR, "**", "*.py"), recursive=True):
        src = _read(path)
        for m in pattern.finditer(src):
            tbl = m.group(1)
            found.setdefault(tbl, set()).add(os.path.relpath(path, REPO_ROOT))

    unknown = {t: files for t, files in found.items() if t not in KNOWN_TABLES}
    lines = [f"Tables referenced in collectors: {', '.join(sorted(found)) or 'none'}"]
    if unknown:
        lines.append("\n⚠️ Unknown tables (not in schema):")
        for t, files in unknown.items():
            lines.append(f"  - {t}  (in {', '.join(sorted(files))})")
    else:
        lines.append("✅ All referenced tables exist in the known schema.")
    return "\n".join(lines)


@mcp.tool()
def check_env() -> str:
    """List all env vars declared in config.py and flag which are currently unset."""
    src = _read(CONFIG_FILE)
    aliases = re.findall(r'alias="([A-Z_]+)"', src)
    lines = ["Environment variables (from config.py):"]
    for a in sorted(set(aliases)):
        present = "set" if os.environ.get(a) else "UNSET"
        lines.append(f"  - {a}: {present}")
    return "\n".join(lines)


@mcp.tool()
def list_endpoints() -> str:
    """List FastAPI v1 route decorators found in the api/v1 modules."""
    pattern = re.compile(r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']')
    lines = []
    for path in sorted(glob.glob(os.path.join(API_DIR, "*.py"))):
        src = _read(path)
        mod = os.path.basename(path)
        for m in pattern.finditer(src):
            lines.append(f"  {m.group(1).upper():6} {m.group(2):40} [{mod}]")
    return "API v1 endpoints:\n" + "\n".join(lines) if lines else "No endpoints found."


if __name__ == "__main__":
    mcp.run()
