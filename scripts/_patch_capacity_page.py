"""One-shot patch: Capacity page growth summary uses server-side projection,
default window widened to 90d, adds a data-freshness badge.

Run from repo root:  python scripts/_patch_capacity_page.py
Idempotent (guards on a marker).
"""
import sys
from pathlib import Path

TARGET = Path("frontend/src/pages/Capacity.tsx")

# 1) Widen default window (14 -> 90). We have ~134 days of daily_stats.
OLD_DEFAULT = "const DEFAULT_TREND_DAYS = 14"
NEW_DEFAULT = "const DEFAULT_TREND_DAYS = 90"

# 2) Replace the client-side projection derivation with server projection.
OLD_DERIVE = """  const points = data?.data ?? []

  // Derive growth metrics from the daily used-capacity series.
  const used = points.map((p) => p.total_used_tb)
  const first = used.length ? used[0] : 0
  const last = used.length ? used[used.length - 1] : 0
  const deltaTb = last - first
  const deltaPct = first > 0 ? (deltaTb / first) * 100 : null
  const spanDays = points.length > 1 ? points.length - 1 : 0
  const perDay = spanDays > 0 ? linregSlope(used) : 0  // robust avg TB/day
  const growing = deltaTb >= 0

  // Headroom + naive projection to full (only meaningful while growing).
  const lastUsable = points.length ? points[points.length - 1].total_capacity_tb : 0
  const headroomTb = Math.max(lastUsable - last, 0)
  const daysToFull = perDay > 0.0001 ? Math.round(headroomTb / perDay) : null"""

NEW_DERIVE = """  const points = data?.data ?? []

  // Prefer the server-authoritative projection so the growth summary and the
  // "Projected Full" stat are consistent everywhere. Fall back to a local
  // derivation only if the API hasn't been upgraded yet.
  const proj = data?.projection
  const used = points.map((p) => p.total_used_tb)
  const first = used.length ? used[0] : 0
  const last = used.length ? used[used.length - 1] : 0
  const deltaTb = proj ? proj.net_change_tb : last - first
  const deltaPct = proj ? proj.net_change_pct : first > 0 ? (deltaTb / first) * 100 : null
  const spanDays = proj ? proj.span_days : points.length > 1 ? points.length - 1 : 0
  const perDay = proj ? proj.avg_rate_tb_per_day : spanDays > 0 ? linregSlope(used) : 0
  const trend = proj ? proj.trend : deltaTb > 0.01 ? 'growing' : deltaTb < -0.01 ? 'declining' : 'stable'
  const growing = trend === 'growing'

  const lastUsable = proj ? proj.usable_tb : points.length ? points[points.length - 1].total_capacity_tb : 0
  const headroomTb = proj ? proj.headroom_tb : Math.max(lastUsable - last, 0)
  const daysToFull = proj ? proj.days_to_full : perDay > 0.0001 ? Math.round(headroomTb / perDay) : null
  const projectedFullDate = proj ? proj.projected_full_date : null
  const lastCollected = data?.last_collected ?? null"""

# 3) Replace the Projected Full stat to show a calendar date + tri-state trend.
OLD_PROJ_STAT = """            <GrowthStat label="Free Headroom" value={tb(headroomTb)} sub={`of ${tb(lastUsable)} usable`} />
            <GrowthStat
              label="Projected Full"
              value={daysToFull != null ? `~${daysToFull}d` : '—'}
              sub={daysToFull != null ? 'at current rate' : (perDay <= 0 ? 'not growing' : 'n/a')}
              tone={daysToFull != null && daysToFull < 90 ? 'down' : 'neutral'}
            />"""

NEW_PROJ_STAT = """            <GrowthStat label="Free Headroom" value={tb(headroomTb)} sub={`of ${tb(lastUsable)} usable`} />
            <GrowthStat
              label="Projected Full"
              value={
                daysToFull != null
                  ? `~${daysToFull}d`
                  : trend === 'declining'
                  ? 'N/A'
                  : trend === 'stable'
                  ? 'N/A'
                  : '—'
              }
              sub={
                daysToFull != null
                  ? projectedFullDate
                    ? `by ${trendDate(projectedFullDate)}`
                    : 'at current rate'
                  : trend === 'declining'
                  ? 'reclaiming — not filling'
                  : 'stable — not filling'
              }
              tone={daysToFull != null && daysToFull < 90 ? 'down' : daysToFull != null && daysToFull < 180 ? 'neutral' : 'neutral'}
            />"""

# 4) Add a freshness badge next to the date range subtitle.
OLD_SUBTITLE = """          {points.length > 0 && (
            <p className="text-xs text-gray-500 mt-0.5">
              {trendDate(points[0].date)} → {trendDate(points[points.length - 1].date)} · {points.length} daily points
            </p>
          )}"""

NEW_SUBTITLE = """          {points.length > 0 && (
            <p className="text-xs text-gray-500 mt-0.5">
              {trendDate(points[0].date)} → {trendDate(points[points.length - 1].date)} · {points.length} daily points
              {lastCollected && (
                <span className="ml-2 text-gray-600">· updated {trendDate(lastCollected)}</span>
              )}
            </p>
          )}"""


PATCHES = [
    ("DEFAULT_TREND_DAYS", OLD_DEFAULT, NEW_DEFAULT),
    ("growth derivation", OLD_DERIVE, NEW_DERIVE),
    ("projected-full stat", OLD_PROJ_STAT, NEW_PROJ_STAT),
    ("freshness badge", OLD_SUBTITLE, NEW_SUBTITLE),
]


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    if "projectedFullDate" in text and "DEFAULT_TREND_DAYS = 90" in text:
        print("Already patched. Nothing to do.")
        return 0
    failed = []
    for name, old, new in PATCHES:
        if old in text:
            text = text.replace(old, new, 1)
        elif new.split("\n")[0] in text:
            pass  # this fragment already applied
        else:
            failed.append(name)
    if failed:
        print("ERROR: could not find blocks for: " + ", ".join(failed))
        return 1
    TARGET.write_text(text, encoding="utf-8")
    print("Patched Capacity.tsx successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
