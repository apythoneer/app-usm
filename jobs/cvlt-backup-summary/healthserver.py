#!/usr/bin/env python3
"""
Always-on health/status endpoint for the cvlt-backup-summary service.

The nightly ETL runs as a CronJob; this long-running sidecar Deployment gives a
live surface to validate the service's health, its connections (Commvault,
Postgres, Datadog), and uptime. Stdlib only (plus psycopg2/requests already in
the image) to keep this separate service tiny.

Endpoints:
  GET /ping     -> 200 {"ok": true}         liveness (no dependency calls)
  GET /health   -> 200 if healthy else 503  readiness: live connection checks
  GET /status   -> 200 full JSON (checks + last run + uptime)
  GET /         -> HTML status page (the "service header")
"""

import json
import os
import socket
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

START = time.monotonic()
STARTED_AT = datetime.now(timezone.utc)
VERSION = os.environ.get("SERVICE_VERSION", "0.1.0")
PORT = int(os.environ.get("HEALTH_PORT", "8080"))

COMMVAULT_HOST = "uscomvlt1.corp.intranet"
DATADOG_VALIDATE = "https://api.us5.datadoghq.com/api/v1/validate"


def _load_secrets():
    with open("/home/cvadmin/.var/secrets.json", "r") as f:
        return json.load(f)


def _uptime_seconds():
    return round(time.monotonic() - START, 1)


# --------------------------------------------------------------- dependency checks
def check_commvault():
    """TCP reachability to the Commvault Command Center (443)."""
    t0 = time.monotonic()
    try:
        socket.create_connection((COMMVAULT_HOST, 443), timeout=4).close()
        return {"ok": True, "detail": f"TCP {COMMVAULT_HOST}:443 open", "ms": _ms(t0)}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "ms": _ms(t0)}


def check_postgres(secrets):
    """Connect + SELECT 1 against the AWS RDS stormart DB."""
    t0 = time.monotonic()
    try:
        import psycopg2
        pg = secrets["postgres"]
        conn = psycopg2.connect(
            host=pg["host"], port=pg["port"], dbname=pg["database"],
            user=pg["user"], password=pg["password"], connect_timeout=4,
        )
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            conn.close()
        return {"ok": True, "detail": f"SELECT 1 on {pg['host']}/{pg['database']}", "ms": _ms(t0)}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "ms": _ms(t0)}


def check_datadog(secrets):
    """Validate the Datadog API key (also proves SaaS egress)."""
    t0 = time.monotonic()
    try:
        key = secrets["datadog"]["api_key"]
        r = requests.get(DATADOG_VALIDATE, headers={"DD-API-KEY": key}, timeout=5)
        valid = r.status_code == 200 and r.json().get("valid") is True
        return {"ok": valid, "detail": f"HTTP {r.status_code}, valid={valid}", "ms": _ms(t0)}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "ms": _ms(t0)}


def last_run(secrets):
    """Most recent recorded ETL run (written by run_job.py)."""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        pg = secrets["postgres"]
        conn = psycopg2.connect(
            host=pg["host"], port=pg["port"], dbname=pg["database"],
            user=pg["user"], password=pg["password"], connect_timeout=4,
        )
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT started_at, finished_at, status, error, host,
                       EXTRACT(EPOCH FROM (finished_at - started_at)) AS duration_s
                FROM stormart.cvlt_job_runs
                ORDER BY started_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()
            cur.execute("SELECT count(*) AS n FROM stormart.cvlt_backupjobsummary")
            total = cur.fetchone()["n"]
        finally:
            conn.close()
        if not row:
            return {"status": "unknown", "detail": "no runs recorded yet", "summary_rows": total}
        return {
            "status": row["status"],
            "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["finished_at"]),
            "duration_s": round(row["duration_s"], 1) if row["duration_s"] else None,
            "error": row["error"],
            "host": row["host"],
            "summary_rows": total,
        }
    except Exception as e:
        return {"status": "unknown", "detail": f"{type(e).__name__}: {e}"}


def _ms(t0):
    return round((time.monotonic() - t0) * 1000)


def _iso(dt):
    return dt.isoformat() if dt else None


