#!/usr/bin/env python3
"""
Thin run-wrapper for the nightly ETL. It does NOT change PJ's logic — it imports
dataDogIncidentManagement.main() and records each run in stormart.cvlt_job_runs so
the health server can report last-run status / duration / errors.

The CronJob runs this; the exit code still reflects the real outcome (re-raises on
failure) so a failed run shows as a failed Job in Kubernetes.
"""

import socket
import sys
import traceback

import dataDogIncidentManagement as job  # importing runs load_secrets() at module level

DDL = """
CREATE TABLE IF NOT EXISTS stormart.cvlt_job_runs (
    id          bigserial PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'running',   -- running | success | failed
    error       text,
    host        text
)
"""


def _conn():
    return job.get_db_connection()


def _record_start():
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(DDL)
        cur.execute(
            "INSERT INTO stormart.cvlt_job_runs (status, host) VALUES ('running', %s) RETURNING id",
            (socket.gethostname(),),
        )
        run_id = cur.fetchone()[0]
        conn.commit()
        return run_id
    finally:
        conn.close()


def _record_end(run_id, status, error=None):
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE stormart.cvlt_job_runs SET finished_at = now(), status = %s, error = %s WHERE id = %s",
            (status, error, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def main():
    run_id = None
    try:
        run_id = _record_start()
    except Exception as e:
        # Don't let run-tracking failures block the actual ETL.
        print(f"WARN: could not record run start: {e}", flush=True)

    try:
        job.main()
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        if run_id is not None:
            try:
                _record_end(run_id, "failed", err[-4000:])
            except Exception as e:
                print(f"WARN: could not record failure: {e}", flush=True)
        sys.exit(1)

    if run_id is not None:
        try:
            _record_end(run_id, "success")
        except Exception as e:
            print(f"WARN: could not record success: {e}", flush=True)


if __name__ == "__main__":
    main()
