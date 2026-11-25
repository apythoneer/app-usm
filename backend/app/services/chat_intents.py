"""
Intent router ("skills") for the Storage AI chat.

WHY
    On the sandbox CPU the 3B model needs ~73 s just to read the 4k-token
    schema prompt cold, and its free-form SQL over that schema is often wrong
    (wrong table -> SQL error -> a second "fix it" LLM call -> past the 120 s
    ingress timeout). But it is quick and reliable at *picking* one of a few
    known question types. So the common questions are answered by vetted,
    parameterised queries (or the existing analytics code) with templated
    answers, and free-form Text-to-SQL stays only for the long tail.

HOW (route())
    1. Embed the question and compare it with every intent's example
       questions (nomic-embed-text, vectors computed once per process).
         best >= CHAT_INTENT_ACCEPT with a clear margin -> that intent, no LLM
         best >= CHAT_INTENT_CONSIDER                   -> CHAT_INTENT_LLM: "verify" asks the
              LLM yes/no whether the top candidate answers it, "choose" lets it pick among
              the top 3 or "other", "off" skips it (tiny prompt, JSON-schema output, ~2-3 s)
         otherwise                                      -> "other"
    2. Parameters (array, vendor, days, n, %) are extracted deterministically.
    3. answer() runs the vetted query, caches the result briefly and renders a
       templated answer. "other" -> the caller falls back to Text-to-SQL.

Every answer carries a `sql` label ("-- vetted query · intent=... · route=...")
so the UI and logs show which path answered. Measure with
backend/scripts/eval_chat_intents.py.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from app.core.config import get_settings

logger = logging.getLogger("usm.chat.intents")
settings = get_settings()
SCHEMA = settings.db_schema
_TIB = 1099511627776.0

VENDOR_NAMES = {"pure": "Pure", "netapp": "NetApp", "hitachi": "Hitachi", "hpe": "HPE",
                "dell": "Dell", "oracle": "Oracle", "nimble": "Nimble",
                "commvault": "Commvault", "veeam": "Veeam"}
# word-boundary aliases -> vendor code (codes as stored in USM.*.vendor)
_VENDOR_ALIASES = {
    "pure": ("pure", "purestorage", "flasharray", "flash array", "cbs"),
    "netapp": ("netapp", "ontap", "cvo", "cdot"),
    "hitachi": ("hitachi", "vsp", "hds"),
    "hpe": ("hpe", "3par", "primera", "alletra"),
    "dell": ("dell", "emc", "powermax", "vmax", "unity", "powerstore", "powerscale", "isilon"),
    "oracle": ("oracle zfs", "zfs", "oracle storage", "oracle array", "oracle arrays"),
}


# ── Parameters ────────────────────────────────────────────────────────────────

@dataclass
class Params:
    array: Optional[str] = None
    vendor: Optional[str] = None
    days: Optional[int] = None        # None = not stated; intents apply a default
    n: Optional[int] = None
    pct: Optional[float] = None
    text: str = ""                    # lower-cased question

    def key(self) -> tuple:
        return (self.array, self.vendor, self.days, self.n, self.pct)


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twenty": 20}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "quarter": 90, "year": 365}

_names_cache: Dict[str, Any] = {"names": [], "expires": 0.0}


def _array_names() -> List[str]:
    if time.monotonic() > _names_cache["expires"]:
        try:
            from app.db.session import get_db_cursor
            with get_db_cursor() as cur:
                cur.execute(f"SELECT array_name FROM {SCHEMA}.metrics_current WITH (NOLOCK) "
                            f"UNION SELECT array_name FROM {SCHEMA}.managed_arrays WITH (NOLOCK)")
                names = [r[0] for r in cur.fetchall() if r and r[0]]
            _names_cache.update(names=names, expires=time.monotonic() + 600)
        except Exception as e:
            logger.warning("array name list unavailable: %s", type(e).__name__)
            _names_cache["expires"] = time.monotonic() + 60
    return _names_cache["names"]


def _word(term: str, text: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def extract_params(question: str, names: Optional[List[str]] = None) -> Params:
    q = (question or "").lower()
    p = Params(text=q)

    names = _array_names() if names is None else names
    hits = [n for n in names if len(n) >= 3 and _word(n.lower(), q)]
    if hits:
        p.array = max(hits, key=len)

    for code, aliases in _VENDOR_ALIASES.items():
        if any(_word(a, q) for a in aliases):
            p.vendor = code
            break

    m = re.search(r"(\d+)\s*(hour|day|week|month|quarter|year)s?", q)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        p.days = max(1, -(-n // 24)) if unit == "hour" else n * _UNIT_DAYS[unit]
    elif re.search(r"\b(today|yesterday|24 ?h)\b", q):
        p.days = 1
    elif re.search(r"\b(week|weekly)\b", q):
        p.days = 7
    elif re.search(r"\b(month|monthly)\b", q):
        p.days = 30
    elif re.search(r"\bquarter\b", q):
        p.days = 90
    elif re.search(r"\b(year|ytd|annual)\b", q):
        p.days = 365
    if p.days:
        p.days = min(max(p.days, 1), 365)

    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent|pct)", q)
    if m:
        p.pct = float(m.group(1))

    m = (re.search(r"\b(?:top|first|biggest|largest)\s+(\d+|" + "|".join(_NUM_WORDS) + r")\b", q)
         or re.search(r"\b(\d+|" + "|".join(_NUM_WORDS) + r")\s+(?:arrays|volumes|systems|boxes|hosts|alerts)\b", q))
    if m:
        v = m.group(1)
        p.n = min(max(int(v) if v.isdigit() else _NUM_WORDS[v], 1), 50)
    return p


# ── DB + formatting helpers ───────────────────────────────────────────────────

def _rows(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    from app.db.session import get_db_cursor, rows_to_dicts
    with get_db_cursor() as cur:
        cur.execute(sql, params)
        return rows_to_dicts(cur, cur.fetchall())


def _vname(code: Optional[str]) -> str:
    return VENDOR_NAMES.get((code or "").lower(), code or "unknown")


def _tb(b) -> str:
    return f"{(float(b or 0) / _TIB):,.2f} TB"


def _scope(p: Params) -> str:
    return f" {_vname(p.vendor)}" if p.vendor else ""


def _active_join() -> str:
    return (f"LEFT JOIN {SCHEMA}.managed_arrays ma WITH (NOLOCK) ON ma.array_name = mc.array_name "
            f"WHERE ISNULL(ma.enabled, 1) = 1")


@dataclass
class Result:
    answer: str
    rows: int = 0


class NeedsArray(Exception):
    """The intent needs an array name the question didn't contain."""


