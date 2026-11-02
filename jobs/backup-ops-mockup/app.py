#!/usr/bin/env python3
"""
Backup Operations — read-only web app for the SIP showcase.

Serves one static page plus a read-only JSON API over
stormart.cvlt_backupjobsummary (the table the cvlt-backup-summary pipeline
loads). Runs in the prod namespace next to that pipeline so it shares the proven
Postgres network path + credentials. It only ever issues SELECTs.

Stdlib HTTP server + psycopg2 (already a tiny dependency) — no web framework, to
keep this separate service small.

Endpoints:
  GET /          -> the Backup Operations page (index.html)
  GET /healthz   -> 200 liveness (no DB call)
  GET /api/jobs  -> JSON: aggregates + recent rows for the current filters.
                    query: window (days, 1..90, default 1), client (substring),
                           status (comma-separated job_status), limit (rows)
"""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PORT", "8080"))
SECRETS_PATH = os.environ.get("SECRETS_PATH", "/var/secrets/secrets.json")
HERE = os.path.dirname(os.path.abspath(__file__))
ROW_CAP = 500
TABLE = "stormart.cvlt_backupjobsummary"


def _pg():
    with open(SECRETS_PATH, "r") as f:
        return json.load(f)["postgres"]


def _connect():
    import psycopg2
    pg = _pg()
    return psycopg2.connect(
        host=pg["host"], port=pg["port"], dbname=pg["database"],
        user=pg["user"], password=pg["password"], connect_timeout=6,
    )


def _jsonable(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def query_jobs(window_days, client, statuses, limit):
    """All reads are parameterized; the window/limit are clamped by the caller."""
    from psycopg2.extras import RealDictCursor
    where = ["start_time >= now() - make_interval(days => %s)"]
    params = [window_days]
    if client:
        where.append("client_name ILIKE %s")
        params.append("%" + client + "%")
    if statuses:
        where.append("job_status = ANY(%s)")
        params.append(statuses)
    w = " AND ".join(where)

    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT count(*) AS total, count(DISTINCT client_name) AS clients, "
            "COALESCE(sum(size_of_application_gb), 0) AS data_gb "
            "FROM " + TABLE + " WHERE " + w, params)
        agg = cur.fetchone()

        cur.execute(
            "SELECT job_status, count(*) AS n FROM " + TABLE + " WHERE " + w +
            " GROUP BY job_status ORDER BY n DESC", params)
        by_status = cur.fetchall()

        cur.execute(
            "SELECT workload, count(*) AS n, "
            "count(*) FILTER (WHERE job_status = 'Failed') AS failed "
            "FROM " + TABLE + " WHERE " + w +
            " GROUP BY workload ORDER BY n DESC LIMIT 8", params)
        by_workload = cur.fetchall()

        cur.execute(
            "SELECT job_id, client_name, workload, agent, subclient, start_time, "
            "end_time, duration_mins, size_of_application_gb, job_status, failure_reason "
            "FROM " + TABLE + " WHERE " + w +
            " ORDER BY start_time DESC LIMIT %s", params + [limit])
        rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "total": agg["total"],
        "clients": agg["clients"],
        "data_gb": agg["data_gb"],
        "by_status": {r["job_status"]: r["n"] for r in by_status},
        "by_workload": by_workload,
        "rows": rows,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/healthz":
            return self._send(200, "ok\n", "text/plain")
        if u.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception:
                return self._send(500, "page unavailable", "text/plain")
        if u.path == "/api/jobs":
            q = parse_qs(u.query)
            try:
                window = max(1, min(90, int(q.get("window", ["1"])[0])))
            except ValueError:
                window = 1
            client = (q.get("client", [""])[0] or "").strip()[:80]
            statuses = [s for s in (q.get("status", [""])[0] or "").split(",") if s][:12]
            try:
                limit = max(1, min(ROW_CAP, int(q.get("limit", ["300"])[0])))
            except ValueError:
                limit = 300
            try:
                data = query_jobs(window, client, statuses, limit)
                return self._send(200, json.dumps(data, default=_jsonable))
            except Exception as e:
                return self._send(502, json.dumps({"error": "%s: %s" % (type(e).__name__, e)}))
        return self._send(404, json.dumps({"error": "not found"}))

    do_HEAD = do_GET


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("backup-ops listening on :%d" % PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
