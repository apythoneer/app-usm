"""
Per-vendor hardware health rules — pure functions over raw API payloads.

Each function takes the JSON returned by a vendor's documented CURRENT-STATE
endpoints and returns (state, detail) via HealthRollup, where state is
healthy | warning | critical, or None when nothing could be checked. They hold
no clients and touch no DB, so they are unit-tested against the vendors'
documented example payloads. A payload argument of None means "that call failed"
and the check is skipped rather than guessed.

Status vocabularies are taken from:
  Pure         FlashArray REST 2.17 (py-pure-client FA_2_17 models
               controller_v_2_2 / hardware_v_2_2 / drive_v_2_4)
  NetApp       ONTAP REST — the same checks NetApp Harvest's health plugin runs
  HPE          WSAPI v1 GET /system (onlineNodes, clusterNodes, failedCapacityMiB)
               and GET /ports (linkState / type enums)
  Hitachi      Configuration Manager REST, "Getting information about the hardware
               installed in a storage system" (components/instance) — VSP F900
  Oracle       ZFSSA RESTful API OS8.8.x — problems, hardware chassis + cluster, pools
  Dell Unity   Unisphere REST HealthEnum (storops): 0 UNKNOWN, 5 OK, 7 OK_BUT,
               10 DEGRADED, 15 MINOR, 20 MAJOR, 25 CRITICAL, 30 NON_RECOVERABLE
  StorageGRID  Grid Management API v3 GET /grid/health
"""

from typing import Dict, Iterable, List, Optional, Tuple

from app.collectors.health import CRITICAL, WARNING, HEALTHY, HealthRollup

Result = Tuple[Optional[str], str]


def _records(payload, *keys) -> Optional[list]:
    """Pull the item list out of a vendor payload. Returns None if the payload is
    None (call failed) so the caller skips the check; [] for a valid empty list."""
    if payload is None:
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return v
        return []
    return None


def _group(items: Iterable[Tuple[str, str]], noun: str) -> List[str]:
    """[(name, status), …] -> ["2 drives failed (BAY3, BAY9)", …]."""
    by: Dict[str, List[str]] = {}
    for name, status in items:
        by.setdefault(status, []).append(str(name))
    out = []
    for status, names in by.items():
        shown = ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
        label = noun if len(names) == 1 else noun + "s"
        out.append(f"{len(names)} {label} {status} ({shown})")
    return out


# ── Pure FlashArray / Cloud Block Store ─────────────────────────────────────────
# controller.mode:   primary | secondary | offline | not present
# controller.status: ready | not ready | unknown | updating
# hardware.status:   healthy | unhealthy | critical | identifying | unknown | unused
# drive.status:      healthy | failed | missing | unhealthy | recovering |
#                    unrecognized | unadmitted | updating | empty | identifying
_PURE_HW = {"critical": CRITICAL, "unhealthy": WARNING}
_PURE_DRIVE_BAD = {"failed", "missing", "unhealthy", "unrecognized", "recovering"}


def pure_health(controllers=None, hardware=None, drives=None) -> Result:
    r = HealthRollup()

    ctrls = _records(controllers, "items")
    if ctrls is not None:
        r.check("controllers")
        for c in ctrls:
            name = c.get("name") or "controller"
            mode = (c.get("mode") or "").lower()
            status = (c.get("status") or "").lower()
            if mode == "not present":
                continue
            if mode == "offline":
                r.crit(f"{name} offline")
            elif status == "not ready":
                r.crit(f"{name} not ready")
            elif status == "updating":
                r.warn(f"{name} updating")

    hw = _records(hardware, "items")
    if hw is not None:
        r.check("hardware")
        for h in hw:
            status = (h.get("status") or "").lower()
            sev = _PURE_HW.get(status)
            if sev:
                details = f" — {h['details']}" if h.get("details") else ""
                r.add(sev, f"{h.get('name') or 'component'} {status}{details}")

    drv = _records(drives, "items")
    if drv is not None:
        r.check("drives")
        bad = [(d.get("name") or "?", (d.get("status") or "").lower())
               for d in drv if (d.get("status") or "").lower() in _PURE_DRIVE_BAD]
        for msg in _group(bad, "drive"):
            r.warn(msg)   # RAID-HA keeps serving through a failed drive

    return r.result()


