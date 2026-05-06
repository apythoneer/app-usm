"""
Unified Storage Monitoring v2 — FastAPI entry point.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api.v1.router import router as api_v1_router
from app.collectors.scheduler import build_scheduler
from app.db.session import test_connection, init_database

settings = get_settings()

# ------------------------------------------------------------------ logging
log_dir = settings.log_dir
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(log_dir, "usm_backend.log")),
    ],
)
logger = logging.getLogger("usm")


# ------------------------------------------------------------------ lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start scheduler on startup, shut it down on exit."""
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")

    db_ok = test_connection()
    logger.info(f"Database connection: {'OK' if db_ok else 'FAILED'}")

    if db_ok:
        init_database()
        from app.api.v1.settings import load_persisted_settings
        load_persisted_settings()

    scheduler = build_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler

    yield

    logger.info("Shutting down scheduler...")
    scheduler.shutdown(wait=False)


# ------------------------------------------------------------------ app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Storage Intelligence Platform — vendor-agnostic monitoring for "
        "Pure Storage, NetApp, Commvault and more."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router, prefix=settings.api_v1_prefix)


# ------------------------------------------------------------------ health
@app.get("/ping", tags=["health"])
async def ping():
    """Lightweight liveness probe — used by Docker healthcheck."""
    return {"ok": True}


@app.get("/health", tags=["health"])
async def health():
    """Deep health check — includes DB and collector status (may be slow if KeePass is unavailable)."""
    from app.collectors.registry import CollectorRegistry
    from fastapi.concurrency import run_in_threadpool

    from app.services.keepass import cache_summary

    db_ok = await run_in_threadpool(test_connection)
    return {
        "status": "healthy" if db_ok else "degraded",
        "version": settings.app_version,
        "database": db_ok,
        "collectors": CollectorRegistry.summary(),
        "credential_cache": cache_summary(),
    }
