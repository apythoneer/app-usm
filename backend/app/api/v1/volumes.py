"""
Volumes API — inventory across all arrays and vendors.
"""

import json
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.db.session import get_db_cursor, rows_to_dicts
from app.schemas.volume import VolumeSchema
from app.core.config import get_settings


class NoteUpdate(BaseModel):
    notes: Optional[str] = None

router = APIRouter(prefix="/volumes", tags=["volumes"])
settings = get_settings()
SCHEMA = settings.db_schema


def _fetch_volumes(array_name: Optional[str], search: Optional[str], limit: int) -> List[dict]:
    where = []
    params = []
    if array_name:
        where.append("array_name = ?")
        params.append(array_name)
    if search:
        where.append("volume_name LIKE ?")
        params.append(f"%{search}%")

    sql = f"SELECT TOP {limit} * FROM {SCHEMA}.volumes_cache"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY array_name, volume_name"

    with get_db_cursor() as cursor:
        cursor.execute(sql, params)
        return rows_to_dicts(cursor, cursor.fetchall())


@router.get("", response_model=List[VolumeSchema])
async def list_volumes(
    array_name: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=500, le=5000),
):
    rows = await run_in_threadpool(_fetch_volumes, array_name, search, limit)
    return [_row_to_volume(r) for r in rows]


@router.patch("/{array_name}/{volume_name}/notes")
async def update_volume_notes(array_name: str, volume_name: str, body: NoteUpdate):
    """Update the notes field for a specific volume."""
    def _update(an: str, vn: str, notes: Optional[str]):
        with get_db_cursor() as cursor:
            cursor.execute(
                f"UPDATE {SCHEMA}.volumes_cache SET notes=? WHERE array_name=? AND volume_name=?",
                (notes, an, vn),
            )
            if cursor.rowcount == 0:
                return False
            cursor.connection.commit()
            return True

    ok = await run_in_threadpool(_update, array_name, volume_name, body.notes)
    if not ok:
        raise HTTPException(status_code=404, detail="Volume not found")
    return {"success": True}


def _safe_json(val) -> list:
    if not val:
        return []
    try:
        return json.loads(val) if isinstance(val, str) else val
    except Exception:
        return []


def _row_to_volume(row: dict) -> VolumeSchema:
    return VolumeSchema(
        array_name=row.get("array_name", ""),
        vendor=row.get("vendor", "pure"),
        volume_name=row.get("volume_name", ""),
        size_bytes=row.get("size"),
        used_bytes=row.get("used"),
        data_reduction=row.get("data_reduction"),
        total_reduction=row.get("total_reduction"),
        thin_provisioning=row.get("thin_provisioning"),
        snapshots=row.get("snapshots"),
        created=row.get("created"),
        serial=row.get("serial"),
        hosts=_safe_json(row.get("hosts")),
        host_groups=_safe_json(row.get("host_groups")),
        protection_groups=_safe_json(row.get("protection_groups")),
        notes=row.get("notes"),
        last_updated=str(row["last_updated"]) if row.get("last_updated") else None,
    )