# ── Handlers ──────────────────────────────────────────────────────────────────

def _growth(p: Params, shrink: bool = False) -> Result:
    from app.api.v1.analytics import _fetch_top_growers
    days, n = p.days or 30, p.n or 5
    rows = _fetch_top_growers(days, 5000)
    if p.vendor:
        rows = [r for r in rows if r["vendor"] == p.vendor]
    rows = [r for r in rows if (r["delta_tb"] < 0 if shrink else r["delta_tb"] > 0)]
    rows.sort(key=lambda r: r["delta_tb"], reverse=not shrink)
    word = "shrinking" if shrink else "growing"
    if not rows:
        return Result(f"No{_scope(p)} array was {word} over the last {days} days.")
    lines = [f"Fastest-{word}{_scope(p)} arrays over the last {days} days (used capacity):"]
    for i, r in enumerate(rows[:n], 1):
        pct = f", {r['delta_pct']:+.1f}%" if r.get("delta_pct") is not None else ""
        util = f", now {r['utilization_pct']:.1f}% full" if r.get("utilization_pct") is not None else ""
        lines.append(f"{i}. {r['array_name']} ({_vname(r['vendor'])}): {r['delta_tb']:+,.2f} TB{pct}{util}")
    return Result("\n".join(lines), len(rows))


def _volume_growth(p: Params) -> Result:
    if not p.array:
        raise NeedsArray()
    from app.api.v1.analytics import _fetch_top_volume_growers
    days, n = p.days or 7, p.n or 5
    rows = [r for r in _fetch_top_volume_growers(days, 5000, p.array) if r["delta_tb"] > 0]
    rows.sort(key=lambda r: r["delta_tb"], reverse=True)
    if not rows:
        return Result(f"No volume on {p.array} grew over the last {days} days.")
    lines = [f"Fastest-growing volumes on {p.array} over the last {days} days:"]
    for i, r in enumerate(rows[:n], 1):
        pct = f", {r['delta_pct']:+.1f}%" if r.get("delta_pct") is not None else ""
        lines.append(f"{i}. {r['volume_name']}: {r['delta_tb'] * 1024:+,.1f} GB{pct} "
                     f"(now {r['current_used_tb']:,.2f} TB used)")
    return Result("\n".join(lines), len(rows))


