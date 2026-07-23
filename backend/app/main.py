"""
Unified Storage Monitoring v3 — FastAPI entry point.
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

    # A placeholder signing secret must never reach production — once auth lands,
    # forged JWTs would be trivial. Hard-fail on boot unless in debug. (Both the
    # config default and the compose default start with "CHANGE_ME".)
    if not settings.debug and settings.secret_key.startswith("CHANGE_ME"):
        raise RuntimeError(
            "SECRET_KEY is still the placeholder. Set a real SECRET_KEY "
            "(e.g. `openssl rand -hex 32`) in the environment before production, "
            "or set DEBUG=true for local development."
        )

    db_ok = test_connection()
    logger.info(f"Database connection: {'OK' if db_ok else 'FAILED'}")

    if db_ok:
        init_database()
        from app.api.v1.settings import load_persisted_settings
        load_persisted_settings()

    # Initialize SQLite read cache
    from app.db.cache import init_cache
    try:
        init_cache()
    except Exception as e:
        logger.warning(f"SQLite cache init failed (non-fatal): {e}")

    # Only the collector-role process runs the scheduler. An API-only process
    # (RUN_SCHEDULER=false) skips it entirely, so it can run multiple uvicorn
    # workers without every worker duplicating collection.
    scheduler = None
    if settings.run_scheduler:
        scheduler = build_scheduler()
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info("Scheduler STARTED (RUN_SCHEDULER=true)")
    else:
        logger.info("Scheduler DISABLED (RUN_SCHEDULER=false) — API-only role")

    yield

    if scheduler is not None:
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
    # Swagger/ReDoc are a self-documenting map of every (unauthenticated) mutating
    # endpoint. Expose them only in debug; hide them in production until auth lands.
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
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

    # SQLite cache stats
    try:
        from app.db.cache import cache_stats
        sqlite_cache = cache_stats()
    except Exception:
        sqlite_cache = {}

    return {
        "status": "healthy" if db_ok else "degraded",
        "version": settings.app_version,
        "database": db_ok,
        "sqlite_cache": sqlite_cache,
        "collectors": CollectorRegistry.summary(),
        "credential_cache": cache_summary(),
    }
