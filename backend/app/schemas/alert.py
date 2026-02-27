"""
Vendor-agnostic Alert schemas.
"""

from typing import Optional, Literal, Any
from pydantic import BaseModel, Field

SeverityType = Literal["critical", "warning", "info", "unknown"]


class AlertSchema(BaseModel):
    id: Optional[int] = None
    array_name: str
    vendor: str = "unknown"
    message_id: Optional[int] = None
    event: Optional[str] = None
    severity: SeverityType = "unknown"
    component_type: Optional[str] = None
    component_name: Optional[str] = None
    opened: Optional[str] = None
    closed: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    collected_at: Optional[str] = None
    teams_notified: Optional[str] = None
    snow_ticket: Optional[str] = None
    suppressed: bool = False
    resolved: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertFilter(BaseModel):
    array_name: Optional[str] = None
    vendor: Optional[str] = None
    severity: Optional[SeverityType] = None
    resolved: Optional[bool] = None
    suppressed: Optional[bool] = False
