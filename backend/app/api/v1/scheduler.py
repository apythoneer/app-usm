"""
Scheduler API — inspect and trigger collector jobs.
"""

from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.collectors.registry import CollectorRegistry
from app.collectors.scheduler import get_job_status


class IntervalUpdate(BaseModel):
    seconds: int

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.get("/status")
async def scheduler_status(request: Request):
    """Current status of all collector jobs."""
    scheduler = request.app.state.scheduler
    registered = CollectorRegistry.summary()
    job_status = get_job_status()

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
                "trigger": str(job.trigger),
                "status": job_status.get(job.id, {}),
            }
        )

    return {
        "running": scheduler.running,
        "registered_collectors": registered,
        "jobs": jobs,
    }


@router.post("/jobs/{job_id}/run")
async def run_job_now(job_id: str, request: Request):
    """Trigger a job to run immediately."""
    scheduler = request.app.state.scheduler
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    job.modify(next_run_time=__import__("datetime").datetime.now())
    return {"message": f"Job '{job_id}' triggered"}


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str, request: Request):
    scheduler = request.app.state.scheduler
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    scheduler.pause_job(job_id)
    return {"message": f"Job '{job_id}' paused"}


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str, request: Request):
    scheduler = request.app.state.scheduler
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    scheduler.resume_job(job_id)
    return {"message": f"Job '{job_id}' resumed"}


@router.put("/jobs/{job_id}/interval")
async def update_job_interval(job_id: str, body: IntervalUpdate, request: Request):
    """Update a job's polling interval (seconds) — takes effect immediately."""
    from apscheduler.triggers.interval import IntervalTrigger
    scheduler = request.app.state.scheduler
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    if body.seconds < 10:
        raise HTTPException(status_code=400, detail="Minimum interval is 10 seconds")
    scheduler.reschedule_job(job_id, trigger=IntervalTrigger(seconds=body.seconds))
    return {"message": f"Job '{job_id}' rescheduled every {body.seconds}s"}