def _fullest(p: Params) -> Result:
    n = p.n or (50 if p.pct is not None else 5)
    where, args = [], []
    if p.vendor:
        where.append("mc.vendor = ?"); args.append(p.vendor)
    if p.pct is not None:
        where.append("mc.capacity_used_pct >= ?"); args.append(p.pct)
    extra = "".join(f" AND {w}" for w in where)
    rows = _rows(f"""SELECT TOP {int(n)} mc.array_name, mc.vendor, mc.capacity_used_pct,
                            mc.capacity_used, mc.capacity_total
                     FROM {SCHEMA}.metrics_current mc WITH (NOLOCK) {_active_join()}
                       AND mc.capacity_used_pct IS NOT NULL{extra}
                     ORDER BY mc.capacity_used_pct DESC""", tuple(args))
    if not rows:
        cond = f" at or above {p.pct:g}% full" if p.pct is not None else ""
        return Result(f"No{_scope(p)} arrays{cond}.")
    head = (f"{len(rows)}{_scope(p)} array{'s' if len(rows) != 1 else ''} at or above {p.pct:g}% full:"
            if p.pct is not None else f"Fullest{_scope(p)} arrays:")
    lines = [head] + [f"{i}. {r['array_name']} ({_vname(r['vendor'])}): {r['capacity_used_pct']:.1f}% "
                      f"({_tb(r['capacity_used'])} of {_tb(r['capacity_total'])})"
                      for i, r in enumerate(rows[:max(n, 5)], 1)]
    return Result("\n".join(lines), len(rows))


def _most_free(p: Params) -> Result:
    n = p.n or 5
    args = (p.vendor,) if p.vendor else ()
    rows = _rows(f"""SELECT TOP {int(n)} mc.array_name, mc.vendor, mc.capacity_used_pct,
                            mc.capacity_total - mc.capacity_used AS free_bytes, mc.capacity_total
                     FROM {SCHEMA}.metrics_current mc WITH (NOLOCK) {_active_join()}
                       AND mc.capacity_total > 0{' AND mc.vendor = ?' if p.vendor else ''}
                     ORDER BY mc.capacity_total - mc.capacity_used DESC""", args)
    if not rows:
        return Result("No capacity data found.")
    lines = [f"{_vname(p.vendor) + ' arrays' if p.vendor else 'Arrays'} with the most free capacity:"]
    lines += [f"{i}. {r['array_name']} ({_vname(r['vendor'])}): {_tb(r['free_bytes'])} free "
              f"of {_tb(r['capacity_total'])} ({r['capacity_used_pct']:.1f}% used)"
              for i, r in enumerate(rows, 1)]
    return Result("\n".join(lines), len(rows))


def _latency(p: Params) -> Result:
    n = p.n or 5
    col, kind = (("write_latency_us", "write") if "write" in p.text and "read" not in p.text
                 else ("read_latency_us", "read"))
    args = (p.vendor,) if p.vendor else ()
    rows = _rows(f"""SELECT TOP {int(n)} mc.array_name, mc.vendor, mc.{col} AS lat
                     FROM {SCHEMA}.metrics_current mc WITH (NOLOCK) {_active_join()}
                       AND mc.{col} IS NOT NULL{' AND mc.vendor = ?' if p.vendor else ''}
                     ORDER BY mc.{col} DESC""", args)
    if not rows:
        return Result(f"No {kind}-latency data found.")
    lines = [f"Highest current {kind} latency{_scope(p)} (slowest first):"]
    lines += [f"{i}. {r['array_name']} ({_vname(r['vendor'])}): {float(r['lat']) / 1000:,.2f} ms"
              for i, r in enumerate(rows, 1)]
    return Result("\n".join(lines), len(rows))


def _busiest(p: Params) -> Result:
    n = p.n or 5
    args = (p.vendor,) if p.vendor else ()
    rows = _rows(f"""SELECT TOP {int(n)} mc.array_name, mc.vendor, mc.read_iops, mc.write_iops,
                            ISNULL(mc.read_iops, 0) + ISNULL(mc.write_iops, 0) AS iops
                     FROM {SCHEMA}.metrics_current mc WITH (NOLOCK) {_active_join()}
                       {' AND mc.vendor = ?' if p.vendor else ''}
                     ORDER BY ISNULL(mc.read_iops, 0) + ISNULL(mc.write_iops, 0) DESC""", args)
    if not rows:
        return Result("No IOPS data found.")
    lines = [f"Busiest{_scope(p)} arrays right now (total IOPS):"]
    lines += [f"{i}. {r['array_name']} ({_vname(r['vendor'])}): {int(r['iops'] or 0):,} IOPS "
              f"({int(r['read_iops'] or 0):,} read / {int(r['write_iops'] or 0):,} write)"
              for i, r in enumerate(rows, 1)]
    return Result("\n".join(lines), len(rows))


