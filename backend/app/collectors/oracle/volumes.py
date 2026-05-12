"""
Oracle ZFS Volumes Collector — collects LUNs and shares from ZFS REST API.
Auto-registered with CollectorRegistry.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import CollectorRegistry
from app.collectors.oracle.client import OracleZFSClient
from app.db.session import get_db_cursor
from app.core.config import get_settings
from app.schemas.array import ArrayConfig

logger = logging.getLogger("usm.oracle.volumes")
settings = get_settings()
SCHEMA = settings.db_schema


@CollectorRegistry.register("oracle", "volumes")
class OracleVolumesCollector(BaseCollector):
    VENDOR = "oracle"
    COLLECTOR_TYPE = "volumes"

    def __init__(self, array_config: ArrayConfig):
        super().__init__(array_config)
        cred_key = array_config.cred_key
        if not cred_key:
            raise ValueError(f"Oracle array '{array_config.name}' has no cred_key")
        self.client = OracleZFSClient(
            array_name=array_config.name, cred_key=cred_key,
            fqdn=array_config.array_fqdn, mgmt_ip=array_config.mgmt_ip,
        )

    def authenticate(self) -> bool:
        return self.client.authenticate()

    def _extract(self, entry: dict, singular_key: str) -> dict:
        """Handle both flat and nested ZFS REST API list responses.

        LIST endpoints may return items as flat dicts or nested under a singular key.
        e.g. pools list: [{"name":"Pool1",...}]  OR  [{"pool":{"name":"Pool1",...}}]
        """
        if singular_key in entry and isinstance(entry[singular_key], dict):
            return entry[singular_key]
        return entry

    def collect(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"volumes": {}}

        # Get pools list
        pools_resp = self.client.get("storage/v1/pools")
        if not pools_resp or not pools_resp.get("pools"):
            logger.warning(f"[{self.array_name}] No pools returned from API")
            return data

        pool_items = pools_resp["pools"]
        logger.debug(f"[{self.array_name}] {len(pool_items)} pools found, "
                     f"first item keys: {sorted(pool_items[0].keys()) if pool_items else []}")

        for pool_entry in pool_items:
            p = self._extract(pool_entry, "pool")
            pool_name = p.get("name", "")
            pool_status = p.get("status", "")
            if not pool_name:
                logger.debug(f"[{self.array_name}] Skipping pool with no name: {list(pool_entry.keys())}")
                continue
            if pool_status != "online":
                logger.debug(f"[{self.array_name}] Skipping pool '{pool_name}' (status={pool_status})")
                continue

            # Get projects in this pool
            projects_resp = self.client.get(f"storage/v1/pools/{pool_name}/projects")
            if not projects_resp or not projects_resp.get("projects"):
                logger.debug(f"[{self.array_name}] No projects in pool '{pool_name}'")
                continue

            proj_items = projects_resp["projects"]
            logger.debug(f"[{self.array_name}] Pool '{pool_name}': {len(proj_items)} projects, "
                         f"first item keys: {sorted(proj_items[0].keys()) if proj_items else []}")

            for proj_entry in proj_items:
                proj = self._extract(proj_entry, "project")
                proj_name = proj.get("name", "")
                if not proj_name:
                    continue

                # Get LUNs in this project
                luns_resp = self.client.get(
                    f"storage/v1/pools/{pool_name}/projects/{proj_name}/luns"
                )
                if luns_resp and luns_resp.get("luns"):
                    lun_items = luns_resp["luns"]
                    logger.debug(f"[{self.array_name}] {pool_name}/{proj_name}: "
                                 f"{len(lun_items)} LUNs, "
                                 f"first keys: {sorted(lun_items[0].keys()) if lun_items else []}")
                    for lun_entry in lun_items:
                        lun = self._extract(lun_entry, "lun")
                        lun_name = lun.get("name", "")
                        vol_name = f"{proj_name}/{lun_name}"
                        data["volumes"][vol_name] = {
                            "array_name": self.array_name,
                            "vendor": "oracle",
                            "volume_name": vol_name,
                            "size": lun.get("volsize", 0),
                            "used": lun.get("space_data", 0) or lun.get("logicalused", 0),
                            "data_reduction": 1.0,
                            "serial": lun.get("lunguid", ""),
                        }

                # Get filesystems/shares in this project
                fs_resp = self.client.get(
                    f"storage/v1/pools/{pool_name}/projects/{proj_name}/filesystems"
                )
                if fs_resp and fs_resp.get("filesystems"):
                    fs_items = fs_resp["filesystems"]
                    logger.debug(f"[{self.array_name}] {pool_name}/{proj_name}: "
                                 f"{len(fs_items)} filesystems, "
                                 f"first keys: {sorted(fs_items[0].keys()) if fs_items else []}")
                    for fs_entry in fs_items:
                        fsd = self._extract(fs_entry, "filesystem")
                        fs_name = fsd.get("name", "")
                        vol_name = f"{proj_name}/{fs_name}"
                        data["volumes"][vol_name] = {
                            "array_name": self.array_name,
                            "vendor": "oracle",
                            "volume_name": vol_name,
                            "size": fsd.get("quota", 0) or fsd.get("space_total", 0),
                            "used": fsd.get("space_data", 0),
                            "data_reduction": fsd.get("compressratio", 1.0),
                        }

        logger.info(f"[{self.array_name}] {len(data['volumes'])} volumes/shares collected")
        return data

    def save(self, data: Dict[str, Any], result: CollectorResult) -> bool:
        volumes = data.get("volumes", {})

        # Guard: keep existing data when collection returned nothing
        if not volumes:
            logger.warning(f"[{self.array_name}] No volumes collected — keeping existing data")
            return True

        try:
            with get_db_cursor() as cursor:
                cursor.execute(f"DELETE FROM {SCHEMA}.volumes_cache WHERE array_name=?", (self.array_name,))
                for v in volumes.values():
                    cursor.execute(
                        f"""INSERT INTO {SCHEMA}.volumes_cache (
                            array_name, vendor, volume_name, size, used,
                            data_reduction, serial, last_updated
                        ) VALUES (?,?,?,?,?,?,?,GETDATE())""",
                        (v["array_name"], "oracle", v["volume_name"],
                         v.get("size", 0), v.get("used", 0),
                         v.get("data_reduction", 1), v.get("serial", "")),
                    )
            result.records_saved = len(data.get("volumes", {}))
            return True
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"[{self.array_name}] save failed: {e}")
            return False

    def disconnect(self):
        self.client.disconnect()
