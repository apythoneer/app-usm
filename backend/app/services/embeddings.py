"""
Text embeddings from the configured Ollama (OLLAMA_EMBED_MODEL, default
nomic-embed-text). Same endpoint + auth as chat: OLLAMA_BASE_URL, with the
sandbox-proxy bearer token when OLLAMA_CRED_KEY / OLLAMA_API_KEY is set.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

import requests

from app.core.config import get_settings

settings = get_settings()


def query_prefix(model: Optional[str] = None) -> str:
    """nomic-embed-text is trained with task prefixes; other models get none."""
    return "search_query: " if "nomic" in (model or settings.ollama_embed_model) else ""


def document_prefix(model: Optional[str] = None) -> str:
    return "search_document: " if "nomic" in (model or settings.ollama_embed_model) else ""


def embed(texts: Sequence[str], timeout: Optional[float] = None) -> List[List[float]]:
    """Embed texts in one /api/embed call. Raises on any failure (caller falls back)."""
    # Lazy import: chat.py owns the proxy-token cache, and importing it at module
    # load would make this helper drag in the DB layer.
    from app.services.chat import _invalidate_ollama_token, _local_ollama_headers

    resp = requests.post(
        f"{settings.ollama_base_url.rstrip('/')}/api/embed",
        json={"model": settings.ollama_embed_model, "input": list(texts), "keep_alive": "24h"},
        headers=_local_ollama_headers(),
        timeout=timeout or settings.chat_embed_timeout,
    )
    if resp.status_code == 401:
        _invalidate_ollama_token()  # rotated token: re-read KeePass next time
    resp.raise_for_status()
    vecs = resp.json().get("embeddings") or []
    if len(vecs) != len(texts) or not all(vecs):
        raise ValueError(f"embed returned {len(vecs)} vectors for {len(texts)} inputs")
    return vecs


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
