"""
HPE 3Par / Primera / Alletra Volumes Collector
Collects volume + host inventory from WSAPI REST API.
Saves to volumes_cache + hosts_cache (same tables as Pure/NetApp).
Auto-registered with CollectorRegistry.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.hpe.client import HPEClient
from app.db.session import get_db_cursor
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.hpe.volumes")
settings = get_settings()
SCHEMA = settings.db_schema


@CollectorRegistry.register("hpe", "volumes")
class HPEVolumesCollector(BaseCollector):
    VENDOR = "hpe"
    COLLECTOR_TYPE = "volumes"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"HPE array '{array_config.name}' has no cred_key configured")
        self.client = HPEClient(
            array_name=array_config.name,
            cred_key=cred_key,
            fqdn=array_config.array_fqdn,
            model=array_config.model,
            mgmt_ip=array_config.mgmt_ip,
        )

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        data: Dict[str, Any] = {"volumes": {}, "hosts": {}}

        # Volumes
        vol_data = self.client.get("volumes")
        if vol_data and vol_data.get("members"):
            for v in vol_data["members"]:
                name = v.get("name", "")
                if not name:
                    continue
                size_mib = v.get("sizeMiB", 0)
                usr_used = v.get("userSpace", {}).get("usedMiB", 0)
                data["volumes"][name] = {
                    "array_name": self.array_name,
                    "vendor": "hpe",
                    "volume_name": name,
                    "size": int(size_mib * 1048576),  # MiB → bytes
                    "used": int(usr_used * 1048576),
                    "serial": str(v.get("wwn", "")),
                    "created": v.get("creationTimeSec", ""),
                    "data_reduction": v.get("compressionRatio", 1.0),
                    "snapshots": v.get("snapshotSpace", {}).get("usedMiB", 0),
                    "hosts": [],
                    "host_groups": [],
                    "last_updated": now,
                }

        # VLUNs — map volumes to hosts
        vlun_data = self.client.get("vluns")
        if vlun_data and vlun_data.get("members"):
            for vlun in vlun_data["members"]:
                vol_name = vlun.get("volumeName", "")
                host_name = vlun.get("hostname", "")
                if vol_name and host_name and vol_name in data["volumes"]:
                    if host_name not in data["volumes"][vol_name]["hosts"]:
                        data["volumes"][vol_name]["hosts"].append(host_name)

        # Hosts
        host_data = self.client.get("hosts")
        if host_data and host_data.get("members"):
            for h in host_data["members"]:
                name = h.get("name", "")
                if not name:
                    continue
                # Get initiators
                fcwwns = []
                iscsi_names = []
                for path in h.get("FCPaths", []):
                    wwn = path.get("wwn", "")
                    if wwn:
                        fcwwns.append(wwn)
                for path in h.get("iSCSIPaths", []):
                    iqn = path.get("name", "")
                    if iqn:
                        iscsi_names.append(iqn)

                # Find volumes for this host from VLUNs
                host_vols = []
                if vlun_data and vlun_data.get("members"):
                    for vlun in vlun_data["members"]:
                        if vlun.get("hostname") == name:
                            vn = vlun.get("volumeName", "")
                            if vn and vn not in host_vols:
                                host_vols.append(vn)

                data["hosts"][name] = {
                    "array_name": self.array_name,
                    "vendor": "hpe",
                    "host_name": name,
                    "wwn": ",".join(fcwwns) if fcwwns else None,
                    "iqn": ",".join(iscsi_names) if iscsi_names else None,
                    "host_group": None,
                    "volumes": json.dumps(host_vols) if host_vols else None,
                    "last_updated": now,
                }

        # Host sets (host groups)
        hset_data = self.client.get("hostsets")
        if hset_data and hset_data.get("members"):
            for hs in hset_data["members"]:
                hs_name = hs.get("name", "")
                members = hs.get("setmembers", [])
                for member in members:
                    if member in data["hosts"]:
                        data["hosts"][member]["host_group"] = hs_name

        logger.info(f"[{self.array_name}] {len(data['volumes'])} volumes, {len(data['hosts'])} hosts collected")
        return data

    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        try:
            with get_db_cursor() as cursor:
                # Delete old data for this array
                cursor.execute(f"DELETE FROM {SCHEMA}.volumes_cache WHERE array_name=?", (self.array_name,))
                cursor.execute(f"DELETE FROM {SCHEMA}.hosts_cache WHERE array_name=?", (self.array_name,))

                # Insert volumes
                for v in data.get("volumes", {}).values():
                    cursor.execute(
                        f"""INSERT INTO {SCHEMA}.volumes_cache (
                            array_name, vendor, volume_name, size, used,
                            data_reduction, serial, hosts, host_groups, last_updated
                        ) VALUES (?,?,?,?,?,?,?,?,?,GETDATE())""",
                        (
                            v["array_name"], "hpe", v["volume_name"],
                            v["size"], v["used"], v.get("data_reduction", 1),
                            v.get("serial"), json.dumps(v.get("hosts", [])),
                            json.dumps(v.get("host_groups", [])),
                        ),
                    )

                # Insert hosts
                for h in data.get("hosts", {}).values():
                    cursor.execute(
                        f"""INSERT INTO {SCHEMA}.hosts_cache (
                            array_name, vendor, host_name, wwn, iqn, host_group, volumes, last_updated
                        ) VALUES (?,?,?,?,?,?,?,GETDATE())""",
                        (
                            h["array_name"], "hpe", h["host_name"],
                            h.get("wwn"), h.get("iqn"), h.get("host_group"),
                            h.get("volumes"),
                        ),
                    )

            result.records_saved = len(data.get("volumes", {})) + len(data.get("hosts", {}))
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    def disconnect(self):
        self.client.disconnect()
