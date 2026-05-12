"""
Hitachi VSP Volumes Collector — collects LDEVs and host-group mappings.
Auto-registered with CollectorRegistry.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.hitachi.client import HitachiVSPClient
from app.db.session import get_db_cursor
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.hitachi.volumes")
settings = get_settings()
SCHEMA = settings.db_schema

# LDEV blockCapacity is in 512-byte blocks
LDEV_BLOCK_SIZE = 512


@CollectorRegistry.register("hitachi", "volumes")
class HitachiVolumesCollector(BaseCollector):
    VENDOR = "hitachi"
    COLLECTOR_TYPE = "volumes"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"Hitachi array '{array_config.name}' has no cred_key")
        self.client = HitachiVSPClient(
            array_name=array_config.name, cred_key=cred_key,
            fqdn=array_config.array_fqdn, mgmt_ip=array_config.mgmt_ip,
        )

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def collect(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"volumes": {}, "hosts": {}}

        # Get LDEVs (volumes) with pagination — VSP SVP limits response size
        # Paginate in batches of 500 to avoid timeouts on large arrays (5000+ LDEVs)
        all_ldevs = []
        start_ldev = 0
        page_size = 500
        max_pages = 30  # safety limit: 15,000 LDEVs max
        for page in range(max_pages):
            params = {"count": page_size, "ldevOption": "defined"}
            if start_ldev > 0:
                params["startLdevId"] = start_ldev
            resp = self.client.get("ldevs", params=params, timeout=120)
            if not resp or not resp.get("data"):
                break
            batch = resp["data"]
            all_ldevs.extend(batch)
            logger.debug(f"[{self.array_name}] LDEV page {page+1}: {len(batch)} items (total: {len(all_ldevs)})")
            if len(batch) < page_size:
                break  # last page
            # Next page starts after the last LDEV ID in this batch
            last_id = batch[-1].get("ldevId", 0)
            start_ldev = last_id + 1

        if all_ldevs:
            for ldev in all_ldevs:
                ldev_id = ldev.get("ldevId", 0)
                label = ldev.get("label", "")
                # Use LDEV ID as prefix to guarantee uniqueness
                # Labels can duplicate across host groups
                vol_name = f"LDEV:{ldev_id:05d}" + (f" ({label})" if label else "")

                # Size: blockCapacity is in blocks of 512 bytes
                size_bytes = ldev.get("blockCapacity", 0) * LDEV_BLOCK_SIZE
                # byteFormatCapacity is human-readable like "2.79 TB"
                byte_fmt = ldev.get("byteFormatCapacity", "")

                # Status
                status = ldev.get("status", "")

                # Pool association
                pool_id = ldev.get("composingPoolId")

                # Ports/host mapping from LDEV ports array
                ports = ldev.get("ports", [])
                host_names = []
                for port_info in ports:
                    hg_name = port_info.get("hostGroupName", "")
                    if hg_name and hg_name not in host_names:
                        host_names.append(hg_name)

                data["volumes"][vol_name] = {
                    "array_name": self.array_name,
                    "vendor": "hitachi",
                    "volume_name": vol_name,
                    "size": size_bytes,
                    "used": size_bytes,  # VSP doesn't expose per-LDEV used; use allocated
                    "data_reduction": 1.0,
                    "serial": f"{ldev_id:05d}",
                    "hosts": host_names,
                }

                # Build host entries from port mappings
                for hg_name in host_names:
                    if hg_name not in data["hosts"]:
                        data["hosts"][hg_name] = {
                            "array_name": self.array_name,
                            "vendor": "hitachi",
                            "host_name": hg_name,
                            "wwn": "",
                            "iqn": "",
                            "nqn": "",
                            "host_group": "",
                            "volumes": [],
                        }
                    if vol_name not in data["hosts"][hg_name]["volumes"]:
                        data["hosts"][hg_name]["volumes"].append(vol_name)

        # Try to get host-groups for WWN info (needs session auth, may timeout)
        try:
            if self.client._create_session_token():
                hg_resp = self.client.get("host-groups", params={"count": 500}, timeout=90)
                if hg_resp and hg_resp.get("data"):
                    for hg in hg_resp["data"]:
                        hg_name = hg.get("hostGroupName", "")
                        if hg_name in data["hosts"]:
                            # Get WWNs from host group
                            port_id = hg.get("portId", "")
                            hg_num = hg.get("hostGroupNumber", 0)
                            # Try to get WWNs for this host group
                            wwn_resp = self.client.get(
                                f"host-wwns?portId={port_id}&hostGroupNumber={hg_num}",
                                timeout=15
                            )
                            if wwn_resp and wwn_resp.get("data"):
                                wwns = [w.get("hostWwn", "") for w in wwn_resp["data"] if w.get("hostWwn")]
                                data["hosts"][hg_name]["wwn"] = ",".join(wwns)
        except Exception as e:
            logger.debug(f"[{self.array_name}] Host WWN enrichment skipped: {e}")

        logger.info(f"[{self.array_name}] {len(data['volumes'])} LDEVs, "
                     f"{len(data['hosts'])} host groups collected")
        return data

    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        try:
            with get_db_cursor() as cursor:
                # Save volumes
                cursor.execute(f"DELETE FROM {SCHEMA}.volumes_cache WHERE array_name=?",
                               (self.array_name,))
                for v in data.get("volumes", {}).values():
                    cursor.execute(
                        f"""INSERT INTO {SCHEMA}.volumes_cache (
                            array_name, vendor, volume_name, size, used,
                            data_reduction, serial, last_updated
                        ) VALUES (?,?,?,?,?,?,?,GETDATE())""",
                        (v["array_name"], "hitachi", v["volume_name"],
                         v.get("size", 0), v.get("used", 0),
                         v.get("data_reduction", 1), v.get("serial", "")),
                    )

                # Save hosts — use try/except per host to handle duplicate
                # host names (case-insensitive SQL constraint)
                cursor.execute(f"DELETE FROM {SCHEMA}.hosts_cache WHERE array_name=?",
                               (self.array_name,))
                seen_hosts = set()
                for h in data.get("hosts", {}).values():
                    import json
                    host_key = h["host_name"].upper()
                    if host_key in seen_hosts:
                        continue  # skip case-insensitive duplicate
                    seen_hosts.add(host_key)
                    try:
                        cursor.execute(
                            f"""INSERT INTO {SCHEMA}.hosts_cache (
                                array_name, vendor, host_name, wwn, iqn, nqn,
                                host_group, volumes, last_updated
                            ) VALUES (?,?,?,?,?,?,?,?,GETDATE())""",
                            (h["array_name"], "hitachi", h["host_name"],
                             h.get("wwn", ""), h.get("iqn", ""), h.get("nqn", ""),
                             h.get("host_group", ""),
                             json.dumps(h.get("volumes", []))),
                        )
                    except Exception:
                        pass  # skip duplicate key violations

            result.records_saved = len(data.get("volumes", {})) + len(data.get("hosts", {}))
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    def disconnect(self):
        self.client.disconnect()
