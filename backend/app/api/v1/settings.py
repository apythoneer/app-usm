"""
Settings API — read/write runtime configuration.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from app.core.config import get_settings
from app.collectors.registry import CollectorRegistry

router = APIRouter(prefix="/settings", tags=["settings"])
settings = get_settings()


class SettingsResponse(BaseModel):
    app_name: str
    version: str
    db_server: str
    db_database: str
    db_schema: str
    metrics_interval: int
    volumes_interval: int
    alerts_interval: int
    teams_webhook_configured: bool
    registered_vendors: list
    registered_collectors: dict


@router.get("", response_model=SettingsResponse)
async def get_app_settings():
    """Return current application settings (non-sensitive)."""
    return SettingsResponse(
        app_name=settings.app_name,
        version=settings.app_version,
        db_server=settings.sql_server,
        db_database=settings.sql_database,
        db_schema=settings.db_schema,
        metrics_interval=settings.metrics_interval,
        volumes_interval=settings.volumes_interval,
        alerts_interval=settings.alerts_interval,
        teams_webhook_configured=bool(settings.teams_webhook_url),
        registered_vendors=CollectorRegistry.vendors(),
        registered_collectors=CollectorRegistry.summary(),
    )