def _eosl(p: Params) -> Result:
    upcoming = bool(re.search(r"\b(next|upcoming|coming|soon|will|this year|going|due|approaching)\b", p.text))
    args: list = []
    vend = ""
    if p.vendor:
        vend, args = " AND vendor = ?", [p.vendor]
    if upcoming:
        days = p.days or 365
        cond = "eosl_date >= CAST(GETDATE() AS DATE) AND eosl_date < DATEADD(DAY, ?, CAST(GETDATE() AS DATE))"
        args = [days] + args
    else:
        cond = "eosl_date < CAST(GETDATE() AS DATE)"
    rows = _rows(f"""SELECT array_name, vendor, model, site, eosl_date
                     FROM {SCHEMA}.managed_arrays WITH (NOLOCK)
                     WHERE disposition = 'Current' AND {cond}{vend}
                     ORDER BY eosl_date""", tuple(args))
    what = f"reach end of service life in the next {p.days or 365} days" if upcoming else "are past end of service life"
    if not rows:
        return Result(f"No active{_scope(p)} arrays {what}.")
    n = p.n or 15
    lines = [f"{len(rows)} active{_scope(p)} array{'s' if len(rows) != 1 else ''} {what}:"]
    lines += [f"• {r['array_name']} ({_vname(r['vendor'])}{', ' + r['model'] if r.get('model') else ''}"
              f"{', ' + r['site'] if r.get('site') else ''}): EOSL {r['eosl_date']}" for r in rows[:n]]
    if len(rows) > n:
        lines.append(f"…and {len(rows) - n} more.")
    return Result("\n".join(lines), len(rows))


def _open_alerts(p: Params) -> Result:
    where, args = ["resolved = 0", "suppressed = 0"], []
    if p.array:
        where.append("array_name = ?"); args.append(p.array)
    if p.vendor:
        where.append("vendor = ?"); args.append(p.vendor)
    for sev in ("critical", "warning", "info"):
        if _word(sev, p.text):
            where.append("LOWER(severity) = ?"); args.append(sev)
            break
    w = " AND ".join(where)
    counts = _rows(f"SELECT LOWER(severity) AS severity, COUNT(*) AS n FROM {SCHEMA}.messages WITH (NOLOCK) "
                   f"WHERE {w} GROUP BY LOWER(severity) ORDER BY COUNT(*) DESC", tuple(args))
    total = sum(int(c["n"]) for c in counts)
    where_txt = f" on {p.array}" if p.array else _scope(p)
    if not total:
        return Result(f"No open alerts{where_txt}.")
    n = p.n or 8
    top = _rows(f"SELECT TOP {int(n)} LOWER(severity) AS severity, event, COUNT(*) AS n, "
                f"COUNT(DISTINCT array_name) AS arrays, MAX(opened) AS latest "
                f"FROM {SCHEMA}.messages WITH (NOLOCK) WHERE {w} "
                f"GROUP BY LOWER(severity), event ORDER BY COUNT(*) DESC", tuple(args))
    by = ", ".join(f"{c['n']:,} {c['severity']}" for c in counts)
    lines = [f"{total:,} open alert{'s' if total != 1 else ''}{where_txt} ({by}). Most frequent:"]
    lines += [f"• [{r['severity']}] {r['event']}: {r['n']:,}× on {r['arrays']} array"
              f"{'s' if r['arrays'] != 1 else ''} (latest {str(r['latest'])[:16]})" for r in top]
    return Result("\n".join(lines), total)


def _unhealthy(p: Params) -> Result:
    args = (p.vendor,) if p.vendor else ()
    rows = _rows(f"""SELECT mc.array_name, mc.vendor, mc.array_status, mc.health_detail
                     FROM {SCHEMA}.metrics_current mc WITH (NOLOCK) {_active_join()}
                       AND LOWER(mc.array_status) IN ('warning', 'critical'){' AND mc.vendor = ?' if p.vendor else ''}
                     ORDER BY CASE LOWER(mc.array_status) WHEN 'critical' THEN 0 ELSE 1 END, mc.array_name""", args)
    if not rows:
        return Result(f"All{_scope(p)} reporting arrays are healthy (no warning or critical status).")
    crit = sum(1 for r in rows if (r["array_status"] or "").lower() == "critical")
    lines = [f"{len(rows)}{_scope(p)} array{'s' if len(rows) != 1 else ''} need attention "
             f"({crit} critical, {len(rows) - crit} warning):"]
    n = p.n or 15
    lines += [f"• {r['array_name']} ({_vname(r['vendor'])}): {r['array_status']}"
              f"{' — ' + r['health_detail'] if r.get('health_detail') else ''}" for r in rows[:n]]
    if len(rows) > n:
        lines.append(f"…and {len(rows) - n} more.")
    return Result("\n".join(lines), len(rows))


