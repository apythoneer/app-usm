"""
NetApp ONTAP Volumes Collector
Collects volume inventory and igroup (host) inventory from ONTAP REST API.
Saves to volumes_cache + hosts_cache (same tables as Pure).
Auto-registered with CollectorRegistry.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.netapp.client import NetAppClient
from app.db.session import get_fast_cursor, batch_upsert, snapshot_volume_history

from app.core.config import get_settings

from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.netapp.volumes")
settings = get_settings()
SCHEMA = settings.db_schema


@CollectorRegistry.register("netapp", "volumes")
class NetAppVolumesCollector(BaseCollector):
    VENDOR = "netapp"
    COLLECTOR_TYPE = "volumes"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"NetApp array '{array_config.name}' has no cred_key configured")
        self.client = NetAppClient(array_config.name, cred_key)

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        data: Dict[str, Any] = {
            "volumes": {},
            "hosts": {},
        }

        # Volumes — name, size, space, type, state, uuid, svm
        vols = self.client.get_all("storage/volumes", params={
            "fields": "name,size,space.used,space.available,uuid,svm.name,type,state,create_time",
        })
        if vols:
            for v in vols:
                name = v.get("name", "")
                vol_type = v.get("type", "")
                # Skip root volumes and internal system volumes
                if not name or vol_type == "dp" or name.endswith("_root"):
                    continue
                svm = v.get("svm", {}).get("name", "")
                # Use SVM-qualified name to avoid collisions
                display_name = f"{svm}:{name}" if svm else name
                size = v.get("size", 0) or 0
                space = v.get("space", {})
                used = space.get("used", 0) or 0

                data["volumes"][display_name] = {
                    "volume_name": display_name,
                    "size": int(size),
                    "used": int(used),
                    "data_reduction": 1.0,
                    "total_reduction": 1.0,
                    "snapshots": 0,
                    "created": v.get("create_time", ""),
                    "serial": v.get("uuid", ""),
                    "hosts": [],
                    "host_groups": [],
                    "protection_groups": [],
                    "last_updated": now,
                }

        # Igroups → hosts (SAN initiators mapped to LUNs)
        igroups = self.client.get_all("protocols/san/igroups", params={
            "fields": "name,initiators,protocol,lun_maps.logical_unit_number,"
                      "lun_maps.lun.name,svm.name,os_type",
        })
        if igroups:
            for ig in igroups:
                ig_name = ig.get("name", "")
                if not ig_name:
                    continue
                svm = ig.get("svm", {}).get("name", "")
                display_name = f"{svm}:{ig_name}" if svm else ig_name

                # Collect initiator IQNs/WWNs
                iqns = []
                wwns = []
                for init in ig.get("initiators", []):
                    initiator_name = init.get("name", "")
                    if initiator_name.startswith("iqn."):
                        iqns.append(initiator_name)
                    elif ":" in initiator_name and len(initiator_name) > 10:
                        wwns.append(initiator_name)

                # Collect mapped volumes
                mapped_vols = []
                for lm in ig.get("lun_maps", []):
                    lun_name = lm.get("lun", {}).get("name", "")
                    if lun_name:
                        mapped_vols.append(lun_name)

                data["hosts"][display_name] = {
                    "host_name": display_name,
                    "iqn": ",".join(iqns),
                    "wwn": ",".join(wwns),
                    "nqn": "",
                    "host_group": "",
                    "volumes": mapped_vols,
                    "last_updated": now,
                }

        return data

    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        """Batched, set-based persistence (see db.session.batch_upsert)."""
        try:
            volumes = data.get("volumes", {})
            hosts = data.get("hosts", {})

            vol_rows = [{
                "volume_name": name, "vendor": "netapp",
                "size": v["size"], "used": v["used"],
                "data_reduction": v["data_reduction"], "total_reduction": v["total_reduction"],
                "snapshots": v.get("snapshots", 0), "created": v["created"], "serial": v["serial"],
                "hosts": json.dumps(v["hosts"]),
                "host_groups": json.dumps(v["host_groups"]),
                "protection_groups": json.dumps(v["protection_groups"]),
            } for name, v in volumes.items()]

            host_rows = [{
                "host_name": name, "vendor": "netapp",
                "iqn": h["iqn"], "wwn": h["wwn"], "nqn": h["nqn"],
                "host_group": h["host_group"], "volumes": json.dumps(h["volumes"]),
            } for name, h in hosts.items()]

            with get_fast_cursor() as cursor:
                batch_upsert(
                    cursor, f"{SCHEMA}.volumes_cache",
                    key_cols=("volume_name",),
                    update_cols=("vendor", "size", "used", "data_reduction", "total_reduction",
                                 "snapshots", "created", "serial", "hosts",
                                 "host_groups", "protection_groups"),
                    rows=vol_rows, array_name=self.array_name,
                    extra_where=" AND vendor='netapp'",
                )
                batch_upsert(
                    cursor, f"{SCHEMA}.hosts_cache",
                    key_cols=("host_name",),
                    update_cols=("vendor", "iqn", "wwn", "nqn", "host_group", "volumes"),
                    rows=host_rows, array_name=self.array_name,
                    extra_where=" AND vendor='netapp'",
                )

                # Append a point-in-time snapshot for volume growth analytics.
                snapshot_volume_history(cursor, self.array_name)

            result.records_saved = len(vol_rows) + len(host_rows)

            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    def disconnect(self):

        self.client.disconnect()
