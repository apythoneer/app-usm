"""
Report actual time-series coverage on the dev DB for the three history tables
that feed growth/decline analytics. Run inside the usm-backend container.
"""
from app.db.session import get_db_cursor
from app.core.config import get_settings

s = get_settings().db_schema


def q1(cur, sql):
    cur.execute(sql)
    row = cur.fetchone()
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, row)) if row else {}


with get_db_cursor() as c:
    print(f"schema = {s}")
    print(f"HISTORY_RETENTION_DAYS (config) = {get_settings().history_retention_days}")
    print("=" * 70)

    # 1) metrics_history — per-array samples
    m = q1(c, f"""
        SELECT
            MIN(collected_at)                              AS first_seen,
            MAX(collected_at)                              AS last_seen,
            DATEDIFF(DAY, MIN(collected_at), MAX(collected_at)) AS span_days,
            COUNT(*)                                       AS rows_total,
            COUNT(DISTINCT array_name)                     AS arrays,
            COUNT(DISTINCT CAST(collected_at AS DATE))     AS distinct_days
        FROM {s}.metrics_history WITH (NOLOCK)
    """)
    print("metrics_history (per-array raw):")
    for k, v in m.items():
        print(f"   {k:14} = {v}")

    # 2) daily_stats — fleet rollup, 1 row/day
    d = q1(c, f"""
        SELECT
            MIN(stat_date)                                 AS first_day,
            MAX(stat_date)                                 AS last_day,
            DATEDIFF(DAY, MIN(stat_date), MAX(stat_date))  AS span_days,
            COUNT(*)                                       AS rows_total,
            COUNT(DISTINCT stat_date)                      AS distinct_days
        FROM {s}.daily_stats WITH (NOLOCK)
    """)
    print("daily_stats (fleet rollup):")
    for k, v in d.items():
        print(f"   {k:14} = {v}")

    # 3) volumes_history — per-volume snapshots
    v = q1(c, f"""
        SELECT
            MIN(collected_at)                              AS first_seen,
            MAX(collected_at)                              AS last_seen,
            DATEDIFF(DAY, MIN(collected_at), MAX(collected_at)) AS span_days,
            COUNT(*)                                       AS rows_total,
            COUNT(DISTINCT array_name)                     AS arrays,
            COUNT(DISTINCT CAST(collected_at AS DATE))     AS distinct_days
        FROM {s}.volumes_history WITH (NOLOCK)
    """)
    print("volumes_history (per-volume):")
    for k, val in v.items():
        print(f"   {k:14} = {val}")

    print("=" * 70)
    # Histogram: rows per day for metrics_history over last 21 days, so we can
    # see collection cadence / gaps.
    print("metrics_history — rows per day (last 21 days):")
    c.execute(f"""
        SELECT TOP 21 CAST(collected_at AS DATE) AS d,
               COUNT(*) AS rows, COUNT(DISTINCT array_name) AS arrays
        FROM {s}.metrics_history WITH (NOLOCK)
        WHERE collected_at >= DATEADD(DAY, -21, GETDATE())
        GROUP BY CAST(collected_at AS DATE)
        ORDER BY d DESC
    """)
    for r in c.fetchall():
        print(f"   {r[0]}  rows={r[1]:>7}  arrays={r[2]}")
