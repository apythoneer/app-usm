"""One-shot: report time-series depth across USM history tables.

Run on the dev host inside the backend container:
    docker exec -i usm-backend python < _dev_history_depth.py
"""
from app.db.session import get_db_cursor
from app.core.config import get_settings

s = get_settings().db_schema


def columns(table):
    with get_db_cursor() as c:
        c.execute(
            "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{s}' AND TABLE_NAME = '{table}' "
            "ORDER BY ORDINAL_POSITION"
        )
        return c.fetchall()


def pick_date_col(table):
    cols = columns(table)
    names = [c[0] for c in cols]
    for cand in ("collected_at", "captured_at", "snapshot_at", "created_at",
                 "stat_date", "recorded_at", "ts", "timestamp"):
        if cand in names:
            return cand, cols
    # fall back to first datetime-ish column
    for name, dtype in cols:
        if "date" in dtype.lower() or "time" in dtype.lower():
            return name, cols
    return None, cols


def depth(label, table):
    date_col, cols = pick_date_col(table)
    print(f"=== {label} ({table}) ===")
    print("  columns      :", ", ".join(f"{n}:{t}" for n, t in cols))
    if not date_col:
        print("  (no date column found)\n")
        return
    with get_db_cursor() as c:
        c.execute(
            f"SELECT MIN({date_col}), MAX({date_col}), "
            f"COUNT(DISTINCT CAST({date_col} AS DATE)), COUNT(*) "
            f"FROM {s}.{table}"
        )
        r = c.fetchone()
    print(f"  date_col     : {date_col}")
    print(f"  first        : {r[0]}")
    print(f"  last         : {r[1]}")
    print(f"  distinct_days: {r[2]}")
    print(f"  rows         : {r[3]}")
    print()


depth("Fleet daily stats", "daily_stats")
depth("Per-array metrics history", "metrics_history")
depth("Per-volume history", "volumes_history")
