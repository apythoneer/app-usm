"""
Chat API — natural language Q&A over storage infrastructure data.
Uses local Ollama LLM for Text-to-SQL (no data leaves the host).
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

_chat_service = ChatService()


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

    result = await run_in_threadpool(
        _chat_service.ask, req.message, req.context
    )
    return ChatResponse(**result)


@router.get("/status")
async def chat_status():
    """Check if the chat service (Ollama) is available and model is loaded."""
    status = await run_in_threadpool(_chat_service.check_ollama)
    return {
        "chat_enabled": settings.chat_enabled,
        "ollama": status,
    }
