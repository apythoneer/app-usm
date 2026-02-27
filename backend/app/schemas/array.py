"""
Vendor-agnostic Array schemas.
All collectors map their native data to these models.
"""

from typing import Optional, Literal, Any
from pydantic import BaseModel, Field


VendorType = Literal["pure", "netapp", "commvault", "dell", "hpe", "unknown"]


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
    model: Optional[str] = None
    capacity_used_pct: Optional[float] = None
    total_iops: Optional[float] = None
    read_latency_us: Optional[float] = None
    write_latency_us: Optional[float] = None
    array_status: Optional[str] = None
    collected_at: Optional[str] = None


class ArrayConfig(BaseModel):
    """Array connection configuration (from arrays.txt or DB)."""
    name: str
    vendor: VendorType = "pure"
    host: Optional[str] = None   # hostname/IP if different from name
    enabled: bool = True
    tags: dict[str, str] = Field(default_factory=dict)