# --------------------------------------------------------------- aggregate
def build_status():
    try:
        secrets = _load_secrets()
    except Exception as e:
        return False, {"error": f"secrets not mounted: {type(e).__name__}: {e}"}

    checks = {
        "commvault": check_commvault(),       # critical (source)
        "postgres": check_postgres(secrets),  # critical (sink)
        "datadog": check_datadog(secrets),    # non-critical (eventing)
    }
    # Healthy requires both critical connections; Datadog down => degraded, not down.
    critical_ok = checks["commvault"]["ok"] and checks["postgres"]["ok"]
    if critical_ok and checks["datadog"]["ok"]:
        state = "healthy"
    elif critical_ok:
        state = "degraded"
    else:
        state = "unhealthy"

    return critical_ok, {
        "service": "cvlt-backup-summary",
        "version": VERSION,
        "state": state,
        "uptime_seconds": _uptime_seconds(),
        "started_at": STARTED_AT.isoformat(),
        "now": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "last_run": last_run(secrets),
    }


# --------------------------------------------------------------- HTTP
PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>cvlt-backup-summary status</title>
<style>
:root{{--bg:#f6f7f9;--fg:#1a1d21;--card:#fff;--line:#e3e6ea;--muted:#5b636b;
--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e}}
@media(prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#0d1117;--fg:#e6edf3;
--card:#161b22;--line:#30363d;--muted:#8b949e;--ok:#3fb950;--warn:#d29922;--bad:#f85149}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:16px}}
.wrap{{max-width:720px;margin:0 auto}}
.hdr{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}}
.dot{{width:14px;height:14px;border-radius:50%}}
h1{{font-size:18px;margin:0}}.sub{{color:var(--muted);font-size:13px}}
.badge{{font-weight:600;padding:3px 10px;border-radius:999px;font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin-bottom:12px}}
.row{{display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px solid var(--line)}}
.row:last-child{{border-bottom:0}}.k{{color:var(--muted)}}.v{{text-align:right;word-break:break-word}}
.ok{{color:var(--ok)}}.warn{{color:var(--warn)}}.bad{{color:var(--bad)}}
code{{font:13px ui-monospace,Menlo,Consolas,monospace}}
</style></head><body><div class="wrap" id="app">loading…</div>
<script>
const C={{healthy:'ok',degraded:'warn',unhealthy:'bad',unknown:'warn'}};
function dur(s){{if(s==null)return '—';if(s<90)return s+'s';if(s<5400)return (s/60|0)+'m';return (s/3600).toFixed(1)+'h';}}
async function load(){{
 let d; try{{d=await (await fetch('/status',{{cache:'no-store'}})).json();}}catch(e){{document.getElementById('app').textContent='unreachable';return;}}
 const c=C[d.state]||'warn';
 const chk=Object.entries(d.checks||{{}}).map(([n,v])=>
  `<div class="row"><span class="k">${{n}}</span><span class="v ${{v.ok?'ok':'bad'}}">${{v.ok?'OK':'FAIL'}} · ${{v.ms}}ms<br><span class="sub">${{v.detail||''}}</span></span></div>`).join('');
 const lr=d.last_run||{{}};
 document.getElementById('app').innerHTML=
 `<div class="hdr"><span class="dot ${{c}}" style="background:var(--${{c}})"></span>
   <h1>cvlt-backup-summary</h1>
   <span class="badge ${{c}}" style="background:var(--${{c}});color:#fff">${{(d.state||'').toUpperCase()}}</span>
   <span class="sub">v${{d.version}} · uptime ${{dur(d.uptime_seconds)}}</span></div>
  <div class="card"><div class="row"><span class="k">connections</span><span class="v"></span></div>${{chk}}</div>
  <div class="card">
   <div class="row"><span class="k">last run</span><span class="v ${{lr.status==='success'?'ok':(lr.status==='failed'?'bad':'warn')}}">${{(lr.status||'unknown')}}</span></div>
   <div class="row"><span class="k">finished</span><span class="v">${{lr.finished_at||lr.detail||'—'}}</span></div>
   <div class="row"><span class="k">duration</span><span class="v">${{dur(lr.duration_s)}}</span></div>
   <div class="row"><span class="k">summary rows</span><span class="v">${{lr.summary_rows??'—'}}</span></div>
   ${{lr.error?`<div class="row"><span class="k">error</span><span class="v bad"><code>${{lr.error}}</code></span></div>`:''}}
  </div>
  <p class="sub">checked ${{d.now}} · <a href="/status">/status</a> · <a href="/health">/health</a></p>`;
}}
load();setInterval(load,15000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/ping":
            self._send(200, json.dumps({"ok": True}))
        elif path in ("/health", "/status"):
            ok, status = build_status()
            code = 200 if (path == "/status" or ok) else 503
            self._send(code, json.dumps(status, indent=2))
        elif path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *a):  # quiet access log (probes every few seconds)
        pass


def main():
    print(f"cvlt-backup-summary health server v{VERSION} on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