def _hosts_on_array(p: Params) -> Result:
    if not p.array:
        raise NeedsArray()
    rows = _rows(f"SELECT host_name, host_group FROM {SCHEMA}.hosts_cache WITH (NOLOCK) "
                 f"WHERE array_name = ? ORDER BY host_name", (p.array,))
    if not rows:
        return Result(f"No hosts are recorded on {p.array}.")
    n = p.n or 25
    shown = ", ".join(r["host_name"] for r in rows[:n])
    more = f", …and {len(rows) - n} more" if len(rows) > n else ""
    return Result(f"{p.array} has {len(rows)} host{'s' if len(rows) != 1 else ''}: {shown}{more}.", len(rows))


def _volumes_on_array(p: Params) -> Result:
    if not p.array:
        raise NeedsArray()
    rows = _rows(f"SELECT volume_name, size, used FROM {SCHEMA}.volumes_cache WITH (NOLOCK) "
                 f"WHERE array_name = ? ORDER BY used DESC", (p.array,))
    if not rows:
        return Result(f"No volumes are recorded on {p.array}.")
    n = p.n or 5
    total_used = sum(float(r["used"] or 0) for r in rows)
    lines = [f"{p.array} has {len(rows)} volume{'s' if len(rows) != 1 else ''} using {_tb(total_used)} in total. "
             f"Largest by space used:"]
    lines += [f"{i}. {r['volume_name']}: {_tb(r['used'])} used of {_tb(r['size'])} provisioned"
              for i, r in enumerate(rows[:n], 1)]
    return Result("\n".join(lines), len(rows))


def _data_reduction(p: Params) -> Result:
    if p.array:
        rows = _rows(f"SELECT array_name, vendor, data_reduction, total_reduction FROM {SCHEMA}.metrics_current "
                     f"WITH (NOLOCK) WHERE array_name = ?", (p.array,))
        if not rows or rows[0].get("data_reduction") is None:
            return Result(f"No data-reduction figure is reported for {p.array}.")
        r = rows[0]
        tot = f"; total reduction incl. thin/snapshots {r['total_reduction']:.1f}:1" if r.get("total_reduction") else ""
        return Result(f"{p.array} ({_vname(r['vendor'])}) data reduction is {r['data_reduction']:.1f}:1{tot}.", 1)
    args = (p.vendor,) if p.vendor else ()
    rows = _rows(f"""SELECT mc.vendor, COUNT(*) AS arrays, AVG(mc.data_reduction) AS avg_dr,
                            MAX(mc.data_reduction) AS max_dr
                     FROM {SCHEMA}.metrics_current mc WITH (NOLOCK) {_active_join()}
                       AND mc.data_reduction > 0{' AND mc.vendor = ?' if p.vendor else ''}
                     GROUP BY mc.vendor ORDER BY AVG(mc.data_reduction) DESC""", args)
    if not rows:
        return Result("No data-reduction figures are reported.")
    lines = ["Average data reduction (dedup + compression) by vendor:"]
    lines += [f"• {_vname(r['vendor'])}: {r['avg_dr']:.1f}:1 across {r['arrays']} arrays (best {r['max_dr']:.1f}:1)"
              for r in rows]
    return Result("\n".join(lines), len(rows))


def _array_count(p: Params) -> Result:
    args = (p.vendor,) if p.vendor else ()
    rows = _rows(f"SELECT vendor, COUNT(*) AS n FROM {SCHEMA}.managed_arrays WITH (NOLOCK) "
                 f"WHERE disposition = 'Current'{' AND vendor = ?' if p.vendor else ''} "
                 f"GROUP BY vendor ORDER BY COUNT(*) DESC", args)
    total = sum(int(r["n"]) for r in rows)
    if p.vendor:
        return Result(f"We have {total} active {_vname(p.vendor)} array{'s' if total != 1 else ''}.", total)
    by = ", ".join(f"{_vname(r['vendor'])} {r['n']}" for r in rows)
    return Result(f"We have {total} active systems in the inventory: {by}.", total)


def _by_site(p: Params) -> Result:
    args = (p.vendor,) if p.vendor else ()
    rows = _rows(f"SELECT ISNULL(NULLIF(site, ''), '(no site)') AS site, COUNT(*) AS n "
                 f"FROM {SCHEMA}.managed_arrays WITH (NOLOCK) WHERE disposition = 'Current'"
                 f"{' AND vendor = ?' if p.vendor else ''} "
                 f"GROUP BY ISNULL(NULLIF(site, ''), '(no site)') ORDER BY COUNT(*) DESC", args)
    if not rows:
        return Result("No site information found.")
    n = p.n or 10
    lines = [f"Active{_scope(p)} arrays by site ({len(rows)} sites):"]
    lines += [f"• {r['site']}: {r['n']}" for r in rows[:n]]
    if len(rows) > n:
        lines.append(f"…and {len(rows) - n} more sites.")
    return Result("\n".join(lines), len(rows))


