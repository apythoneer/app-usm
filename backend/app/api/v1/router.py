"""
Main API v1 router — assembles all sub-routers.
"""

from fastapi import APIRouter

from app.api.v1 import arrays, volumes, hosts, alerts, analytics, scheduler, settings

router = APIRouter()

router.include_router(arrays.router)
router.include_router(volumes.router)
router.include_router(hosts.router)
router.include_router(alerts.router)
router.include_router(analytics.router)
router.include_router(scheduler.router)
router.include_router(settings.router)
