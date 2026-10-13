"""
Chat API — natural language Q&A over storage infrastructure data.
Uses the local Ollama LLM for Text-to-SQL. No data leaves the host.
"""

import logging
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.core.config import get_settings
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


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Ask a natural language question about storage infrastructure.
    The question is converted to SQL by a local LLM, executed against the DB,
    and the results are formatted into a natural language answer.
    No data leaves the host — all inference runs locally via Ollama.
    """
    if not settings.chat_enabled:
        raise HTTPException(status_code=503, detail="Chat feature is disabled")

    global _chat_inflight
    if _chat_inflight >= settings.chat_max_concurrent:
        # Fast-fail rather than pile up and starve the API. 503 + Retry-After.
        raise HTTPException(
            status_code=503,
            detail="The assistant is busy with other questions — please try again in a moment.",
            headers={"Retry-After": "10"},
        )
    _chat_inflight += 1
    try:
        result = await run_in_threadpool(_chat.ask, req.message, req.context)
        return ChatResponse(**result)
    finally:
        _chat_inflight -= 1


@router.get("/status")
async def chat_status():
    """Chat availability."""
    status = await run_in_threadpool(_chat.check_ollama)
    return {
        "chat_enabled": settings.chat_enabled,
        "ollama": status,
    }