# ── NetApp ONTAP (on-prem AFF and CVO) ──────────────────────────────────────────
# Mirrors NetApp Harvest's health plugin: node health (private CLI — the only
# place `health` exists; /api/cluster/nodes does not return it), node state and
# controller FRUs, broken disks, shelf errors, HA takeover-possible, and the
# system health monitor's open alerts.
_ONTAP_NODE_STATE = {
    "down": CRITICAL, "taken_over": CRITICAL,
    "waiting_for_giveback": WARNING, "booting": WARNING, "degraded": WARNING,
}
_ONTAP_SHELF_SEV = {"critical": CRITICAL, "error": CRITICAL, "warning": WARNING}


def ontap_health(node_health=None, nodes=None, broken_disks=None, shelves=None,
                 failover=None, support_alerts=None) -> Result:
    r = HealthRollup()

    nh = _records(node_health, "records")
    if nh is not None:
        r.check("node health")
        for n in nh:
            if n.get("health") is False:
                r.crit(f"node {n.get('node') or n.get('name') or '?'} unhealthy")

    nd = _records(nodes, "records")
    if nd is not None:
        r.check("nodes")
        for n in nd:
            name = n.get("name") or "?"
            sev = _ONTAP_NODE_STATE.get((n.get("state") or "").lower())
            if sev:
                r.add(sev, f"node {name} {n.get('state')}")
            ctl = n.get("controller") or {}
            if (ctl.get("over_temperature") or "").lower() == "over":
                r.crit(f"node {name} over temperature")
            fans = (ctl.get("failed_fan") or {}).get("count") or 0
            if fans:
                r.warn(f"node {name}: {fans} failed fan(s)")
            psus = (ctl.get("failed_power_supply") or {}).get("count") or 0
            if psus:
                r.warn(f"node {name}: {psus} failed power supply(s)")

    disks = _records(broken_disks, "records")
    if disks is not None:
        r.check("disks")
        broken = [(d.get("name") or "?", "broken") for d in disks
                  if (d.get("container_type") or "broken").lower() == "broken"]
        for msg in _group(broken, "disk"):
            r.warn(msg)   # RAID-DP/TEC keeps serving; spare reconstruction follows

    sh = _records(shelves, "records")
    if sh is not None:
        r.check("shelves")
        for s in sh:
            sev = _ONTAP_SHELF_SEV.get((s.get("error_severity") or "").lower())
            if sev:
                text = s.get("error_text") or s.get("error_type") or "error"
                r.add(sev, f"shelf {s.get('shelf') or s.get('name') or '?'}: {text}"[:160])

    fo = _records(failover, "records")
    if fo is not None:
        r.check("HA")
        for f in fo:
            if (f.get("mode") or "ha").lower() != "ha":
                continue          # single-node / non-HA (e.g. single-node CVO)
            if f.get("possible") is not True:
                why = f.get("state_description") or "takeover not possible"
                r.crit(f"HA: node {f.get('node') or '?'} cannot take over ({why})"[:160])

    sa = _records(support_alerts, "records")
    if sa is not None:
        r.check("health monitor")
        for a in sa:
            if a.get("suppress") is True:
                continue
            sev = (a.get("perceived_severity") or "").lower()
            label = a.get("alert_id") or a.get("monitor") or "health monitor alert"
            where = f" on {a['node']}" if a.get("node") else ""
            r.add(CRITICAL if sev in ("critical", "fatal") else WARNING, f"{label}{where}"[:160])

    return r.result()


# ── HPE 3PAR / Primera / Alletra 9000 (WSAPI) ──────────────────────────────────
# /system: onlineNodes / clusterNodes are node-id lists; failedCapacityMiB > 0
# means failed physical capacity (failed drives). /ports linkState enum:
# 4 READY, 5 LOSS_SYNC, 6 ERROR_STATE, 10 OFFLINE, 11 FWDEAD; type 2 = DISK,
# 3 = FREE (unused).
_HPE_PORT_ERROR = {6: "ERROR_STATE", 11: "FWDEAD"}
_HPE_DISK_PORT_DOWN = {5: "LOSS_SYNC", 10: "OFFLINE"}
_HPE_PORT_TYPE_DISK, _HPE_PORT_TYPE_FREE = 2, 3


