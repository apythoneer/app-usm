"""
Vendor-agnostic Array schemas.
All collectors map their native data to these models.
"""

from typing import Optional, Literal, Any
from pydantic import BaseModel, Field


VendorType = Literal[
    "pure", "netapp", "hpe", "oracle", "hitachi",
    "commvault", "dell", "veeam", "nimble",
    "ibm", "veritas", "storagegrid", "unknown",
]


class ArrayMetrics(BaseModel):
    """Current performance + capacity snapshot for any storage array."""

    # Identity
    array_name: str
    vendor: VendorType = "unknown"
    model: Optional[str] = None
    firmware_version: Optional[str] = None

    # Performance
    read_iops: Optional[float] = None
    write_iops: Optional[float] = None
    total_iops: Optional[float] = None
    read_latency_us: Optional[float] = None
    write_latency_us: Optional[float] = None
    read_bandwidth_bytes: Optional[float] = None
    write_bandwidth_bytes: Optional[float] = None

    # Capacity
    capacity_total_bytes: Optional[int] = None
    capacity_used_bytes: Optional[int] = None
    capacity_used_pct: Optional[float] = None
    data_reduction: Optional[float] = None
    total_reduction: Optional[float] = None
    shared_space_bytes: Optional[int] = None
    snapshot_space_bytes: Optional[int] = None
    volume_space_bytes: Optional[int] = None

    # Health
    array_status: Optional[str] = None
    controller_status: Optional[str] = None
    uptime_seconds: Optional[int] = None
    uptime_str: Optional[str] = None

    # Timestamp
    collected_at: Optional[str] = None

    # Vendor-specific extras (not in core schema)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArraySummary(BaseModel):
    """Lightweight array summary for list views."""
    array_name: str
    vendor: VendorType = "unknown"
    model: Optional[str] = None          # hardware model from managed_arrays (NOT firmware)
    firmware_version: Optional[str] = None  # purity_version / ONTAP version etc.
    group: Optional[str] = None          # cloud/site group from arrays.txt column 3
    capacity_total_bytes: Optional[int] = None
    capacity_used_pct: Optional[float] = None
    data_reduction: Optional[float] = None
    total_iops: Optional[float] = None
    read_latency_us: Optional[float] = None
    write_latency_us: Optional[float] = None
    array_status: Optional[str] = None
    collected_at: Optional[str] = None


class ArrayTableRow(BaseModel):
    """Enriched per-array row for the dashboard Arrays table.

    Joins metrics_current with per-array counts from the same SQL Server tables the
    Alerts/Volumes/Hosts pages read (messages, volumes_cache, hosts_cache), so the
    counts stay consistent with those pages and with /arrays/fleet-stats.
    """
    array_name: str
    vendor: VendorType = "unknown"
    model: Optional[str] = None
    group: Optional[str] = None
    array_status: Optional[str] = None
    active_alert_count: int = 0
    capacity_used_bytes: Optional[int] = None
    capacity_total_bytes: Optional[int] = None   # usable (presented) capacity
    snapshot_space_bytes: Optional[int] = None
    capacity_used_pct: Optional[float] = None
    data_reduction: Optional[float] = None
    total_volumes: int = 0
    total_hosts: int = 0
    collected_at: Optional[str] = None


class ArrayConfig(BaseModel):
    """Array connection configuration (from managed_arrays DB or arrays.txt fallback)."""
    name: str
    vendor: VendorType = "pure"
    group: Optional[str] = None          # cloud/site label
    host: Optional[str] = None           # hostname/IP if different from name
    enabled: bool = True
    cred_key: Optional[str] = None       # KeePass entry name (auto-derived if None)
    model: Optional[str] = None          # hardware model from DimStorageFinance
    site: Optional[str] = None           # physical/cloud site
    array_fqdn: Optional[str] = None     # FQDN for API connections
    mgmt_ip: Optional[str] = None        # management IP
    tags: dict[str, str] = Field(default_factory=dict)


# ── Managed arrays (Settings UI) ─────────────────────────────────────────────

class ManagedArray(BaseModel):
    """Array record from the managed_arrays DB table."""
    id: Optional[int] = None
    array_name: str
    vendor: VendorType = "pure"
    group_label: Optional[str] = None
    cred_key: Optional[str] = None
    enabled: bool = True
    # DimStorageFinance inventory fields
    array_fqdn: Optional[str] = None
    array_serial: Optional[str] = None
    model: Optional[str] = None
    site: Optional[str] = None
    technology: Optional[str] = None
    category: Optional[str] = None
    usage_label: Optional[str] = None
    disposition: Optional[str] = None
    oem: Optional[str] = None
    support_provider: Optional[str] = None
    install_date: Optional[str] = None
    eosl_date: Optional[str] = None
    maint_end_date: Optional[str] = None
    mgmt_ip: Optional[str] = None
    monitoring_status: Optional[str] = None
    dim_sync_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ManagedArrayCreate(BaseModel):
    """Payload for adding a new array."""
    array_name: str
    vendor: VendorType = "pure"
    group_label: Optional[str] = None
    cred_key: Optional[str] = None


class ManagedArrayUpdate(BaseModel):
    """Payload for updating an existing array."""
    vendor: Optional[str] = None
    group_label: Optional[str] = None
    cred_key: Optional[str] = None
    enabled: Optional[bool] = None
    array_fqdn: Optional[str] = None
    mgmt_ip: Optional[str] = None
    monitoring_status: Optional[str] = None


class ArrayVerifyResult(BaseModel):
    """Result of verifying array connectivity."""
    array_name: str
    keepass_ok: bool = False
    connectivity_ok: bool = False
    version: Optional[str] = None
    error: Optional[str] = None
