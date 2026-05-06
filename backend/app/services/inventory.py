"""
Inventory Sync Service — syncs array inventory from DimStorageFinance into USM.managed_arrays.

Runs as a daily cron job (03:00 UTC). Upserts all active, non-decommissioned arrays
from the authoritative DimStorageFinance table into USM's managed_arrays, enriching
each record with metadata (model, site, serial, FQDN, etc.).

Arrays not present in DimStorageFinance are left untouched (manual entries).
Arrays marked as Decommissioned in DimStorageFinance are disabled in USM.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from app.db.session import get_db_cursor, rows_to_dicts
from app.core.config import get_settings

logger = logging.getLogger("usm.inventory")
settings = get_settings()
SCHEMA = settings.db_schema

# Map DimStorageFinance vendor names → USM vendor codes
_VENDOR_MAP: Dict[str, str] = {
    "PureStorage": "pure",
    "NetApp": "netapp",
    "HPE": "hpe",
    "Oracle": "oracle",
    "Hitachi Vantara": "hitachi",
    "CommVault": "commvault",
    "Dell EMC": "dell",
    "Veeam": "veeam",
    "Nimble": "nimble",
}

# Vendors that have working collectors in USM
_SUPPORTED_VENDORS = {"pure", "netapp", "hpe", "oracle", "hitachi"}


def resolve_cred_key(vendor: str, array_name: str, model: str, site: str) -> Optional[str]:
    """
    Derive a KeePass credential key from vendor + array metadata.
    Returns None if no convention exists for this vendor.
    """
    if vendor == "pure":
        return f"PureStorage_API_{array_name}"
    elif vendor == "netapp":
        if model and "A400" in model.upper():
            return f"NetApp_A400_{site}" if site else None
        elif model and "StorageGrid" in model:
            return f"NetApp_SG_{site}" if site else None
        elif model and "CVO" in model.upper():
            return f"NetApp_CVO_{site}" if site else None
        return None
    elif vendor == "hpe":
        return f"HPE_{(model or '').replace(' ', '_')}_{site}" if site else None
    elif vendor == "oracle":
        return f"Oracle_ZFS_{site}" if site else None
    elif vendor == "hitachi":
        # F900 cred works for all VSP F900 arrays (user: maintenance)
        if model and "F900" in model.upper():
            return "F900"
        elif model and "VSP" in model.upper():
            return "VSP"
        return "F900"  # default to F900 for Hitachi
    elif vendor == "commvault":
        return f"CommVault_{site}" if site else None
    elif vendor == "dell":
        return f"DellEMC_{(model or '').replace(' ', '_')}_{site}" if site else None
    return None


def sync_from_dim_storage_finance() -> Dict[str, int]:
    """
    Sync arrays from dbo.DimStorageFinance → USM.managed_arrays.

    Returns dict with counts: inserted, updated, disabled, total.
    """
    logger.info("Starting inventory sync from DimStorageFinance...")
    stats = {"inserted": 0, "updated": 0, "disabled": 0, "total": 0, "errors": 0}

    # 1. Fetch all active arrays from DimStorageFinance
    try:
        with get_db_cursor() as cur:
            cur.execute(
                "SELECT ArrayNameKey, ArrayFQDN, ArraySerial, VendorArrayName, "
                "Vendor, Model, Site, Technology, Category, Usage, "
                "Dispostition, OEM, Support, ArrayIPCTL, "
                "InstallDate, OEM_EOSL_Date, MainEndDate, ActiveRecord "
                "FROM dbo.DimStorageFinance "
                "WHERE ActiveRecord = 1"
            )
            dim_rows = rows_to_dicts(cur, cur.fetchall())
    except Exception as e:
        logger.error(f"Failed to read DimStorageFinance: {e}")
        stats["errors"] = 1
        return stats

    stats["total"] = len(dim_rows)
    logger.info(f"Found {len(dim_rows)} active arrays in DimStorageFinance")

    # 2. Upsert each array into managed_arrays
    for row in dim_rows:
        array_name = row.get("ArrayNameKey", "").strip()
        if not array_name:
            continue

        dim_vendor = row.get("Vendor", "unknown")
        usm_vendor = _VENDOR_MAP.get(dim_vendor, dim_vendor.lower().replace(" ", ""))
        disposition = row.get("Dispostition", "")
        model = row.get("Model", "")
        site = row.get("Site", "")

        # Determine if this array should be enabled
        has_collector = usm_vendor in _SUPPORTED_VENDORS
        is_current = disposition == "Current"
        should_enable = has_collector and is_current

        # Determine monitoring status
        if not is_current:
            mon_status = "decomming"
        elif not has_collector:
            mon_status = "no_collector"
        else:
            mon_status = "active"

        # Derive credential key
        cred_key = resolve_cred_key(usm_vendor, array_name, model, site)

        try:
            with get_db_cursor() as cur:
                # Check if already exists
                cur.execute(
                    f"SELECT id, cred_key FROM {SCHEMA}.managed_arrays WHERE array_name = ?",
                    (array_name,),
                )
                existing = cur.fetchone()

                if existing:
                    # Update — preserve manually set cred_key
                    existing_cred_key = existing[1]
                    final_cred_key = existing_cred_key or cred_key

                    cur.execute(
                        f"""UPDATE {SCHEMA}.managed_arrays SET
                            vendor=?, group_label=?,
                            array_fqdn=?, array_serial=?, model=?, site=?,
                            technology=?, category=?, usage_label=?,
                            disposition=?, oem=?, support_provider=?,
                            mgmt_ip=?,
                            install_date=?, eosl_date=?, maint_end_date=?,
                            cred_key=?,
                            monitoring_status=?,
                            dim_sync_at=GETDATE(), updated_at=GETDATE()
                        WHERE array_name=?""",
                        (
                            usm_vendor, site,
                            row.get("ArrayFQDN"), row.get("ArraySerial"),
                            model, site,
                            row.get("Technology"), row.get("Category"),
                            row.get("Usage"), disposition,
                            row.get("OEM"), row.get("Support"),
                            row.get("ArrayIPCTL"),
                            row.get("InstallDate"), row.get("OEM_EOSL_Date"),
                            row.get("MainEndDate"),
                            final_cred_key,
                            mon_status,
                            array_name,
                        ),
                    )
                    stats["updated"] += 1
                else:
                    # Insert new
                    cur.execute(
                        f"""INSERT INTO {SCHEMA}.managed_arrays (
                            array_name, vendor, group_label, enabled, cred_key,
                            array_fqdn, array_serial, model, site,
                            technology, category, usage_label,
                            disposition, oem, support_provider,
                            mgmt_ip,
                            install_date, eosl_date, maint_end_date,
                            monitoring_status,
                            dim_sync_at, created_at, updated_at
                        ) VALUES (
                            ?,?,?,?,?,
                            ?,?,?,?,
                            ?,?,?,
                            ?,?,?,
                            ?,
                            ?,?,?,
                            ?,
                            GETDATE(), GETDATE(), GETDATE()
                        )""",
                        (
                            array_name, usm_vendor, site, should_enable, cred_key,
                            row.get("ArrayFQDN"), row.get("ArraySerial"),
                            model, site,
                            row.get("Technology"), row.get("Category"),
                            row.get("Usage"),
                            disposition, row.get("OEM"), row.get("Support"),
                            row.get("ArrayIPCTL"),
                            row.get("InstallDate"), row.get("OEM_EOSL_Date"),
                            row.get("MainEndDate"),
                            mon_status,
                        ),
                    )
                    stats["inserted"] += 1

        except Exception as e:
            logger.warning(f"Failed to sync array '{array_name}': {e}")
            stats["errors"] += 1

    # 3. Disable arrays that are in managed_arrays but decommissioned in DimStorageFinance
    try:
        with get_db_cursor() as cur:
            cur.execute(
                f"""UPDATE {SCHEMA}.managed_arrays
                    SET enabled = 0, monitoring_status = 'decommissioned',
                        disposition = 'Decommissioned', updated_at = GETDATE()
                    WHERE array_name IN (
                        SELECT ArrayNameKey FROM dbo.DimStorageFinance
                        WHERE ActiveRecord = 0 OR Dispostition = 'Decommissioned'
                    )
                    AND enabled = 1""",
            )
            stats["disabled"] = cur.rowcount
    except Exception as e:
        logger.warning(f"Failed to disable decommissioned arrays: {e}")

    logger.info(
        f"Inventory sync complete: {stats['inserted']} inserted, "
        f"{stats['updated']} updated, {stats['disabled']} disabled, "
        f"{stats['errors']} errors (from {stats['total']} DimStorageFinance rows)"
    )
    return stats
