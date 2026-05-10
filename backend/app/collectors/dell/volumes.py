"""
Dell EMC Unity Volumes Collector — LUNs, filesystems, and host mappings.
Auto-registered with CollectorRegistry.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.dell.client import DellUnityClient
from app.db.session import get_db_cursor
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.dell.volumes")
settings = get_settings()
SCHEMA = settings.db_schema


@CollectorRegistry.register("dell", "volumes")
class DellVolumesCollector(BaseCollector):
    VENDOR = "dell"
    COLLECTOR_TYPE = "volumes"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"Dell array '{array_config.name}' has no cred_key")
        self.client = DellUnityClient(
            array_name=array_config.name, cred_key=cred_key,
            fqdn=array_config.array_fqdn, mgmt_ip=array_config.mgmt_ip,
        )

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"volumes": {}, "hosts": {}}

        # Collect LUNs
        luns_resp = self.client.get(
            "lun",
            fields="name,sizeTotal,sizeAllocated,hostAccess,pool,health",
            params={"per_page": 2000},
        )
        if luns_resp and luns_resp.get("entries"):
            for entry in luns_resp["entries"]:
                content = entry.get("content", {})
                name = content.get("name", "")
                if not name:
                    name = entry.get("content", {}).get("id", "unknown")

                size = content.get("sizeTotal", 0)
                used = content.get("sizeAllocated", 0)

                # Host access mappings
                host_access = content.get("hostAccess", [])
                host_names = []
                for ha in (host_access or []):
                    host_ref = ha.get("host", {})
                    host_id = host_ref.get("id", "") if isinstance(host_ref, dict) else ""
                    if host_id:
                        host_names.append(host_id)

                data["volumes"][name] = {
                    "array_name": self.array_name,
                    "vendor": "dell",
                    "volume_name": name,
                    "size": size,
                    "used": used,
                    "data_reduction": 1.0,
                    "serial": "",
                }

        # Collect filesystem shares
        fs_resp = self.client.get(
            "filesystem",
            fields="name,sizeTotal,sizeAllocated,pool,health",
            params={"per_page": 2000},
        )
        if fs_resp and fs_resp.get("entries"):
            for entry in fs_resp["entries"]:
                content = entry.get("content", {})
                name = content.get("name", "")
                if not name:
                    continue
                size = content.get("sizeTotal", 0)
                used = content.get("sizeAllocated", 0)
                data["volumes"][f"fs:{name}"] = {
                    "array_name": self.array_name,
                    "vendor": "dell",
                    "volume_name": f"fs:{name}",
                    "size": size,
                    "used": used,
                    "data_reduction": 1.0,
                    "serial": "",
                }

        # Collect hosts
        hosts_resp = self.client.get(
            "host",
            fields="name,type,fcHostInitiators,iscsiHostInitiators,hostLUNs",
        )
        if hosts_resp and hosts_resp.get("entries"):
            for entry in hosts_resp["entries"]:
                content = entry.get("content", {})
                host_name = content.get("name", "")
                host_id = content.get("id", "")
                if not host_name:
                    continue

                # FC WWNs
                fc_inits = content.get("fcHostInitiators", []) or []
                wwns = []
                for fc in fc_inits:
                    if isinstance(fc, dict):
                        wwns.append(fc.get("id", ""))

                # iSCSI IQNs
                iscsi_inits = content.get("iscsiHostInitiators", []) or []
                iqns = []
                for iscsi in iscsi_inits:
                    if isinstance(iscsi, dict):
                        iqns.append(iscsi.get("id", ""))

                # Mapped LUNs
                host_luns = content.get("hostLUNs", []) or []
                vol_names = []
                for hl in host_luns:
                    if isinstance(hl, dict):
                        lun_ref = hl.get("lun", {})
                        if isinstance(lun_ref, dict):
                            vol_names.append(lun_ref.get("id", ""))

                data["hosts"][host_name] = {
                    "array_name": self.array_name,
                    "vendor": "dell",
                    "host_name": host_name,
                    "wwn": ",".join(wwns),
                    "iqn": ",".join(iqns),
                    "nqn": "",
                    "host_group": "",
                    "volumes": vol_names,
                }

        logger.info(f"[{self.array_name}] {len(data['volumes'])} volumes/shares, "
                     f"{len(data['hosts'])} hosts collected")
        return data

    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        try:
            with get_db_cursor() as cursor:
                cursor.execute(f"DELETE FROM {SCHEMA}.volumes_cache WHERE array_name=?",
                               (self.array_name,))
                for v in data.get("volumes", {}).values():
                    cursor.execute(
                        f"""INSERT INTO {SCHEMA}.volumes_cache (
                            array_name, vendor, volume_name, size, used,
                            data_reduction, serial, last_updated
                        ) VALUES (?,?,?,?,?,?,?,GETDATE())""",
                        (v["array_name"], "dell", v["volume_name"],
                         v.get("size", 0), v.get("used", 0),
                         v.get("data_reduction", 1), v.get("serial", "")),
                    )

                cursor.execute(f"DELETE FROM {SCHEMA}.hosts_cache WHERE array_name=?",
                               (self.array_name,))
                for h in data.get("hosts", {}).values():
                    cursor.execute(
                        f"""INSERT INTO {SCHEMA}.hosts_cache (
                            array_name, vendor, host_name, wwn, iqn, nqn,
                            host_group, volumes, last_updated
                        ) VALUES (?,?,?,?,?,?,?,?,GETDATE())""",
                        (h["array_name"], "dell", h["host_name"],
                         h.get("wwn", ""), h.get("iqn", ""), h.get("nqn", ""),
                         h.get("host_group", ""),
                         json.dumps(h.get("volumes", []))),
                    )

            result.records_saved = len(data.get("volumes", {})) + len(data.get("hosts", {}))
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    def disconnect(self):
        self.client.disconnect()