def _array_summary(p: Params) -> Result:
    if not p.array:
        raise NeedsArray()
    m = _rows(f"SELECT TOP 1 * FROM {SCHEMA}.metrics_current WITH (NOLOCK) WHERE array_name = ?", (p.array,))
    a = _rows(f"SELECT TOP 1 vendor, model, site, disposition, eosl_date, monitoring_status "
              f"FROM {SCHEMA}.managed_arrays WITH (NOLOCK) WHERE array_name = ?", (p.array,))
    if not m and not a:
        return Result(f"I have no data for {p.array}.")
    m0, a0 = (m[0] if m else {}), (a[0] if a else {})
    bits = [f"{p.array} ({_vname(a0.get('vendor') or m0.get('vendor'))}"
            f"{', ' + a0['model'] if a0.get('model') else ''}{', ' + a0['site'] if a0.get('site') else ''})"]
    if m0.get("capacity_used_pct") is not None:
        bits.append(f"is {m0['capacity_used_pct']:.1f}% full ({_tb(m0.get('capacity_used'))} of "
                    f"{_tb(m0.get('capacity_total'))})")
    if m0.get("array_status"):
        bits.append(f"health {m0['array_status']}{' (' + m0['health_detail'] + ')' if m0.get('health_detail') else ''}")
    iops = (m0.get("read_iops") or 0) + (m0.get("write_iops") or 0)
    if m0.get("read_latency_us") or iops:  # some vendors don't report performance
        bits.append(f"read latency {float(m0.get('read_latency_us') or 0) / 1000:.2f} ms, {int(iops):,} IOPS")
    if m0.get("data_reduction"):
        bits.append(f"data reduction {m0['data_reduction']:.1f}:1")
    if a0.get("eosl_date"):
        bits.append(f"EOSL {a0['eosl_date']}")
    return Result(bits[0] + ": " + "; ".join(bits[1:]) + "." if len(bits) > 1 else bits[0] + ".", 1)


# ── Registry ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Intent:
    name: str
    description: str                  # shown to the LLM chooser
    examples: Tuple[str, ...]         # embedded for nearest-neighbour routing
    handler: Callable[[Params], Result] = field(compare=False)
    ttl: int = 120                    # result-cache seconds
    array_hint: str = ""              # clarification when NeedsArray


