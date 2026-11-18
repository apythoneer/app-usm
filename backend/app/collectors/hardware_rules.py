"""
Per-vendor hardware inventory — pure functions over raw API payloads.

Each builder turns a vendor's hardware endpoints into one shape, so the UI can
draw any array as a wireframe (enclosures → drive bays, ports, controllers,
power, cooling) with every part colored by health:

    {"vendor": "pure",
     "groups": [{"id", "label", "kind", "model"?, "status",
                 "components": [{"type", "name", "status", "raw"?, "slot"?,
                                 "detail"?, "attrs"?}]}],
     "summary": {component_type: {"total", "healthy", "warning", "critical",
                                  "inactive", "unknown"}}}

component type: controller | node | drive | port | psu | fan | battery |
                memory | cpu | module | sensor
status:         healthy | warning | critical | inactive (empty bay, unplugged
                port, not installed) | unknown

Payload shapes and status values below were taken from live responses of each
model in the fleet (read-only probe, 2026-11-18) plus the vendors' docs. A
payload of None means that call failed; the builder simply omits that part.
"""

import re
from typing import Dict, Iterable, List, Optional

from app.collectors.health import CRITICAL, HEALTHY, WARNING
from app.collectors.health_rules import _records, unity_level

INACTIVE = "inactive"
UNKNOWN = "unknown"
STATUSES = (HEALTHY, WARNING, CRITICAL, INACTIVE, UNKNOWN)
_RANK = {HEALTHY: 1, WARNING: 2, CRITICAL: 3}


# ── shared shape helpers ─────────────────────────────────────────────────────────
def comp(type_: str, name, status: str, raw=None, slot=None, detail=None, **attrs) -> dict:
    c = {"type": type_, "name": str(name), "status": status if status in STATUSES else UNKNOWN}
    if raw not in (None, ""):
        c["raw"] = str(raw)
    if slot is not None:
        c["slot"] = slot
    if detail:
        c["detail"] = str(detail)[:200]
    a = {k: v for k, v in attrs.items() if v not in (None, "", [], {})}
    if a:
        c["attrs"] = a
    return c


def worst(statuses: Iterable[str]) -> str:
    """Rollup for an enclosure: worst of healthy/warning/critical; all-inactive
    stays inactive; nothing known -> unknown."""
    best, seen = None, set()
    for s in statuses:
        seen.add(s)
        if s in _RANK and (best is None or _RANK[s] > _RANK[best]):
            best = s
    if best:
        return best
    return INACTIVE if seen and seen <= {INACTIVE} else UNKNOWN


def group(gid, label: str, kind: str, components: List[dict], model=None, status=None) -> dict:
    g = {"id": str(gid), "label": label, "kind": kind,
         "status": status or worst(c["status"] for c in components),
         "components": components}
    if model:
        g["model"] = str(model)
    return g


