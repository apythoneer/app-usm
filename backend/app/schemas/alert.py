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
    datadog_notified: Optional[str] = None
    datadog_event_id: Optional[str] = None
    datadog_event_url: Optional[str] = None
    # Recurrence: occurrence_count is this exact alert's clear→reappear count;
    # event_occurrences is the unified "how many times this event occurred on this
    # array" (SUM of occurrence_count over the same array+event signature), which is
    # correct across both stable-id (flapping) and insert-per-event vendors.
    occurrence_count: int = 1
    event_occurrences: Optional[int] = None
    # first_seen/last_seen = platform observation window (distinct from `opened`,
    # the ARRAY-reported time, and `collected_at`, the crawl that last saw it).
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    suppressed: bool = False
    resolved: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertFilter(BaseModel):
    array_name: Optional[str] = None
    vendor: Optional[str] = None
    severity: Optional[SeverityType] = None
    resolved: Optional[bool] = None
    suppressed: Optional[bool] = False
