"""
Chat API — natural language Q&A over storage infrastructure data.
Uses local Ollama LLM for Text-to-SQL (no data leaves the host).
"""

import logging
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, Field

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.services.chat import ChatService

logger = logging.getLogger("usm.api.chat")
router = APIRouter(prefix="/chat", tags=["chat"])
settings = get_settings()

# One cached service per backend. 'local' is always available; 'dgx' is built
# lazily and only if configured, so an unconfigured DGX never blocks startup.
_services: Dict[str, ChatService] = {"local": ChatService.for_backend("local")}


def _get_service(backend: str) -> ChatService:
    if backend not in ("local", "dgx"):
        raise HTTPException(status_code=400, detail=f"Unknown chat backend '{backend}'")
    if backend not in _services:
        try:
            _services[backend] = ChatService.for_backend(backend)
        except ValueError as e:
            # DGX not configured (no CHAT_DGX_BASE_URL). 503 = capability absent.
            raise HTTPException(status_code=503, detail=str(e))
    return _services[backend]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    context: Optional[List[Dict]] = Field(default=None, description="Previous Q&A turns for multi-turn conversation")
    # WARNING: 'dgx' routes to an OFF-NETWORK model (ai.racktocloud.com). The
    # local backend keeps all data on the host; the DGX backend sends the
    # question AND the SQL result rows out for answer formatting. Defaults to
    # 'local' so egress is always an explicit choice.
    backend: Literal["local", "dgx"] = Field(default="local")


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

    svc = _get_service(req.backend)
    if req.backend == "dgx":
        # Make the off-network hop auditable — this is the one path that leaves
        # the host with real inventory data.
        logger.warning("chat routed to DGX (off-network): %r", req.message[:80])

    result = await run_in_threadpool(svc.ask, req.message, req.context)
    return ChatResponse(**result)


@router.get("/status")
async def chat_status():
    """Chat availability, and which backends are configured."""
    status = await run_in_threadpool(_services["local"].check_ollama)
    return {
        "chat_enabled": settings.chat_enabled,
        "ollama": status,
        # Lets the UI show/hide the DGX toggle instead of guessing.
        "dgx_configured": bool(settings.chat_dgx_base_url),
        "dgx_model": settings.chat_dgx_model if settings.chat_dgx_base_url else None,
    }