def summarize(groups: List[dict]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for g in groups:
        for c in g.get("components", []):
            s = out.setdefault(c["type"], {"total": 0, **{k: 0 for k in STATUSES}})
            s["total"] += 1
            s[c["status"]] = s.get(c["status"], 0) + 1
    return out


def inventory(vendor: str, groups: List[dict]) -> dict:
    groups = [g for g in groups if g.get("components")]
    return {"vendor": vendor, "groups": groups, "summary": summarize(groups)}


def _speed(bps) -> Optional[str]:
    try:
        v = float(bps)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return f"{v / 1e9:g} Gb/s" if v >= 1e9 else f"{v / 1e6:g} Mb/s"


def _int_tail(s) -> Optional[int]:
    m = re.search(r"(\d+)\D*$", str(s or ""))
    return int(m.group(1)) if m else None


# ── Pure FlashArray / Cloud Block Store ──────────────────────────────────────────
# Live v2 `hardware` types: chassis, storage_shelf, controller, drive_bay,
# eth_port, fc_port, ha_rdma_port, power_supply, cooling, temp_sensor (older
# docs also list short forms ct/ch/sh/bay/eth/fc/pwr/fan/tmp/iom/nvb/sas/ib).
# Live hardware status is "ok"; drive status "healthy".
_PURE_TYPE = {
    "controller": "controller", "ct": "controller",
    "drive_bay": "drive", "bay": "drive", "nvram_bay": "drive", "nvb": "drive",
    "eth_port": "port", "fc_port": "port", "ha_rdma_port": "port", "sas_port": "port",
    "ib_port": "port", "eth": "port", "fc": "port", "sas": "port", "ib": "port",
    "power_supply": "psu", "pwr": "psu", "cooling": "fan", "fan": "fan",
    "temp_sensor": "sensor", "tmp": "sensor",
}
_PURE_ENCLOSURE = {"chassis": "chassis", "ch": "chassis", "storage_shelf": "shelf", "sh": "shelf"}
_PURE_PORT_PROTO = {"eth_port": "ETH", "eth": "ETH", "fc_port": "FC", "fc": "FC",
                    "ha_rdma_port": "HA-RDMA", "sas_port": "SAS", "sas": "SAS",
                    "ib_port": "IB", "ib": "IB"}
_PURE_HW_STATUS = {"ok": HEALTHY, "healthy": HEALTHY, "identifying": HEALTHY,
                   "critical": CRITICAL, "noncritical": WARNING, "unhealthy": WARNING,
                   "degraded": WARNING, "not_installed": INACTIVE, "unused": INACTIVE,
                   "unknown": UNKNOWN}
_PURE_DRIVE_STATUS = {"healthy": HEALTHY, "updating": HEALTHY, "identifying": HEALTHY,
                      "failed": CRITICAL, "missing": CRITICAL, "unrecognized": CRITICAL,
                      "unhealthy": WARNING, "recovering": WARNING,
                      "empty": INACTIVE, "unused": INACTIVE, "unadmitted": INACTIVE}


def _pure_ctrl_status(c: dict) -> str:
    mode, status = (c.get("mode") or "").lower(), (c.get("status") or "").lower()
    if mode == "not present":
        return INACTIVE
    if mode == "offline" or status == "not ready":
        return CRITICAL
    if status == "updating":
        return WARNING
    return HEALTHY if status == "ready" else UNKNOWN


def pure_inventory(hardware=None, drives=None, controllers=None) -> dict:
    hw = _records(hardware, "items")
    if hw is None:
        return inventory("pure", [])
    drv = _records(drives, "items")
    drive_by = {d.get("name"): d for d in (drv or [])}
    ctrl_by = {c.get("name"): c for c in (_records(controllers, "items") or [])}

    order: List[str] = []
    members: Dict[str, List[dict]] = {}
    enclosure: Dict[str, dict] = {}
    for h in hw:
        name = h.get("name") or "?"
        htype = (h.get("type") or "").lower()
        key = name.split(".", 1)[0]
        if key not in members:
            members[key] = []
            order.append(key)
        status = _PURE_HW_STATUS.get((h.get("status") or "").lower(), UNKNOWN)
        detail = h.get("details")
        if htype in _PURE_ENCLOSURE and "." not in name:
            enclosure[key] = h
            continue
        ctype = _PURE_TYPE.get(htype, "module")
        if ctype == "sensor" and status == HEALTHY:
            continue                      # 60+ healthy temp sensors would bury the diagram
        if ctype == "drive":
            d = drive_by.get(name)
            if d is not None:
                status = _PURE_DRIVE_STATUS.get((d.get("status") or "").lower(), status)
                detail = d.get("details") or detail
                members[key].append(comp("drive", name, status, raw=d.get("status"), slot=h.get("index"),
                                         detail=detail, capacity=d.get("capacity"), media=d.get("type"),
                                         protocol=d.get("protocol")))
            else:
                # drives call succeeded but no drive in this bay -> empty bay
                members[key].append(comp("drive", name, INACTIVE if drv is not None else status,
                                         raw="empty" if drv is not None else h.get("status"),
                                         slot=h.get("index")))
            continue
        if ctype == "controller":
            c = ctrl_by.get(name, {})
            cs = _pure_ctrl_status(c) if c else status
            st = cs if _RANK.get(cs, 0) >= _RANK.get(status, 0) else status
            members[key].append(comp("controller", name, st, raw=c.get("status") or h.get("status"),
                                     slot=h.get("index"), detail=detail, mode=c.get("mode"),
                                     model=h.get("model"), version=c.get("version")))
            continue
        attrs = {}
        if ctype == "port":
            attrs = {"protocol": _PURE_PORT_PROTO.get(htype), "speed": _speed(h.get("speed"))}
        elif ctype == "sensor":
            attrs = {"temperature_c": h.get("temperature")}
        elif ctype == "psu":
            attrs = {"model": h.get("model"), "voltage": h.get("voltage")}
        members[key].append(comp(ctype, name, status, raw=h.get("status"), slot=h.get("index"),
                                 detail=detail, **attrs))

    def kind_of(key):
        if key in enclosure:
            return _PURE_ENCLOSURE[(enclosure[key].get("type") or "").lower()]
        return "controller" if key.upper().startswith("CT") else "chassis"

    rank = {"chassis": 0, "controller": 1, "shelf": 2}
    groups = []
    for key in sorted(order, key=lambda k: (rank.get(kind_of(k), 3), k)):
        kind = kind_of(key)
        enc = enclosure.get(key, {})
        comps = sorted(members[key], key=lambda c: (c["type"], c.get("slot") if c.get("slot") is not None else 999, c["name"]))
        st = None
        if enc:
            enc_st = _PURE_HW_STATUS.get((enc.get("status") or "").lower(), UNKNOWN)
            st = worst([enc_st] + [c["status"] for c in comps])
        groups.append(group(key, f"{kind.title()} {key}", kind, comps, model=enc.get("model"), status=st))
    return inventory("pure", groups)


# ── NetApp ONTAP ─────────────────────────────────────────────────────────────────
# nodes:   cluster/nodes?fields=name,model,state,controller.frus  (frus: fan|psu|dimm|… state ok|error)
# disks:   storage/disks?fields=name,state,container_type,type,model,usable_size,bay,shelf.uid,home_node.name
# shelves: storage/shelves?fields=uid,name,model,state,module_type,bays,frus  (bays: id, has_disk, state)
# ports:   network/ethernet/ports (type physical|vlan|lag; state up|down|degraded; speed Mb/s)
#          network/fc/ports (state online|link_not_connected|offlined_by_user|…)
_ONTAP_NODE = {"up": HEALTHY, "down": CRITICAL, "taken_over": CRITICAL,
               "waiting_for_giveback": WARNING, "booting": WARNING, "degraded": WARNING}
_ONTAP_FC = {"online": HEALTHY, "link_not_connected": INACTIVE, "offlined_by_user": INACTIVE,
             "startup": WARNING, "link_disconnected": WARNING,
             "offlined_by_system": CRITICAL, "node_offline": CRITICAL}
_ONTAP_FRU_TYPE = {"fan": "fan", "psu": "psu", "dimm": "memory", "nvdimm": "memory"}


def _ontap_disk(d: dict, slot=None) -> dict:
    state, ctype = (d.get("state") or "").lower(), (d.get("container_type") or "").lower()
    if state == "broken" or ctype == "broken":
        st = CRITICAL
    elif state in ("reconstructing", "copy", "removed", "maintenance", "unfail"):
        st = WARNING
    elif state in ("present", "spare", "zeroing", "pending", "partner"):
        st = HEALTHY
    else:
        st = UNKNOWN
    return comp("drive", d.get("name") or "?", st, raw=state or ctype, slot=slot,
                capacity=d.get("usable_size"), media=d.get("type"), model=d.get("model"),
                container=ctype, owner=(d.get("home_node") or {}).get("name"))


def ontap_inventory(nodes=None, disks=None, shelves=None, eth_ports=None, fc_ports=None) -> dict:
    groups = []
    eth = [p for p in (_records(eth_ports, "records") or []) if (p.get("type") or "physical") == "physical"]
    fc = _records(fc_ports, "records") or []

    for n in _records(nodes, "records") or []:
        name = n.get("name") or "?"
        comps = [comp("node", name, _ONTAP_NODE.get((n.get("state") or "").lower(), UNKNOWN),
                      raw=n.get("state"), model=n.get("model"))]
        for f in ((n.get("controller") or {}).get("frus") or []):
            st = {"ok": HEALTHY, "error": CRITICAL}.get((f.get("state") or "").lower(), UNKNOWN)
            comps.append(comp(_ONTAP_FRU_TYPE.get((f.get("type") or "").lower(), "module"),
                              f.get("id"), st, raw=f.get("state"), slot=_int_tail(f.get("id"))))
        for p in eth:
            if (p.get("node") or {}).get("name") != name:
                continue
            st = {"up": HEALTHY, "down": INACTIVE, "degraded": WARNING}.get((p.get("state") or "").lower(), UNKNOWN)
            if p.get("enabled") is False:
                st = INACTIVE
            comps.append(comp("port", p.get("name"), st, raw=p.get("state"), protocol="ETH",
                              speed=_speed((p.get("speed") or 0) * 1e6)))
        for p in fc:
            if (p.get("node") or {}).get("name") != name:
                continue
            spd = p.get("speed")
            spd = (spd.get("maximum") if isinstance(spd, dict) else spd)
            comps.append(comp("port", p.get("name"), _ONTAP_FC.get((p.get("state") or "").lower(), UNKNOWN),
                              raw=p.get("state"), protocol="FC",
                              speed=f"{spd} Gb/s" if isinstance(spd, (int, float)) and spd else None))
        groups.append(group(f"node:{name}", f"Node {name}", "node", comps, model=n.get("model")))

    disk_recs = _records(disks, "records") or []
    by_bay, loose = {}, []
    for d in disk_recs:
        uid, bay = (d.get("shelf") or {}).get("uid"), d.get("bay")
        if uid is not None and bay is not None:
            by_bay[(uid, bay)] = d
        else:
            loose.append(d)

    for s in _records(shelves, "records") or []:
        uid, comps = s.get("uid"), []
        for b in s.get("bays") or []:
            bid = b.get("id")
            d = by_bay.pop((uid, bid), None)
            if d:
                comps.append(_ontap_disk(d, slot=bid))
            elif b.get("has_disk"):
                st = {"ok": HEALTHY, "error": CRITICAL}.get((b.get("state") or "").lower(), UNKNOWN)
                comps.append(comp("drive", f"bay {bid}", st, raw=b.get("state"), slot=bid))
            else:
                comps.append(comp("drive", f"bay {bid}", INACTIVE, raw="empty", slot=bid))
        for f in s.get("frus") or []:
            st = {"ok": HEALTHY, "error": CRITICAL}.get((f.get("state") or "").lower(), UNKNOWN)
            if f.get("installed") is False:
                st = INACTIVE
            comps.append(comp({"psu": "psu", "fan": "fan"}.get((f.get("type") or "").lower(), "module"),
                              f"{f.get('type') or 'fru'} {f.get('id')}", st, raw=f.get("state"), slot=f.get("id")))
        shelf_st = {"ok": None, "error": CRITICAL}.get((s.get("state") or "").lower())
        st = worst(([shelf_st] if shelf_st else []) + [c["status"] for c in comps])
        groups.append(group(f"shelf:{s.get('name')}", f"Shelf {s.get('name')}", "shelf", comps,
                            model=s.get("model"), status=st))

    rest = loose + list(by_bay.values())
    if rest:
        virtual = any((d.get("type") or "").lower() == "vmdisk" for d in rest)
        comps = [_ontap_disk(d, slot=i) for i, d in enumerate(sorted(rest, key=lambda d: d.get("name") or ""))]
        groups.append(group("disks", "Virtual disks" if virtual else "Other disks",
                            "virtual" if virtual else "disks", comps))
    return inventory("netapp", groups)


# ── HPE 3PAR / Primera / Alletra 9000 ────────────────────────────────────────────
# /system onlineNodes/clusterNodes; /ports portPos{node,slot,cardPort}, type, protocol,
# linkState, maxRate, label; /disks position "cage:magazine:disk", state 1 NORMAL,
# 2 DEGRADED, 3 NEW, 4 FAILED, 99 UNKNOWN, type 1 FC / 2 NL / 3 SSD, loopA0/B0.degraded.
_HPE_PORT_ROLE = {1: "host", 2: "disk", 3: "free", 4: "iport", 5: "rcfc", 6: "peer",
                  7: "rcip", 8: "iscsi", 9: "cna", 10: "fs"}
_HPE_PROTO = {1: "FC", 2: "iSCSI", 3: "FCoE", 4: "IP", 5: "SAS", 6: "NVMe"}
_HPE_DISK_STATE = {1: HEALTHY, 2: WARNING, 3: HEALTHY, 4: CRITICAL, 99: UNKNOWN}
_HPE_DISK_TYPE = {1: "FC", 2: "NL", 3: "SSD", 4: "SCM"}


def _hpe_port_status(p: dict) -> str:
    ls, role = p.get("linkState"), p.get("type")
    if ls == 4:
        return HEALTHY
    if ls in (6, 11):                      # ERROR_STATE, FWDEAD
        return CRITICAL
    if ls in (5, 10):                      # LOSS_SYNC (no light), OFFLINE
        return WARNING if role == 2 else INACTIVE
    return UNKNOWN


def hpe_inventory(system=None, ports=None, disks=None) -> dict:
    groups = []
    sysd = system if isinstance(system, dict) else {}
    online = set(sysd.get("onlineNodes") or [])
    node_ids = list(sysd.get("clusterNodes") or []) or list(range(sysd.get("totalNodes") or 0))
    port_list = _records(ports, "members") or []
    node_ids = sorted(set(node_ids) | {(p.get("portPos") or {}).get("node") for p in port_list} - {None})

    for i in node_ids:
        up = i in online if online else None
        comps = [comp("node", f"Node {i}", HEALTHY if up else (CRITICAL if up is False else UNKNOWN),
                      raw="online" if up else ("offline" if up is False else None),
                      master=True if sysd.get("masterNode") == i else None)]
        for p in sorted((p for p in port_list if (p.get("portPos") or {}).get("node") == i),
                        key=lambda p: ((p.get("portPos") or {}).get("slot", 0), (p.get("portPos") or {}).get("cardPort", 0))):
            pos = p.get("portPos") or {}
            comps.append(comp("port", f"{i}:{pos.get('slot')}:{pos.get('cardPort')}", _hpe_port_status(p),
                              raw=f"linkState {p.get('linkState')}", protocol=_HPE_PROTO.get(p.get("protocol")),
                              role=_HPE_PORT_ROLE.get(p.get("type")), speed=p.get("maxRate"), label=p.get("label")))
        groups.append(group(f"node:{i}", f"Node {i}", "node", comps))

    by_cage: Dict[str, List[dict]] = {}
    for d in _records(disks, "members") or []:
        pos = str(d.get("position") or "?")
        cage = pos.split(":")[0]
        st = _HPE_DISK_STATE.get(d.get("state"), UNKNOWN)
        detail = None
        if st == HEALTHY and any((d.get(k) or {}).get("degraded") for k in ("loopA0", "loopB0")):
            st, detail = WARNING, "path degraded"
        cap = d.get("mfgCapacityGB")
        by_cage.setdefault(cage, []).append(comp(
            "drive", pos, st, raw=f"state {d.get('state')}", detail=detail,
            capacity=int(cap * 1e9) if cap else (int(d["totalSizeMiB"] * 1048576) if d.get("totalSizeMiB") else None),
            media=_HPE_DISK_TYPE.get(d.get("type")), model=d.get("model")))
    for cage in sorted(by_cage, key=lambda c: int(c) if c.isdigit() else 999):
        comps = sorted(by_cage[cage], key=lambda c: [int(x) if x.isdigit() else 0 for x in c["name"].split(":")])
        for idx, c in enumerate(comps):
            c["slot"] = idx
        groups.append(group(f"cage:{cage}", f"Cage {cage}", "cage", comps))
    return inventory("hpe", groups)


# ── Hitachi VSP (components/instance) ────────────────────────────────────────────
_HIT_TYPE = {
    "ctls": "controller", "cacheMemories": "memory", "cacheFlashMemories": "module",
    "dkbs": "module", "chbs": "module", "lanbs": "module", "bkmfs": "module",
    "batteries": "battery", "fans": "fan", "dkcpss": "psu", "pecbs": "module",
    "pcps": "module", "swpks": "module", "chbbfans": "fan", "chbbpss": "psu",
    "sfps": "port", "hies": "module", "xPaths": "module", "encs": "module",
    "dbps": "psu", "drives": "drive",
}


def _hit_status(key: str, node: dict) -> str:
    s = (node.get("status") or "").strip().lower()
    if s == "normal":
        if key == "sfps" and "not connected" in (node.get("portCondition") or "").lower():
            return INACTIVE
        return HEALTHY
    if s in ("blocked", "failed"):
        return CRITICAL
    if s.startswith("warning") or s in ("copying", "copy incomplete", "pending"):
        return WARNING
    if s == "reserved":
        return HEALTHY if key == "drives" else INACTIVE      # reserved drive = spare
    if s in ("not installed", "not fix", "-", ""):
        return INACTIVE
    return UNKNOWN


def _hit_items(key: str, items) -> List[dict]:
    out = []
    for idx, it in enumerate(items or []):
        if not isinstance(it, dict) or "status" not in it:
            continue
        name = it.get("location") or it.get("portId") or f"{key}-{idx}"
        attrs = {}
        if key == "sfps":
            attrs = {"protocol": "SFP", "speed": it.get("speed"), "sfp": it.get("type"),
                     "condition": it.get("portCondition")}
        elif key == "ctls":
            attrs = {"temperature_c": it.get("temperature"), "temperature": it.get("temperatureStatus")}
        elif key == "drives":
            attrs = {"model": it.get("modelCode"), "usage": it.get("usage")}
        elif key in ("chbs", "dkbs", "cacheFlashMemories"):
            attrs = {"model": it.get("type")}
        elif key == "cacheMemories":
            attrs = {"cache_gb": it.get("cacheSize")}
        out.append(comp(_HIT_TYPE.get(key, "module"), name, _hit_status(key, it), raw=it.get("status"),
                        slot=_int_tail(name) if key == "drives" else idx, **attrs))
        for sub_key, sub in it.items():          # e.g. bkmfs[].batteries[]
            if isinstance(sub, list) and sub_key in _HIT_TYPE:
                out.extend(_hit_items(sub_key, sub))
    return out


def hitachi_inventory(components=None) -> dict:
    if not isinstance(components, dict):
        return inventory("hitachi", [])
    groups, dkc = [], []
    for key, val in components.items():
        if key == "driveBoxes":
            continue
        if isinstance(val, list):
            dkc.extend(_hit_items(key, val))
        elif isinstance(val, dict) and key != "system":       # e.g. chbb (channel-board box)
            sub = [c for k, v in val.items() if isinstance(v, list) for c in _hit_items(k, v)]
            if sub:
                groups.append(group(key, "Channel board box" if key == "chbb" else key, "chassis", sub))
    groups.insert(0, group("dkc", "Controller chassis (DKC)", "chassis", dkc))
    for box in components.get("driveBoxes") or []:
        comps = []
        for k in ("drives", "encs", "dbps"):
            comps.extend(_hit_items(k, box.get(k)))
        loc = box.get("location") or "drive box"
        groups.append(group(loc, f"Drive box {loc}", "drive_box", comps, model=box.get("type")))
    return inventory("hitachi", groups)


# ── Oracle ZFS Storage Appliance ─────────────────────────────────────────────────
# chassis detail (hardware/v1/chassis/chassis-NNN -> {"chassis": {...}}) carries
# disk/fan/psu/cpu/memory/slot lists, each with label, present, faulted (+ size,
# type, use, offline for disks). network/v1/devices: device, up, speed, location.
_ORA_LISTS = (("disk", "drive"), ("psu", "psu"), ("fan", "fan"), ("cpu", "cpu"),
              ("memory", "memory"), ("slot", "module"))


def _ora_status(it: dict) -> str:
    if it.get("present") is False:
        return INACTIVE
    if it.get("faulted") is True:
        return CRITICAL
    if it.get("offline") is True:
        return WARNING
    return HEALTHY


def oracle_inventory(chassis_details=None, devices=None) -> dict:
    groups = []
    for ch in chassis_details or []:
        if not isinstance(ch, dict):
            continue
        ch = ch.get("chassis", ch)
        is_head = (ch.get("type") or "") == "system"
        comps = []
        for key, ctype in _ORA_LISTS:
            for idx, it in enumerate(ch.get(key) or []):
                label = it.get("label") or f"{key} {idx}"
                attrs = {"model": it.get("model")}
                if key == "disk":
                    attrs.update(capacity=it.get("size"), use=it.get("use"), media=it.get("type"), rpm=it.get("rpm"))
                elif key == "memory":
                    attrs.update(capacity=it.get("size"))
                comps.append(comp(ctype, label, _ora_status(it),
                                  raw="faulted" if it.get("faulted") else ("absent" if it.get("present") is False else "ok"),
                                  slot=_int_tail(label) if key == "disk" else idx, **attrs))
        if is_head:
            for dv in _records(devices, "devices") or []:
                st = HEALTHY if dv.get("up") is True else INACTIVE
                media = (dv.get("media") or "").strip()
                comps.append(comp("port", dv.get("device"), st, raw="up" if dv.get("up") else "down",
                                  protocol={"ethernet": "ETH", "infiniband": "IB"}.get(media.lower(), media[:8] or "ETH"),
                                  speed=dv.get("speed"), location=dv.get("location")))
        st = worst(([WARNING] if ch.get("faulted") else []) + [c["status"] for c in comps])
        groups.append(group(ch.get("name") or ("head" if is_head else "shelf"),
                            f"{'Controller' if is_head else 'Disk shelf'} {ch.get('name') or ''}".strip(),
                            "head" if is_head else "shelf", comps, model=ch.get("model"), status=st))
    groups.sort(key=lambda g: 0 if g["kind"] == "head" else 1)
    return inventory("oracle", groups)


# ── Dell Unity ───────────────────────────────────────────────────────────────────
# Every resource carries health {value (HealthEnum), descriptionIds, descriptions}.
# ALRT_DISK_SLOT_EMPTY / ALRT_PORT_LINK_DOWN_NOT_IN_USE = an empty slot / unused
# port (not a fault). Parents: disk.parentDae|parentDpe, psu/fan/lcc/iom/memory/
# battery .parent {id, resource}, ports .storageProcessor|.parent.
def _unity_status(h: dict):
    h = h or {}
    ids = " ".join(h.get("descriptionIds") or [])
    if "SLOT_EMPTY" in ids or "NOT_IN_USE" in ids or "NOT_PRESENT" in ids:
        return INACTIVE, None
    lvl = unity_level(h.get("value"))
    detail = None if lvl == HEALTHY else "; ".join((h.get("descriptions") or [])[:1]) or None
    return (lvl or UNKNOWN), detail


def _unity_entries(payload) -> List[dict]:
    return [e.get("content", e) for e in (_records(payload, "entries") or [])]


def unity_inventory(sps=None, dpe=None, daes=None, disks=None, eth_ports=None, fc_ports=None,
                    sas_ports=None, psus=None, fans=None, batteries=None, lccs=None,
                    io_modules=None, memory=None) -> dict:
    encl: Dict[str, dict] = {}
    for e in _unity_entries(dpe):
        encl[e.get("id") or "dpe"] = {"label": e.get("name") or "DPE", "kind": "dpe", "model": e.get("model"),
                                      "health": e.get("health"), "comps": []}
    for e in _unity_entries(daes):
        encl[e.get("id")] = {"label": e.get("name") or e.get("id"), "kind": "dae", "model": e.get("model"),
                             "health": e.get("health"), "comps": []}
    if not encl:
        encl["dpe"] = {"label": "DPE", "kind": "dpe", "model": None, "health": None, "comps": []}
    dpe_id = next((k for k, v in encl.items() if v["kind"] == "dpe"), "dpe")
    sp_ids = {e.get("id") for e in _unity_entries(sps)}

    def home(e: dict) -> str:
        for k in ("parentDae", "parentDpe", "parent", "storageProcessor"):
            pid = (e.get(k) or {}).get("id") if isinstance(e.get(k), dict) else None
            if pid in encl:
                return pid
            if pid in sp_ids:
                return dpe_id                 # SPs live in the DPE
        return dpe_id

    def add(payload, ctype, protocol=None, speed_field=None, speed_scale=1e9, **extra):
        for e in _unity_entries(payload):
            st, detail = _unity_status(e.get("health"))
            attrs = {k: e.get(src) for k, src in extra.items()}
            if protocol:
                attrs["protocol"] = protocol
            if speed_field and isinstance(e.get(speed_field), (int, float)):
                attrs["speed"] = _speed(e[speed_field] * speed_scale)
            slot = e.get("slotNumber") if ctype == "drive" else _int_tail(e.get("name"))
            encl[home(e)]["comps"].append(comp(ctype, e.get("name") or e.get("id"), st,
                                               raw=(e.get("health") or {}).get("value"), slot=slot,
                                               detail=detail, **attrs))

    for e in _unity_entries(sps):
        st, detail = _unity_status(e.get("health"))
        encl[dpe_id]["comps"].append(comp("controller", e.get("name") or e.get("id"), st,
                                          raw=(e.get("health") or {}).get("value"), detail=detail,
                                          model=e.get("model")))
    add(disks, "drive", capacity="size", in_use="isInUse")
    # ethernetPort.speed is Mb/s; fc/sas currentSpeed is Gb/s
    add(eth_ports, "port", protocol="ETH", speed_field="speed", speed_scale=1e6, link_up="isLinkUp")
    add(fc_ports, "port", protocol="FC", speed_field="currentSpeed")
    add(sas_ports, "port", protocol="SAS", speed_field="currentSpeed")
    add(psus, "psu"); add(fans, "fan"); add(batteries, "battery")
    add(lccs, "module"); add(io_modules, "module"); add(memory, "memory")

    groups = []
    for eid in sorted(encl, key=lambda k: (0 if encl[k]["kind"] == "dpe" else 1, k)):
        e = encl[eid]
        own, _ = _unity_status(e["health"]) if e["health"] else (None, None)
        st = worst(([own] if own in _RANK else []) + [c["status"] for c in e["comps"]])
        groups.append(group(eid, e["label"], e["kind"], e["comps"], model=e["model"], status=st))
    return inventory("dell", groups)


# ── NetApp StorageGRID ───────────────────────────────────────────────────────────
# grid/node-health -> data: [{name, type adminNode|storageNode|apiGatewayNode|…,
#   state connected|administratively-down|unknown, severity normal|notice|minor|
#   major|critical, siteName, isPrimaryAdmin}]
_SG_ROLE = {"adminNode": "admin", "storageNode": "storage", "apiGatewayNode": "gateway",
            "archiveNode": "archive"}
_SG_SEV = {"normal": HEALTHY, "notice": HEALTHY, "minor": WARNING, "major": WARNING, "critical": CRITICAL}


def storagegrid_inventory(node_health=None) -> dict:
    sites: Dict[str, List[dict]] = {}
    for n in _records(node_health, "data") or []:
        state = (n.get("state") or "").lower()
        if state == "administratively-down":
            st = INACTIVE
        elif state != "connected":
            st = CRITICAL
        else:
            st = _SG_SEV.get((n.get("severity") or "").lower(), UNKNOWN)
        sites.setdefault(n.get("siteName") or "grid", []).append(comp(
            "node", n.get("name"), st, raw=f"{n.get('state')}/{n.get('severity')}",
            role=_SG_ROLE.get(n.get("type"), n.get("type")),
            primary_admin=True if n.get("isPrimaryAdmin") else None))
    groups = [group(f"site:{s}", f"Site {s}", "site",
                    sorted(c, key=lambda x: (x.get("attrs", {}).get("role", ""), x["name"])))
              for s, c in sorted(sites.items())]
    return inventory("storagegrid", groups)