INTENTS: List[Intent] = [
    Intent("top_growth", "Arrays whose used capacity grew the most over a period (fastest growing).",
           ("which array is growing the most", "fastest growing arrays", "top 5 arrays by capacity growth this month",
            "which arrays grew the most in the last 30 days", "where is storage consumption increasing fastest",
            "biggest capacity growth by array this quarter", "which pure arrays are growing fastest"),
           _growth, ttl=900),
    Intent("top_shrink", "Arrays whose used capacity dropped the most over a period (shrinking / freed space).",
           ("which arrays are shrinking", "which array freed up the most space this month",
            "biggest decrease in used capacity", "which arrays lost the most data in the last 30 days"),
           lambda p: _growth(p, shrink=True), ttl=900),
    Intent("volume_growth", "Volumes on one array that grew the most over a period.",
           ("which volumes grew the most on 2bja", "fastest growing volumes on array 09rw this week",
            "what volume is growing on usodcpure07", "top growing luns on this array last 7 days"),
           _volume_growth, ttl=900, array_hint="which volumes grew most on 2bja this week"),
    Intent("fullest", "Arrays with the highest percent of capacity used, or above a given % full.",
           ("which arrays are the fullest", "most utilized arrays", "arrays over 90% full",
            "which arrays are above 80 percent utilization", "top 10 arrays by capacity used percent",
            "which arrays are almost full", "which netapp arrays are over 85% used"),
           _fullest),
    Intent("most_free", "Arrays with the most free / available capacity left.",
           ("which arrays have the most free space", "where do we have the most available capacity",
            "arrays with the most headroom", "which array has the most unused capacity"),
           _most_free),
    Intent("latency", "Arrays with the highest current read or write latency (slowest).",
           ("which arrays have the highest latency", "slowest arrays right now", "top read latency by array",
            "which arrays have the worst write latency", "show arrays with high response time"),
           _latency, ttl=60),
    Intent("busiest", "Arrays with the most current IOPS (busiest / highest load).",
           ("which arrays are the busiest", "highest iops arrays right now", "top arrays by io load",
            "which arrays are doing the most operations"),
           _busiest, ttl=60),
    Intent("eosl", "Active arrays past, or coming up on, end of service life (EOSL / out of support).",
           ("which arrays are past eosl", "arrays out of support", "what goes end of life this year",
            "which arrays reach eosl in the next 6 months", "list arrays past end of service life",
            "which pure arrays are end of support"),
           _eosl, ttl=3600),
    Intent("open_alerts", "Open (unresolved, unsuppressed) alerts, optionally for one array or severity.",
           ("show open alerts", "any active critical alerts", "what alerts are open on 2bja",
            "how many unresolved alerts do we have", "list current warnings", "open alerts for netapp"),
           _open_alerts, ttl=60),
    Intent("unhealthy", "Arrays whose hardware health is warning or critical, with the reason.",
           ("which arrays are unhealthy", "which arrays have a critical status", "show arrays in warning state",
            "any arrays with failed drives or controllers", "what arrays have health problems"),
           _unhealthy, ttl=60),
    Intent("hosts_on_array", "Hosts (initiators) connected to one named array.",
           ("what hosts are on 2bja", "list hosts connected to array 09rw", "how many hosts does usodcpure07 have",
            "which servers are attached to this array"),
           _hosts_on_array, array_hint="what hosts are on 2bja"),
    Intent("volumes_on_array", "Volumes on one named array, largest first.",
           ("what volumes are on 2bja", "largest volumes on array 09rw", "how many volumes does usodcpure07 have",
            "list luns on this array"),
           _volumes_on_array, array_hint="what volumes are on 2bja"),
    Intent("data_reduction", "Data-reduction (dedup + compression) ratio by vendor or for one array.",
           ("what is our data reduction ratio", "average dedup ratio by vendor", "data reduction on 2bja",
            "how good is compression across the fleet"),
           _data_reduction, ttl=600),
    Intent("array_count", "How many active arrays we have, overall or for a vendor.",
           ("how many arrays do we have", "how many pure storage arrays do we have", "count of netapp arrays",
            "total number of storage arrays by vendor"),
           _array_count, ttl=3600),
    Intent("by_site", "How many active arrays are at each site / datacenter.",
           ("how many arrays per site", "arrays by datacenter", "which site has the most arrays",
            "where are our arrays located"),
           _by_site, ttl=3600),
    Intent("array_summary", "Overview of one named array: fullness, health, latency, IOPS, model, site, EOSL.",
           ("tell me about 2bja", "how full is 09rw", "status of array usodcpure07", "give me a summary of 2bja",
            "what is the utilization of array 09rw"),
           _array_summary, array_hint="tell me about 2bja"),
]
_BY_NAME = {i.name: i for i in INTENTS}


# ── Routing ───────────────────────────────────────────────────────────────────

_EX_KEY = hashlib.sha1("|".join(f"{i.name}:{'/'.join(i.examples)}" for i in INTENTS).encode()).hexdigest()
_ex_vecs: Dict[str, Any] = {"key": None, "vecs": None, "owners": None}
_embed_state = {"down_until": 0.0}


def _example_scores(question: str) -> Optional[List[Tuple[str, float]]]:
    """Best cosine per intent (desc), or None if embeddings are unavailable."""
    if time.monotonic() < _embed_state["down_until"]:
        return None
    try:
        from app.services.embeddings import cosine, embed, query_prefix
        key = (_EX_KEY, settings.ollama_embed_model)
        if _ex_vecs["key"] != key:
            texts, owners = [], []
            for it in INTENTS:
                for ex in it.examples:
                    texts.append(query_prefix() + ex); owners.append(it.name)
            _ex_vecs.update(key=key, vecs=embed(texts), owners=owners)
            logger.info("intent examples embedded: %d", len(texts))
        # Examples are lower-case and the embedding is case-sensitive: "past EOSL"
        # scored 0.76 vs 0.95 for "past eosl", so normalise before comparing.
        q = embed([query_prefix() + " ".join(question.lower().split())])[0]
        best: Dict[str, float] = {}
        for owner, v in zip(_ex_vecs["owners"], _ex_vecs["vecs"]):
            s = cosine(q, v)
            if s > best.get(owner, -1):
                best[owner] = s
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    except Exception as e:
        _embed_state["down_until"] = time.monotonic() + 300
        logger.warning("intent embeddings unavailable (%s); LLM routing only for 300s", type(e).__name__)
        return None


def _llm_json(svc, system: str, user: str, schema: dict) -> dict:
    import json as _json
    resp = requests.post(
        f"{svc.base_url}/api/chat",
        json={"model": svc.model, "stream": False, "format": schema,
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
              "options": {"temperature": 0, "num_predict": 24}},
        headers=svc.headers, timeout=45, allow_redirects=False,
    )
    resp.raise_for_status()
    return _json.loads(resp.json().get("message", {}).get("content") or "{}")


