"""
Vendor-agnostic Host schemas.
"""

from typing import Optional, List, Any
from pydantic import BaseModel, Field


class HostSchema(BaseModel):
    array_name: str
    vendor: str = "unknown"
    host_name: str
    iqn: Optional[str] = None       # iSCSI
    wwn: Optional[str] = None       # Fibre Channel
    nqn: Optional[str] = None       # NVMe-oF
    host_group: Optional[str] = None
    volumes: Optional[List[str]] = Field(default_factory=list)
    last_updated: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HostGroupSchema(BaseModel):
    array_name: str
    vendor: str = "unknown"
    hgroup_name: str
    hosts: Optional[List[str]] = Field(default_factory=list)
    volumes: Optional[List[str]] = Field(default_factory=list)
    last_updated: Optional[str] = None


class HostFilter(BaseModel):
    array_name: Optional[str] = None
    vendor: Optional[str] = None
    search: Optional[str] = None
