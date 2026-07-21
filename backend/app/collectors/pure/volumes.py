"""
Pure Storage Volumes Collector v2
Collects volume, host, host group, and protection group inventory.
Auto-registered with CollectorRegistry.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.pure.client import PureClient
from app.db.session import get_fast_cursor, batch_upsert, snapshot_volume_history, would_shrink_below

from app.core.config import get_settings

from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.pure.volumes")
settings = get_settings()
SCHEMA = settings.db_schema


@CollectorRegistry.register("pure", "volumes")
class PureVolumesCollector(BaseCollector):
    VENDOR = "pure"
    COLLECTOR_TYPE = "volumes"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        self.client = PureClient(array_config.name)

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        data: Dict[str, Any] = {
            "volumes": {},
            "hosts": {},
            "host_groups": {},
            "protection_groups": {},
        }

        # Snapshot counts per volume
        snap_counts: Dict[str, int] = {}
        snaps = self.client.get("volume", params={"snap": "true"})
        if snaps:
            for snap in snaps:
                snap_name = snap.get("name", "")
                source = snap.get("source", "")
                if not source and "." in snap_name:
                    source = snap_name.rsplit(".", 1)[0]
                if source:
                    snap_counts[source] = snap_counts.get(source, 0) + 1

        # Volumes — basic
        vols = self.client.get("volume")
        if vols:
            for v in vols:
                name = v.get("name", "")
                if name and "::" not in name:
                    data["volumes"][name] = {
                        "volume_name": name,
                        "created": v.get("created", ""),
                        "serial": v.get("serial", ""),
                        "size": 0,
                        "used": 0,
                        "data_reduction": 1.0,
                        "total_reduction": 1.0,
                        "snapshots": 0,
                        "snap_count": snap_counts.get(name, 0),
                        "hosts": [],
                        "host_groups": [],
                        "protection_groups": [],
                        "last_updated": now,
                    }

        # Volumes — space metrics
        space_data = self.client.get("volume", params={"space": "true"})
        if space_data:
            for v in space_data:
                name = v.get("name", "")
                if name in data["volumes"]:
                    data["volumes"][name]["size"] = int(v.get("size", 0) or 0)
                    data["volumes"][name]["used"] = int(v.get("total", 0) or 0)
                    data["volumes"][name]["data_reduction"] = float(v.get("data_reduction", 1) or 1)
                    data["volumes"][name]["total_reduction"] = float(v.get("total_reduction", 1) or 1)
                    data["volumes"][name]["snapshots"] = int(v.get("snapshots", 0) or 0)

        # Volumes — host connections
        conns = self.client.get("volume", params={"connect": "true"})
        if conns:
            for conn in conns:
                vol_name = conn.get("vol", conn.get("name", ""))
                host = conn.get("host", "")
                hgroup = conn.get("hgroup", "")
                if vol_name in data["volumes"]:
                    if host and host not in data["volumes"][vol_name]["hosts"]:
                        data["volumes"][vol_name]["hosts"].append(host)
                    if hgroup and hgroup not in data["volumes"][vol_name]["host_groups"]:
                        data["volumes"][vol_name]["host_groups"].append(hgroup)

        # Protection group membership
        pgs = self.client.get("pgroup", params={"members": "true"})
        if pgs:
            for pg in pgs:
                pg_name = pg.get("name", "")
                for vol in pg.get("volumes") or []:
                    if vol in data["volumes"]:
                        if pg_name not in data["volumes"][vol]["protection_groups"]:
                            data["volumes"][vol]["protection_groups"].append(pg_name)

        # Hosts
        hosts = self.client.get("host")
        if hosts:
            for h in hosts:
                name = h.get("name", "")
                if name:
                    data["hosts"][name] = {
                        "host_name": name,
                        "iqn": ",".join(h.get("iqn", []) or []),
                        "wwn": ",".join(h.get("wwn", []) or []),
                        "nqn": ",".join(h.get("nqn", []) or []),
                        "host_group": h.get("hgroup", ""),
                        "volumes": [],
                        "last_updated": now,
                    }

        # Host volume connections
        host_conns = self.client.get("host", params={"connect": "true"})
        if host_conns:
            for conn in host_conns:
                host_name = conn.get("host", conn.get("name", ""))
                vol = conn.get("vol", "")
                if host_name in data["hosts"] and vol:
                    if vol not in data["hosts"][host_name]["volumes"]:
                        data["hosts"][host_name]["volumes"].append(vol)

        # Host groups
        hgroups = self.client.get("hgroup")
        if hgroups:
            for hg in hgroups:
                name = hg.get("name", "")
                if name:
                    data["host_groups"][name] = {
                        "hgroup_name": name,
                        "hosts": hg.get("hosts", []) or [],
                        "volumes": [],
                        "last_updated": now,
                    }

        hg_conns = self.client.get("hgroup", params={"connect": "true"})
        if hg_conns:
            for conn in hg_conns:
                hg_name = conn.get("hgroup", conn.get("name", ""))
                vol = conn.get("vol", "")
                if hg_name in data["host_groups"] and vol:
                    if vol not in data["host_groups"][hg_name]["volumes"]:
                        data["host_groups"][hg_name]["volumes"].append(vol)

        # Protection groups
        all_pgs = self.client.get("pgroup")
        if all_pgs:
            for pg in all_pgs:
                name = pg.get("name", "")
                if name:
                    data["protection_groups"][name] = {
                        "pgroup_name": name,
                        "volumes": pg.get("volumes", []) or [],
                        "hosts": pg.get("hosts", []) or [],
                        "host_groups": pg.get("hgroups", []) or [],
                        "targets": pg.get("targets", []) or [],
                        "replication_enabled": 1 if pg.get("targets") else 0,
                        "last_updated": now,
                    }

        return data

    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        """Batched, set-based persistence (see db.session.batch_upsert).

        Replaces the old per-row SELECT-then-INSERT/UPDATE loop (which made one
        round-trip per volume/host and caused multi-minute jobs + ODBC timeouts).
        """
        volumes = data.get("volumes", {})
        hosts = data.get("hosts", {})
        host_groups = data.get("host_groups", {})
        pgroups = data.get("protection_groups", {})

        # Only delete old data if we have new data to replace it.
        # client.get() returns None on any API failure and collect() swallows that
        # into empty dicts, so an empty result here means "collection failed", not
        # "the array has no volumes". Without this guard batch_upsert(delete_missing=True)
        # treats every existing row as stale and wipes the array's inventory on a
        # single transient API error. Mirrors the guard in hitachi/hpe/dell/oracle.
        if not volumes and not hosts:
            logger.warning(f"[{self.array_name}] No volumes/hosts collected — keeping existing data")
            return True

        try:
            # Flatten dict-of-dicts into row lists with JSON-encoded list columns
            vol_rows = [{
                "volume_name": name,
                "size": v["size"], "used": v["used"],
                "data_reduction": v["data_reduction"], "total_reduction": v["total_reduction"],
                "snapshots": v.get("snapshots", 0), "created": v["created"], "serial": v["serial"],
                "hosts": json.dumps(v["hosts"]),
                "host_groups": json.dumps(v["host_groups"]),
                "protection_groups": json.dumps(v["protection_groups"]),
            } for name, v in volumes.items()]

            host_rows = [{
                "host_name": name,
                "iqn": h["iqn"], "wwn": h["wwn"], "nqn": h["nqn"],
                "host_group": h["host_group"], "volumes": json.dumps(h["volumes"]),
            } for name, h in hosts.items()]

            hg_rows = [{
                "hgroup_name": name,
                "hosts": json.dumps(hg["hosts"]), "volumes": json.dumps(hg["volumes"]),
            } for name, hg in host_groups.items()]

            pg_rows = [{
                "pgroup_name": name,
                "volumes": json.dumps(pg["volumes"]), "hosts": json.dumps(pg["hosts"]),
                "host_groups": json.dumps(pg["host_groups"]), "targets": json.dumps(pg["targets"]),
                "replication_enabled": pg["replication_enabled"],
            } for name, pg in pgroups.items()]

            with get_fast_cursor() as cursor:
                # Partial-collect guard: batch_upsert(delete_missing=True) deletes
                # every stored volume not in `rows`, so a truncated collect would
                # wipe the difference. The empty-collect guard above only catches a
                # FULLY empty result. Same protection the delete-then-insert vendors
                # get via would_shrink_below.
                blocked, existing = would_shrink_below(
                    cursor, f"{SCHEMA}.volumes_cache", self.array_name,
                    len(vol_rows), settings.collect_shrink_min_ratio,
                )
                if blocked:
                    logger.error(
                        f"[{self.array_name}] Refusing to replace {existing} volumes "
                        f"with only {len(vol_rows)} — partial/failed collection. "
                        f"Keeping existing data."
                    )
                    result.errors.append(f"partial collect: {len(vol_rows)} of ~{existing} volumes")
                    return False
                batch_upsert(
                    cursor, f"{SCHEMA}.volumes_cache",
                    key_cols=("volume_name",),
                    update_cols=("size", "used", "data_reduction", "total_reduction",
                                 "snapshots", "created", "serial", "hosts",
                                 "host_groups", "protection_groups"),
                    rows=vol_rows, array_name=self.array_name,
                )
                batch_upsert(
                    cursor, f"{SCHEMA}.hosts_cache",
                    key_cols=("host_name",),
                    update_cols=("iqn", "wwn", "nqn", "host_group", "volumes"),
                    rows=host_rows, array_name=self.array_name,
                )
                batch_upsert(
                    cursor, f"{SCHEMA}.host_groups_cache",
                    key_cols=("hgroup_name",),
                    update_cols=("hosts", "volumes"),
                    rows=hg_rows, array_name=self.array_name,
                )
                batch_upsert(
                    cursor, f"{SCHEMA}.protection_groups_cache",
                    key_cols=("pgroup_name",),
                    update_cols=("volumes", "hosts", "host_groups", "targets",
                                 "replication_enabled"),
                    rows=pg_rows, array_name=self.array_name,
                )

                # Append a point-in-time snapshot for volume growth analytics.
                snapshot_volume_history(cursor, self.array_name)

            result.records_saved = (

                len(vol_rows) + len(host_rows) + len(hg_rows) + len(pg_rows)
            )
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    def disconnect(self):

        self.client.disconnect()
