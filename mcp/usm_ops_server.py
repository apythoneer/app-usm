#!/usr/bin/env python3
"""
usm-ops MCP Server
==================
Live operations tools that talk to a running USM backend over HTTP.
Lets an AI agent inspect fleet health, scheduler status, and alerts without
manual curl commands.

Set USM_API_BASE to point at the instance (default the sandbox host).

Tools:
  - fleet_status()                : summary of arrays / capacity / health
  - list_arrays(vendor?, status?) : list managed arrays with filters
  - array_detail(name)            : metrics for one array
  - scheduler_status()            : APScheduler job status
  - run_job(job_id)               : trigger a collector job now
  - recent_alerts(severity?, n?)  : recent alerts
  - health()                      : backend /health probe

Run:
  python mcp/usm_ops_server.py
"""

import os
import json
import httpx
from mcp.server.fastmcp import FastMCP

API_BASE = os.environ.get("USM_API_BASE", "http://usodclpsandadm1.corp.intranet:8000").rstrip("/")
V1 = f"{API_BASE}/api/v1"
TIMEOUT = float(os.environ.get("USM_API_TIMEOUT", "30"))

mcp = FastMCP("usm-ops")


def _get(path: str, params: dict = None):
    try:
        with httpx.Client(timeout=TIMEOUT, verify=False) as c:
            r = c.get(path, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "url": path}


def _post(path: str, payload: dict = None):
    try:
        with httpx.Client(timeout=TIMEOUT, verify=False) as c:
            r = c.post(path, json=payload or {})
            r.raise_for_status()
            return r.json() if r.content else {"ok": True}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "url": path}


def _fmt(obj) -> str:
    return json.dumps(obj, indent=2, default=str)


@mcp.tool()
def health() -> str:
    """Probe the USM backend /health endpoint (DB, cache, collectors, scheduler)."""
    return _fmt(_get(f"{API_BASE}/health"))


@mcp.tool()
def fleet_status() -> str:
    """High-level fleet summary: array counts by vendor/health, total/used capacity."""
    arrays = _get(f"{V1}/arrays")
    if isinstance(arrays, dict) and arrays.get("error"):
        return _fmt(arrays)
    items = arrays if isinstance(arrays, list) else arrays.get("items", arrays.get("arrays", []))
    by_vendor, by_status = {}, {}
    total_cap = used_cap = 0
    for a in items:
        by_vendor[a.get("vendor", "?")] = by_vendor.get(a.get("vendor", "?"), 0) + 1
        st = a.get("array_status") or a.get("monitoring_status") or "unknown"
        by_status[st] = by_status.get(st, 0) + 1
        total_cap += a.get("capacity_total", 0) or 0
        used_cap += a.get("capacity_used", 0) or 0
    summary = {
        "total_arrays": len(items),
        "by_vendor": by_vendor,
        "by_status": by_status,
        "total_capacity_tb": round(total_cap / (1024 ** 4), 2),
        "used_capacity_tb": round(used_cap / (1024 ** 4), 2),
        "used_pct": round(used_cap / total_cap * 100, 1) if total_cap else 0,
    }
    return _fmt(summary)


@mcp.tool()
def list_arrays(vendor: str = "", status: str = "") -> str:
    """List managed arrays. Optional filters: vendor (e.g. 'pure'), status (e.g. 'critical')."""
    params = {}
    if vendor:
        params["vendor"] = vendor
    if status:
        params["status"] = status
    return _fmt(_get(f"{V1}/arrays", params or None))


@mcp.tool()
def array_detail(name: str) -> str:
    """Return current metrics and details for a single array by name."""
    return _fmt(_get(f"{V1}/arrays/{name}"))


@mcp.tool()
def scheduler_status() -> str:
    """Return APScheduler job status: last run, next run, duration, success/error counts."""
    return _fmt(_get(f"{V1}/scheduler/status"))


@mcp.tool()
def run_job(job_id: str) -> str:
    """Trigger a collector job immediately. job_id e.g. 'pure_metrics', 'dell_alerts'."""
    return _fmt(_post(f"{V1}/scheduler/jobs/{job_id}/run"))


@mcp.tool()
def recent_alerts(severity: str = "", limit: int = 20) -> str:
    """List recent alerts. Optional severity filter (critical/warning/info)."""
    params = {"limit": limit}
    if severity:
        params["severity"] = severity
    return _fmt(_get(f"{V1}/alerts", params))


@mcp.tool()
def test_teams_alert() -> str:
    """Send a test notification through the configured Teams webhook."""
    return _fmt(_post(f"{V1}/settings/notifications/test"))


if __name__ == "__main__":
    mcp.run()
