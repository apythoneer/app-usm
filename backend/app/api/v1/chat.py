"""
Chat API — natural language Q&A over storage infrastructure data.
Uses the local Ollama LLM for Text-to-SQL. No data leaves the host.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.services import chat_feedback
from app.services.chat import ChatService

logger = logging.getLogger("usm.api.chat")
router = APIRouter(prefix="/chat", tags=["chat"])
settings = get_settings()

# Single local chat service (Ollama on this host).
_chat = ChatService.for_backend("local")


# Concurrency guard. asyncio is single-threaded, so the check-then-increment
# below is race-free (no await between them). Prevents a burst of chat requests
# from starving the single-worker API — the cause of the 2026-07-16 hang.
_chat_inflight = 0


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    context: Optional[List[Dict]] = Field(default=None, description="Previous Q&A turns for multi-turn conversation")


class ChatResponse(BaseModel):
    answer: str
    sql: Optional[str] = None
    rows: int = 0
    duration_ms: int = 0
    model: str = ""
    error: Optional[str] = None
    id: Optional[int] = None          # chat_log id — send it back with /chat/feedback
    route: Optional[str] = None       # intent name | sql | forecast


class FeedbackRequest(BaseModel):
    id: int
    rating: Literal[1, -1]
    # after a 👎: the question type the user wanted ('none' = none of them fit)
    wanted_intent: Optional[str] = Field(default=None, max_length=50)
    comment: Optional[str] = Field(default=None, max_length=1000)


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Ask a natural language question about storage infrastructure.
    The question is converted to SQL by a local LLM, executed against the DB,
    and the results are formatted into a natural language answer.
    No data leaves the host — all inference runs locally via Ollama.
    """
    global _chat_inflight
    _check_capacity()
    _chat_inflight += 1
    try:
        result = await run_in_threadpool(_chat.ask, req.message, req.context)
        result["id"] = await run_in_threadpool(chat_feedback.log_answer, req.message, result)
        return ChatResponse(**result)
    finally:
        _chat_inflight -= 1


def _check_capacity() -> None:
    if not settings.chat_enabled:
        raise HTTPException(status_code=503, detail="Chat feature is disabled")
    if _chat_inflight >= settings.chat_max_concurrent:
        # Fast-fail rather than pile up and starve the API. 503 + Retry-After.
        raise HTTPException(
            status_code=503,
            detail="The assistant is busy with other questions — please try again in a moment.",
            headers={"Retry-After": "10"},
        )


_HEARTBEAT_S = 10  # SSE comment every 10s keeps proxies from timing out slow answers
_stream_tasks: set = set()


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """
    Same as POST /chat, streamed as Server-Sent Events:
      event: status  data: "Writing a SQL query…"       (progress)
      event: sql     data: "SELECT …"                  (once generated)
      event: token   data: "partial answer text"       (answer as it is written)
      event: done    data: {ChatResponse incl. id}     (final; always last on success)
      event: error   data: {"detail": "…"}
    A ': ping' comment is sent every 10 s, so the 120 s proxy read timeout only
    applies to silence, never to a slow answer.
    """
    global _chat_inflight
    _check_capacity()
    _chat_inflight += 1
    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[tuple]" = asyncio.Queue()

    def emit(kind: Optional[str], data: Any) -> None:  # called from the worker thread
        loop.call_soon_threadsafe(queue.put_nowait, (kind, data))

    async def work() -> None:
        global _chat_inflight
        try:
            emit("status", "Matching your question…")
            result = await run_in_threadpool(_chat.ask, req.message, req.context, emit)
            result["id"] = await run_in_threadpool(chat_feedback.log_answer, req.message, result)
            emit("done", ChatResponse(**result).model_dump())
        except Exception as e:  # never leave the client hanging
            logger.error("chat stream failed: %s", e)
            emit("error", {"detail": str(e)})
        finally:
            _chat_inflight -= 1  # freed when the work ends, even if the client left
            emit(None, None)

    # Started now, not inside the generator: the slot accounting must not depend
    # on the client reading the stream. Keep a reference so it isn't GC'd mid-run.
    task = asyncio.create_task(work())
    _stream_tasks.add(task)
    task.add_done_callback(_stream_tasks.discard)

    async def events():
        while True:
            try:
                kind, data = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_S)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if kind is None:
                return
            yield f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/status")
async def chat_status():
    """Chat availability."""
    status = await run_in_threadpool(_chat.check_ollama)
    return {
        "chat_enabled": settings.chat_enabled,
        "ollama": status,
    }


# ── Feedback loop (app/services/chat_feedback.py) ─────────────────────────────

@router.get("/intents")
async def chat_intents_list():
    """Question types the assistant answers directly (for the 👎 'what did you want?' picker)."""
    from app.services.chat_intents import INTENTS
    return [{"name": i.name, "description": i.description, "example": i.examples[0]} for i in INTENTS]


@router.post("/feedback")
async def chat_feedback_post(req: FeedbackRequest):
    """👍/👎 on an answer; a 👎 with wanted_intent teaches the router that phrasing."""
    try:
        return await run_in_threadpool(chat_feedback.record_feedback, req.id, req.rating,
                                       req.wanted_intent, req.comment)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/feedback")
async def chat_feedback_review(limit: int = 50,
                               only: Literal["all", "down", "fallthrough", "unrated-fallthrough"] = "all"):
    """Recent questions with route + rating, for reviewing misses."""
    return await run_in_threadpool(chat_feedback.review, limit, only)


@router.get("/examples")
async def chat_examples_list():
    """Phrasings learned from feedback (active)."""
    return await run_in_threadpool(chat_feedback.list_examples)


@router.delete("/examples/{example_id}")
async def chat_examples_delete(example_id: int):
    """Retire a learned phrasing (soft delete)."""
    if not await run_in_threadpool(chat_feedback.deactivate_example, example_id):
        raise HTTPException(status_code=404, detail="example not found")
    return {"id": example_id, "active": False}
