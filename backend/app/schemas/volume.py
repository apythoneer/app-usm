"""
Vendor-agnostic Volume schemas.
"""

from typing import Optional, List, Any
from pydantic import BaseModel, Field


class VolumeSchema(BaseModel):
    array_name: str
    vendor: str = "unknown"
    volume_name: str
    size_bytes: Optional[int] = None
    used_bytes: Optional[int] = None
    data_reduction: Optional[float] = None
    total_reduction: Optional[float] = None
    thin_provisioning: Optional[float] = None
    snapshots: Optional[int] = None
    created: Optional[str] = None
    serial: Optional[str] = None
    hosts: Optional[List[str]] = Field(default_factory=list)
    host_groups: Optional[List[str]] = Field(default_factory=list)
    protection_groups: Optional[List[str]] = Field(default_factory=list)
    notes: Optional[str] = None
    last_updated: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VolumeFilter(BaseModel):
    array_name: Optional[str] = None
    vendor: Optional[str] = None
    search: Optional[str] = None
