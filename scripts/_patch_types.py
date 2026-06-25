"""One-shot patch: add DailyTrendProjection + projection/last_collected to types.ts.

Run from repo root:  python scripts/_patch_types.py
Idempotent.
"""
import sys
from pathlib import Path

TARGET = Path("frontend/src/api/types.ts")

OLD = """export interface DailyTrendResponse {
  days: number
  data_points: number
  data: DailyTrendPoint[]
}"""

NEW = """export interface DailyTrendProjection {
  span_days: number
  net_change_tb: number
  net_change_pct: number | null
  avg_rate_tb_per_day: number
  headroom_tb: number
  usable_tb: number
  days_to_full: number | null
  projected_full_date: string | null
  trend: 'growing' | 'declining' | 'stable'
}

export interface DailyTrendResponse {
  days: number
  data_points: number
  data: DailyTrendPoint[]
  last_collected?: string | null
  projection?: DailyTrendProjection
}"""


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    if "DailyTrendProjection" in text:
        print("Already patched. Nothing to do.")
        return 0
    if OLD not in text:
        print("ERROR: could not find DailyTrendResponse block to replace.")
        return 1
    TARGET.write_text(text.replace(OLD, NEW), encoding="utf-8")
    print("Patched types.ts successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
