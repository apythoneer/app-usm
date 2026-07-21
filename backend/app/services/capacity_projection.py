"""
Shared fleet capacity growth-projection logic.

Extracted from app/api/v1/analytics.py so both the /daily-trend API endpoint
AND the capacity alerting service (app/services/capacity_alerts.py) compute
the exact same projection — one source of truth for "trend" / "days_to_full".
"""

from datetime import datetime, timedelta
from typing import List

from app.db.session import get_db_cursor, rows_to_dicts
from app.core.config import get_settings

settings = get_settings()
SCHEMA = settings.db_schema

# Below this average daily change (TB/day) the fleet is treated as "stable"
# so the projected-full date doesn't flip wildly on near-flat windows.
STABLE_SLOPE_TB_PER_DAY = 0.01

# The forward projection (avg rate, days-to-full, projected-full date) is always
# fit over this trailing window, INDEPENDENT of the chart's selected range. Fitting
# the slope over a tiny 7d/14d window made "Projected Full" swing wildly (a recent
# daily spike would halve the days-to-full), so the number changed every time the
# range button changed. A fixed 90d basis keeps it stable and robust to spikes.
PROJECTION_WINDOW_DAYS = 90


def linreg_slope(ys: List[float]) -> float:
    """Least-squares slope of y over index 0..n-1 -> average units per step (day)."""
    n = len(ys)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(ys) / n
    num = den = 0.0
    for i, y in enumerate(ys):
        num += (i - x_mean) * (y - y_mean)
        den += (i - x_mean) * (i - x_mean)
    return num / den if den else 0.0


def compute_daily_trend(days: int) -> dict:
    """
    Fleet capacity over time, ascending by date, plus a server-authoritative
    growth projection (net change, avg rate, headroom, projected-full date,
    trend classification) and the latest collected_at for freshness checks.

    Reads pre-aggregated daily_stats (cheap — one row per day).

    `data` covers the requested `days` (for the chart). The `projection` is fit over
    a fixed PROJECTION_WINDOW_DAYS trailing window regardless of `days`, so the
    forward-looking numbers stay stable when the caller changes the chart range.
    """
    # Fetch enough rows for both the chart window and the projection window in one
    # round-trip (daily_stats is one row/day, so this is tiny).
    fetch_n = max(days, PROJECTION_WINDOW_DAYS)
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""SELECT TOP {fetch_n}
                stat_date,
                total_arrays,
                total_capacity_tb,
                total_used_tb,
                avg_utilization_pct,
                avg_data_reduction,
                collected_at
            FROM {SCHEMA}.daily_stats WITH (NOLOCK)
            ORDER BY stat_date DESC""",
        )
        rows = rows_to_dicts(cursor, cursor.fetchall())

    full: List[dict] = []
    last_collected = None
    for r in reversed(rows):  # ascending by date
        if r.get("collected_at") is not None:
            last_collected = r.get("collected_at")
        full.append({
            "date": str(r.get("stat_date")),
            "total_capacity_tb": round(float(r.get("total_capacity_tb") or 0), 2),
            "total_used_tb": round(float(r.get("total_used_tb") or 0), 2),
            "avg_utilization_pct": round(float(r.get("avg_utilization_pct") or 0), 1),
            "avg_data_reduction": round(float(r.get("avg_data_reduction") or 0), 2),
            "total_arrays": int(r.get("total_arrays") or 0),
        })

    # Chart data = the requested window (most recent `days` points).
    out = full[-days:] if days < len(full) else full
    # Projection basis = the most recent PROJECTION_WINDOW_DAYS points (stable).
    proj_points = full[-PROJECTION_WINDOW_DAYS:]
    proj_used = [p["total_used_tb"] for p in proj_points]

    projection = {
        "span_days": max(len(proj_points) - 1, 0),
        "window_days": max(len(proj_points) - 1, 0),  # basis for the forward projection
        "net_change_tb": 0.0,
        "net_change_pct": None,
        "avg_rate_tb_per_day": 0.0,
        "headroom_tb": 0.0,
        "usable_tb": full[-1]["total_capacity_tb"] if full else 0.0,
        "days_to_full": None,
        "projected_full_date": None,
        "trend": "stable",  # growing | declining | stable
    }
    if len(proj_points) >= 2:
        first_used = proj_used[0]
        last_used = proj_used[-1]
        delta = round(last_used - first_used, 2)
        slope = linreg_slope(proj_used)  # TB/day, fit over the stable window
        usable = full[-1]["total_capacity_tb"]
        headroom = max(usable - last_used, 0.0)

        projection["net_change_tb"] = delta
        projection["net_change_pct"] = (
            round(delta / first_used * 100, 1) if first_used > 0 else None
        )
        projection["avg_rate_tb_per_day"] = round(slope, 3)
        projection["headroom_tb"] = round(headroom, 2)
        projection["usable_tb"] = round(usable, 2)

        if slope > STABLE_SLOPE_TB_PER_DAY:
            projection["trend"] = "growing"
            days_to_full = int(round(headroom / slope)) if slope > 0 else None
            projection["days_to_full"] = days_to_full
            if days_to_full is not None:
                full_dt = datetime.now() + timedelta(days=days_to_full)
                projection["projected_full_date"] = full_dt.strftime("%Y-%m-%d")
        elif slope < -STABLE_SLOPE_TB_PER_DAY:
            projection["trend"] = "declining"
        else:
            projection["trend"] = "stable"

    return {
        "data": out,
        "last_collected": str(last_collected) if last_collected else None,
        "projection": projection,
    }
