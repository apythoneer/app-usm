"""
External (partner) read-only API — /api/ext/v1.

A deliberately narrow, token-authenticated surface for outside applications.
Kept SEPARATE from the internal /api/v1 routes (which the dashboard calls
unauthenticated, same-origin) so that:
  - exposing this surface never changes the internal API's behavior, and
  - only hosts + volumes are reachable here — no mutation, no other endpoints.

Query logic is reused verbatim from the internal handlers, so the external view
can never drift from what the platform itself reports.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool

# Reuse the internal fetch + row-mapping helpers — single source of truth.
from app.api.v1.hosts import _fetch_hosts, _row_to_host
from app.api.v1.volumes import _fetch_volumes, _row_to_volume
from app.core.external_auth import require_scope

router = APIRouter(prefix="/api/ext/v1", tags=["external"])


@router.get("/hosts", dependencies=[Depends(require_scope("hosts:read"))])
async def ext_list_hosts(
    array_name: Optional[str] = Query(default=None, description="Filter to one array"),
    vendor: Optional[str] = Query(default=None, description="Filter by vendor"),
    search: Optional[str] = Query(default=None, description="Substring match on host name"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """List hosts (read-only). Each host includes its attached `volumes` for
    host->volume correlation."""
    result = await run_in_threadpool(
        _fetch_hosts, array_name, search, vendor, limit, offset, None, "asc"
    )
    return {
        "total": result["total"],
        "limit": limit,
        "offset": offset,
        "data": [_row_to_host(r) for r in result["rows"]],
    }


@router.get("/volumes", dependencies=[Depends(require_scope("volumes:read"))])
async def ext_list_volumes(
    array_name: Optional[str] = Query(default=None, description="Filter to one array"),
    vendor: Optional[str] = Query(default=None, description="Filter by vendor"),
    search: Optional[str] = Query(default=None, description="Substring match on volume name"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """List volumes (read-only). Each volume includes attached `hosts` and
    `host_groups` for volume->host correlation."""
    result = await run_in_threadpool(
        _fetch_volumes, array_name, search, vendor, limit, offset, None, "asc"
    )
    return {
        "total": result["total"],
        "limit": limit,
        "offset": offset,
        "data": [_row_to_volume(r) for r in result["rows"]],
    }