def hpe_health(system=None, ports=None) -> Result:
    r = HealthRollup()

    if isinstance(system, dict):
        online = system.get("onlineNodes")
        cluster = system.get("clusterNodes")
        total = system.get("totalNodes")
        if isinstance(online, list):
            r.check("nodes")
            if isinstance(cluster, list) and cluster:
                missing = sorted(set(cluster) - set(online))
                if missing:
                    r.crit(f"node(s) {', '.join(map(str, missing))} offline "
                           f"({len(online)}/{len(cluster)} online)")
            elif isinstance(total, int) and len(online) < total:
                r.crit(f"{total - len(online)} node(s) offline ({len(online)}/{total} online)")
        failed = system.get("failedCapacityMiB")
        if isinstance(failed, (int, float)):
            r.check("drives")
            if failed > 0:
                r.warn(f"{failed / 1024:,.0f} GiB failed capacity (failed drive)")

    members = _records(ports, "members")
    if members is not None:
        r.check("ports")
        bad = []
        for p in members:
            ls, typ = p.get("linkState"), p.get("type")
            pos = p.get("portPos") or {}
            name = f"{pos.get('node', '?')}:{pos.get('slot', '?')}:{pos.get('cardPort', '?')}"
            if ls in _HPE_PORT_ERROR and typ != _HPE_PORT_TYPE_FREE:
                bad.append((name, _HPE_PORT_ERROR[ls]))
            elif typ == _HPE_PORT_TYPE_DISK and ls in _HPE_DISK_PORT_DOWN:
                bad.append((name, f"disk-port {_HPE_DISK_PORT_DOWN[ls]}"))
        for msg in _group(bad, "port"):
            r.warn(msg)

    return r.result()


# ── Hitachi VSP (E / Gx00 / Fx00 incl. F900) ────────────────────────────────────
# GET .../storages/{id}/components/instance. Documented status values:
#   Normal | Warning | Warning (FAN) | Warning (Port 0/1 failed) | Blocked | Failed
#   drives also: Copying | Pending | Copy incomplete | Reserved
#   SFP: Not fix | Normal | Warning;  drive-box parts: Not Installed
_HIT_AREA = {
    "ctls": "controller", "cacheMemories": "cache memory",
    "cacheFlashMemories": "cache flash", "dkbs": "disk board", "chbs": "channel board",
    "lanbs": "LAN board", "sfps": "SFP", "bkmfs": "backup module", "batteries": "battery",
    "fans": "fan", "dkcpss": "power supply", "driveBoxes": "drive box", "drives": "drive",
    "encs": "drive-box ENC", "dbps": "drive-box power supply", "xPaths": "x-path",
    "hies": "HIE", "pecbs": "PECB", "pcps": "PCP", "swpks": "switch package",
    "chbbfans": "CHBB fan", "chbbps": "CHBB power supply",
}
_HIT_OK = {"normal", "not fix", "not installed", "reserved", "-", ""}
_HIT_FAIL = {"blocked", "failed"}
_HIT_DEGRADED = {"copying", "copy incomplete", "pending"}


def hitachi_health(components=None) -> Result:
    r = HealthRollup()
    if not isinstance(components, dict):
        return r.result()

    findings: Dict[Tuple[str, str, str], List[str]] = {}   # (sev, noun, status) -> labels

    def classify(key: str, node: dict, status: str):
        noun = _HIT_AREA.get(key, key)
        r.check(noun)
        s = status.strip().lower()
        if s in _HIT_OK:
            return
        if s in _HIT_FAIL:
            # A failed drive is RAID-protected (spare copy follows) -> warning;
            # a blocked/failed controller, cache, PSU, battery, board -> critical.
            sev = WARNING if key == "drives" else CRITICAL
        elif s.startswith("warning") or s in _HIT_DEGRADED:
            sev = WARNING
        else:
            return
        label = node.get("location") or node.get("portId") or noun
        findings.setdefault((sev, noun, status.strip()), []).append(str(label))

    def walk(node, key: str):
        if isinstance(node, list):
            for item in node:
                walk(item, key)
        elif isinstance(node, dict):
            st = node.get("status")
            if isinstance(st, str) and key:
                classify(key, node, st)
            tst = node.get("temperatureStatus")
            if isinstance(tst, str) and tst.strip().lower() not in _HIT_OK:
                findings.setdefault((WARNING, _HIT_AREA.get(key, key) + " temperature",
                                     tst.strip()), []).append(str(node.get("location") or key))
            for k, v in node.items():
                if isinstance(v, (list, dict)):
                    walk(v, k)

    walk(components, "")
    for (sev, noun, status), labels in findings.items():
        for msg in _group(((l, status) for l in labels), noun):
            r.add(sev, msg)
    return r.result()


# ── Oracle ZFS Storage Appliance ─────────────────────────────────────────────────
# problems: active problems (cleared on repair) — severity Critical | Major | Minor
# hardware/v1/chassis: {"hardware": [{name, type, faulted: bool, …}]}
# hardware/v1/cluster: {"cluster": {state, description, peer_state, …}} where
#   AKCS_OWNER = "Active (takeover completed)", AKCS_STRIPPED = "waiting for failback"
# storage/v1/pools: state/status online | offline | exported (exported = owned by
#   the cluster peer, not a fault); BUI adds degraded | faulted | unavail.
_ORA_PROBLEM = {"critical": CRITICAL, "major": WARNING}
_ORA_FAILOVER = {"AKCS_OWNER", "AKCS_STRIPPED"}
_ORA_POOL = {"degraded": WARNING, "faulted": CRITICAL, "unavail": CRITICAL,
             "unavailable": CRITICAL, "offline": CRITICAL}