_NOT_COVERED = ("a value at a past time or averaged over a period (yesterday, peak, average over 7 days), "
                "a different metric, a specific host or volume, applications, owners, firmware, snapshots, "
                "replication, or a comparison")


def _llm_choose(question: str, candidates: List[str], svc) -> str:
    """CHAT_INTENT_LLM=choose: pick one candidate intent or 'other'."""
    options = "\n".join(f"- {c}: {_BY_NAME[c].description}" for c in candidates)
    system = ("You route questions for a storage-monitoring assistant. Choose the ONE option that "
              "directly answers the question. Choose \"other\" if none fits exactly (for example the "
              "question needs different data, a different grouping, or a comparison the option does "
              f"not provide).\nOptions:\n{options}\n- other: none of the above")
    schema = {"type": "object", "properties": {"intent": {"type": "string", "enum": candidates + ["other"]}},
              "required": ["intent"]}
    choice = _llm_json(svc, system, question, schema).get("intent", "other")
    return choice if choice in candidates else "other"


def _llm_verify(question: str, intent: str, svc) -> str:
    """CHAT_INTENT_LLM=verify: yes/no — does the top candidate's report answer the question?"""
    it = _BY_NAME[intent]
    system = ("You check whether a ready-made report answers a user's question about storage arrays. "
              "Users say \"systems\", \"boxes\" or \"storage\" for arrays and \"LUNs\" for volumes. "
              "Answer yes if the report gives the user what they asked for. Answer no if the question "
              f"asks for something the report does not show, such as {_NOT_COVERED}.")
    user = (f"Question: {question}\nReport: {it.description} "
            f"(answers questions like \"{it.examples[0]}\" or \"{it.examples[1]}\")\n"
            "Does the report answer the question?")
    schema = {"type": "object", "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}},
              "required": ["answer"]}
    return intent if _llm_json(svc, system, user, schema).get("answer") == "yes" else "other"


@dataclass
class Route:
    intent: str                       # intent name or "other"
    how: str                          # embedding | llm-verify | llm-choose | none
    score: float = 0.0
    candidates: List[str] = field(default_factory=list)


def route(question: str, svc=None) -> Route:
    scores = _example_scores(question)
    if scores is None:  # no embeddings -> Text-to-SQL (the LLM alone routes poorly)
        return Route("other", "none")
    accept, consider = settings.chat_intent_accept, settings.chat_intent_consider
    mode = (settings.chat_intent_llm or "verify").lower()
    (top, s1), s2 = scores[0], (scores[1][1] if len(scores) > 1 else 0.0)
    if s1 >= accept and s1 - s2 >= settings.chat_intent_margin:
        return Route(top, "embedding", s1, [top])
    cands = [n for n, s in scores[:3] if s >= consider]
    if cands and svc is not None and mode in ("verify", "choose"):
        try:
            pick = _llm_verify(question, top, svc) if mode == "verify" else _llm_choose(question, cands, svc)
            return Route(pick, f"llm-{mode}", s1, cands)
        except Exception as e:
            logger.warning("intent LLM routing failed: %s", type(e).__name__)
    return Route("other", "none", s1, [n for n, _ in scores[:3]])


_result_cache: Dict[tuple, Tuple[float, Result]] = {}


def answer(question: str, svc=None) -> Optional[Dict[str, Any]]:
    """Answer via a vetted intent, or None to let the caller use Text-to-SQL."""
    r = route(question, svc)
    if r.intent == "other":
        logger.info("intent: other (best %.2f, %s) -> text-to-sql", r.score, ",".join(r.candidates[:3]))
        return None
    it = _BY_NAME[r.intent]
    p = extract_params(question)
    label = (f"-- vetted query · intent={it.name} · route={r.how}"
             f"{f'({r.score:.2f})' if r.score else ''}"
             + "".join(f" · {k}={v}" for k, v in (("array", p.array), ("vendor", p.vendor),
                                                 ("days", p.days), ("n", p.n), ("pct", p.pct)) if v is not None))
    key = (it.name, p.key(), "write" in p.text, bool(re.search(r"\b(next|upcoming|coming|soon|will|due)\b", p.text)))
    hit = _result_cache.get(key)
    try:
        if hit and time.monotonic() - hit[0] < it.ttl:
            res = hit[1]
            label += " · cached"
        else:
            res = it.handler(p)
            _result_cache[key] = (time.monotonic(), res)
    except NeedsArray:
        return {"answer": f"Which array do you mean? Include its name, e.g. \"{it.array_hint}\".",
                "sql": label, "rows": 0, "error": None}
    logger.info("intent: %s via %s (%.2f) rows=%d", it.name, r.how, r.score, res.rows)
    return {"answer": res.answer, "sql": label, "rows": res.rows, "error": None}
