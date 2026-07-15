"""One-shot patch: add server-side growth projection + last_collected to /daily-trend.

Run from repo root:  python scripts/_patch_daily_trend.py
Idempotent: re-running detects the marker and does nothing.
"""
import io
import sys
from pathlib import Path

TARGET = Path("backend/app/api/v1/analytics.py")

OLD = '''def _fetch_daily_trend(days: int) -> List[dict]:
    """
    Fleet capacity over time, ascending by date, for charting.
    Reads pre-aggregated daily_stats (cheap — one row per day).
    """
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""SELECT TOP {days}
                stat_date,
                total_arrays,
                total_capacity_tb,
                total_used_tb,
                avg_utilization_pct,
                avg_data_reduction
            FROM {SCHEMA}.daily_stats WITH (NOLOCK)
            ORDER BY stat_date DESC""",
        )
        rows = rows_to_dicts(cursor, cursor.fetchall())

    # Return ascending for left-to-right time-series rendering.
    out: List[dict] = []
    for r in reversed(rows):
        out.append({
            "date": str(r.get("stat_date")),
            "total_capacity_tb": round(float(r.get("total_capacity_tb") or 0), 2),
            "total_used_tb": round(float(r.get("total_used_tb") or 0), 2),
            "avg_utilization_pct": round(float(r.get("avg_utilization_pct") or 0), 1),
            "avg_data_reduction": round(float(r.get("avg_data_reduction") or 0), 2),
            "total_arrays": int(r.get("total_arrays") or 0),
        })
    return out


@router.get("/daily-trend")
async def get_daily_trend(days: int = Query(default=90, ge=1, le=365)):
    """
    Fleet capacity time-series for charting: total usable/used TB and average
    utilization per day, ascending by date. Backed by the daily_stats table.
    """
    rows = await run_in_threadpool(_fetch_daily_trend, days)
    return {"days": days, "data_points": len(rows), "data": rows}'''

NEW = '''def _linreg_slope(ys: List[float]) -> float:
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


# Below this average daily change (TB/day) the fleet is treated as "stable"
# so the projected-full date doesn't flip wildly on near-flat windows.
_STABLE_SLOPE_TB_PER_DAY = 0.01


def _fetch_daily_trend(days: int) -> dict:
    """
    Fleet capacity over time, ascending by date, for charting.
    Reads pre-aggregated daily_stats (cheap — one row per day).

    Also computes a server-side growth projection so the UI shows a single,
    consistent "projected full" value rather than recomputing it client-side,
    plus the latest collected_at so the UI can surface data freshness/gaps.
    """
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""SELECT TOP {days}
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

    # Return ascending for left-to-right time-series rendering.
    out: List[dict] = []
    last_collected = None
    for r in reversed(rows):
        if r.get("collected_at") is not None:
            last_collected = r.get("collected_at")
        out.append({
            "date": str(r.get("stat_date")),
            "total_capacity_tb": round(float(r.get("total_capacity_tb") or 0), 2),
            "total_used_tb": round(float(r.get("total_used_tb") or 0), 2),
            "avg_utilization_pct": round(float(r.get("avg_utilization_pct") or 0), 1),
            "avg_data_reduction": round(float(r.get("avg_data_reduction") or 0), 2),
            "total_arrays": int(r.get("total_arrays") or 0),
        })

    # -- Growth projection (server-authoritative) ------------------------------
    used = [p["total_used_tb"] for p in out]
    projection = {
        "span_days": max(len(out) - 1, 0),
        "net_change_tb": 0.0,
        "net_change_pct": None,
        "avg_rate_tb_per_day": 0.0,
        "headroom_tb": 0.0,
        "usable_tb": out[-1]["total_capacity_tb"] if out else 0.0,
        "days_to_full": None,
        "projected_full_date": None,
        "trend": "stable",  # growing | declining | stable
    }
    if len(out) >= 2:
        first_used = used[0]
        last_used = used[-1]
        delta = round(last_used - first_used, 2)
        slope = _linreg_slope(used)  # TB/day
        usable = out[-1]["total_capacity_tb"]
        headroom = max(usable - last_used, 0.0)

        projection["net_change_tb"] = delta
        projection["net_change_pct"] = (
            round(delta / first_used * 100, 1) if first_used > 0 else None
        )
        projection["avg_rate_tb_per_day"] = round(slope, 3)
        projection["headroom_tb"] = round(headroom, 2)
        projection["usable_tb"] = round(usable, 2)

        if slope > _STABLE_SLOPE_TB_PER_DAY:
            projection["trend"] = "growing"
            days_to_full = int(round(headroom / slope)) if slope > 0 else None
            projection["days_to_full"] = days_to_full
            if days_to_full is not None:
                full_dt = datetime.now() + timedelta(days=days_to_full)
                projection["projected_full_date"] = full_dt.strftime("%Y-%m-%d")
        elif slope < -_STABLE_SLOPE_TB_PER_DAY:
            projection["trend"] = "declining"
        else:
            projection["trend"] = "stable"

    return {
        "data": out,
        "last_collected": str(last_collected) if last_collected else None,
        "projection": projection,
    }


@router.get("/daily-trend")
async def get_daily_trend(days: int = Query(default=90, ge=1, le=365)):
    """
    Fleet capacity time-series for charting: total usable/used TB and average
    utilization per day, ascending by date. Backed by the daily_stats table.

    Includes a server-computed `projection` (net change, avg rate, headroom,
    projected-full date, trend classification) plus `last_collected` so the UI
    can render a single consistent growth summary and a freshness badge.
    """
    result = await run_in_threadpool(_fetch_daily_trend, days)
    return {
        "days": days,
        "data_points": len(result["data"]),
        "data": result["data"],
        "last_collected": result["last_collected"],
        "projection": result["projection"],
    }'''


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    if '"projection": projection,' in text and "_linreg_slope" in text:
        print("Already patched. Nothing to do.")
        return 0
    if OLD not in text:
        print("ERROR: could not find the original _fetch_daily_trend block to replace.")
        return 1
    TARGET.write_text(text.replace(OLD, NEW), encoding="utf-8")
    print("Patched _fetch_daily_trend + /daily-trend successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