def oracle_health(problems=None, chassis=None, cluster=None, pools=None) -> Result:
    r = HealthRollup()

    probs = _records(problems, "problems")
    if probs is not None:
        r.check("problems")
        for entry in probs:
            p = entry.get("problem") if isinstance(entry.get("problem"), dict) else entry
            sev = _ORA_PROBLEM.get((p.get("severity") or "").lower())
            if sev:
                r.add(sev, f"{p.get('severity')}: {(p.get('description') or p.get('type') or 'problem')}"[:160])

    ch = _records(chassis, "hardware", "chassis")
    if ch is not None:
        r.check("chassis")
        for c in ch:
            if c.get("faulted") is True:
                r.warn(f"{c.get('type') or 'chassis'} chassis {c.get('name') or '?'} faulted")

    if isinstance(cluster, dict):
        cl = cluster.get("cluster", cluster)
        if isinstance(cl, dict) and cl.get("state"):
            r.check("cluster")
            if cl.get("state") in _ORA_FAILOVER:
                r.warn(f"cluster failover: {cl.get('description') or cl.get('state')}")

    pl = _records(pools, "pools")
    if pl is not None:
        r.check("pools")
        for p in pl:
            st = (p.get("status") or p.get("state") or "").lower()
            sev = _ORA_POOL.get(st)
            if sev:
                r.add(sev, f"pool {p.get('name') or '?'} {st}")

    return r.result()


# ── Dell Unity ───────────────────────────────────────────────────────────────────
_UNITY_NAMES = {0: "UNKNOWN", 5: "OK", 7: "OK_BUT", 10: "DEGRADED", 15: "MINOR",
                20: "MAJOR", 25: "CRITICAL", 30: "NON_RECOVERABLE"}


def unity_level(value) -> Optional[str]:
    """Unity HealthEnum value -> canonical severity (None = UNKNOWN / no signal)."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if v == 5:
        return HEALTHY
    if v in (7, 10, 15):
        return WARNING
    if v >= 20:
        return CRITICAL
    return None


def _unity_why(health: dict) -> str:
    descs = [d for d in (health.get("descriptions") or []) if d]
    return "; ".join(descs[:2]) or _UNITY_NAMES.get(health.get("value"), str(health.get("value")))


def unity_health(system_health=None, storage_processors=None) -> Result:
    """system_health: the system's `health` object {value, descriptions, …};
    storage_processors: list of SP `content` dicts with name/id + health."""
    r = HealthRollup()

    if isinstance(system_health, dict):
        sev = unity_level(system_health.get("value"))
        if sev:
            r.check("system")
            if sev != HEALTHY:
                r.add(sev, f"system {_unity_why(system_health)}"[:200])

    sps = _records(storage_processors, "entries")
    if sps is not None:
        for sp in sps:
            sp = sp.get("content", sp)
            h = sp.get("health") or {}
            sev = unity_level(h.get("value"))
            if sev:
                r.check("storage processors")
                if sev != HEALTHY:
                    r.add(sev, f"{sp.get('name') or sp.get('id') or 'SP'} {_unity_why(h)}"[:200])

    return r.result()


# ── NetApp StorageGRID ───────────────────────────────────────────────────────────
# GET /grid/health -> data: {alarms:{critical,major,minor,notice},
#   alerts:{critical,major,minor}, nodes:{connected, administratively-down, unknown}}
def storagegrid_health(health=None) -> Result:
    r = HealthRollup()
    if not isinstance(health, dict):
        return r.result()
    h = health.get("data", health)
    alarms, alerts, nodes = h.get("alarms") or {}, h.get("alerts") or {}, h.get("nodes") or {}

    r.check("grid alerts")
    crit = (alarms.get("critical") or 0) + (alerts.get("critical") or 0)
    major = (alarms.get("major") or 0) + (alerts.get("major") or 0)
    if crit:
        r.crit(f"{crit} critical grid alert(s)")
    if major:
        r.warn(f"{major} major grid alert(s)")

    if nodes:
        r.check("nodes")
        unknown = nodes.get("unknown") or 0
        if unknown:
            r.warn(f"{unknown} grid node(s) disconnected")

    return r.result()
