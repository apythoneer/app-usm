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
from app.db.session import get_db_cursor
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
        try:
            with get_db_cursor() as cursor:
                self._save_volumes(cursor, data.get("volumes", {}))
                self._save_hosts(cursor, data.get("hosts", {}))
                self._save_host_groups(cursor, data.get("host_groups", {}))
                self._save_protection_groups(cursor, data.get("protection_groups", {}))

            result.records_saved = (
                len(data.get("volumes", {}))
                + len(data.get("hosts", {}))
                + len(data.get("host_groups", {}))
                + len(data.get("protection_groups", {}))
            )
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    # ------------------------------------------------------------------ helpers
    def _save_volumes(self, cursor, volumes: dict):
        cursor.execute(
            f"SELECT volume_name FROM {SCHEMA}.volumes_cache WHERE array_name=?",
            (self.array_name,),
        )
        existing = {r[0] for r in cursor.fetchall()}
        for name in existing - set(volumes):
            cursor.execute(
                f"DELETE FROM {SCHEMA}.volumes_cache WHERE array_name=? AND volume_name=?",
                (self.array_name, name),
            )
        for name, v in volumes.items():
            hj = json.dumps(v["hosts"])
            hgj = json.dumps(v["host_groups"])
            pgj = json.dumps(v["protection_groups"])
            cursor.execute(
                f"SELECT id FROM {SCHEMA}.volumes_cache WHERE array_name=? AND volume_name=?",
                (self.array_name, name),
            )
            if cursor.fetchone():
                cursor.execute(
                    f"""UPDATE {SCHEMA}.volumes_cache SET
                        size=?,used=?,data_reduction=?,total_reduction=?,
                        snapshots=?,created=?,serial=?,hosts=?,host_groups=?,
                        protection_groups=?,last_updated=GETDATE()
                    WHERE array_name=? AND volume_name=?""",
                    (v["size"], v["used"], v["data_reduction"], v["total_reduction"],
                     v.get("snapshots", 0), v["created"], v["serial"],
                     hj, hgj, pgj, self.array_name, name),
                )
            else:
                cursor.execute(
                    f"""INSERT INTO {SCHEMA}.volumes_cache
                        (array_name,volume_name,size,used,data_reduction,total_reduction,
                         snapshots,created,serial,hosts,host_groups,protection_groups)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (self.array_name, name, v["size"], v["used"],
                     v["data_reduction"], v["total_reduction"],
                     v.get("snapshots", 0), v["created"], v["serial"],
                     hj, hgj, pgj),
                )

    def _save_hosts(self, cursor, hosts: dict):
        cursor.execute(
            f"SELECT host_name FROM {SCHEMA}.hosts_cache WHERE array_name=?",
            (self.array_name,),
        )
        existing = {r[0] for r in cursor.fetchall()}
        for name in existing - set(hosts):
            cursor.execute(
                f"DELETE FROM {SCHEMA}.hosts_cache WHERE array_name=? AND host_name=?",
                (self.array_name, name),
            )
        for name, h in hosts.items():
            vj = json.dumps(h["volumes"])
            cursor.execute(
                f"SELECT id FROM {SCHEMA}.hosts_cache WHERE array_name=? AND host_name=?",
                (self.array_name, name),
            )
            if cursor.fetchone():
                cursor.execute(
                    f"""UPDATE {SCHEMA}.hosts_cache SET
                        iqn=?,wwn=?,nqn=?,host_group=?,volumes=?,last_updated=GETDATE()
                    WHERE array_name=? AND host_name=?""",
                    (h["iqn"], h["wwn"], h["nqn"], h["host_group"], vj, self.array_name, name),
                )
            else:
                cursor.execute(
                    f"""INSERT INTO {SCHEMA}.hosts_cache
                        (array_name,host_name,iqn,wwn,nqn,host_group,volumes)
                    VALUES (?,?,?,?,?,?,?)""",
                    (self.array_name, name, h["iqn"], h["wwn"], h["nqn"], h["host_group"], vj),
                )

    def _save_host_groups(self, cursor, hgroups: dict):
        cursor.execute(
            f"SELECT hgroup_name FROM {SCHEMA}.host_groups_cache WHERE array_name=?",
            (self.array_name,),
        )
        existing = {r[0] for r in cursor.fetchall()}
        for name in existing - set(hgroups):
            cursor.execute(
                f"DELETE FROM {SCHEMA}.host_groups_cache WHERE array_name=? AND hgroup_name=?",
                (self.array_name, name),
            )
        for name, hg in hgroups.items():
            hj = json.dumps(hg["hosts"])
            vj = json.dumps(hg["volumes"])
            cursor.execute(
                f"SELECT id FROM {SCHEMA}.host_groups_cache WHERE array_name=? AND hgroup_name=?",
                (self.array_name, name),
            )
            if cursor.fetchone():
                cursor.execute(
                    f"UPDATE {SCHEMA}.host_groups_cache SET hosts=?,volumes=?,last_updated=GETDATE() "
                    f"WHERE array_name=? AND hgroup_name=?",
                    (hj, vj, self.array_name, name),
                )
            else:
                cursor.execute(
                    f"INSERT INTO {SCHEMA}.host_groups_cache (array_name,hgroup_name,hosts,volumes) VALUES (?,?,?,?)",
                    (self.array_name, name, hj, vj),
                )

    def _save_protection_groups(self, cursor, pgroups: dict):
        cursor.execute(
            f"SELECT pgroup_name FROM {SCHEMA}.protection_groups_cache WHERE array_name=?",
            (self.array_name,),
        )
        existing = {r[0] for r in cursor.fetchall()}
        for name in existing - set(pgroups):
            cursor.execute(
                f"DELETE FROM {SCHEMA}.protection_groups_cache WHERE array_name=? AND pgroup_name=?",
                (self.array_name, name),
            )
        for name, pg in pgroups.items():
            vj = json.dumps(pg["volumes"])
            hj = json.dumps(pg["hosts"])
            hgj = json.dumps(pg["host_groups"])
            tj = json.dumps(pg["targets"])
            cursor.execute(
                f"SELECT id FROM {SCHEMA}.protection_groups_cache WHERE array_name=? AND pgroup_name=?",
                (self.array_name, name),
            )
            if cursor.fetchone():
                cursor.execute(
                    f"""UPDATE {SCHEMA}.protection_groups_cache SET
                        volumes=?,hosts=?,host_groups=?,targets=?,replication_enabled=?,last_updated=GETDATE()
                    WHERE array_name=? AND pgroup_name=?""",
                    (vj, hj, hgj, tj, pg["replication_enabled"], self.array_name, name),
                )
            else:
                cursor.execute(
                    f"""INSERT INTO {SCHEMA}.protection_groups_cache
                        (array_name,pgroup_name,volumes,hosts,host_groups,targets,replication_enabled)
                    VALUES (?,?,?,?,?,?,?)""",
                    (self.array_name, name, vj, hj, hgj, tj, pg["replication_enabled"]),
                )

    def disconnect(self):
        self.client.disconnect()
