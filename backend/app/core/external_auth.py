"""
Authentication for the external (partner) read-only API — /api/ext/v1.

Design notes
------------
- Keys are compared by SHA-256 hash with a constant-time compare, so neither the
  plaintext nor a timing side-channel is exposed. Plaintext keys never touch disk;
  only their hashes live in EXTERNAL_API_KEYS.
- Each key carries scopes; endpoints declare the scope they require via
  `Depends(require_scope("hosts:read"))`, giving per-consumer least privilege.
- Every accepted request is logged (client name, method, path) for audit, and a
  lightweight per-key sliding-window rate limit guards against runaway callers.
  The limiter is in-process (per uvicorn worker), which is plenty for a handful
  of partner consumers; move to Redis if this ever needs to be global.
"""

import hashlib
import hmac
import logging
import time
from collections import defaultdict, deque

from fastapi import Depends, Header, HTTPException, Request, status
from typing import Optional

from app.core.config import get_settings, ExternalApiKey

logger = logging.getLogger("usm.external_api")
settings = get_settings()


def _sha256_hex(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- per-key sliding-window rate limit (in-process) -------------------------
_rl_hits: dict = defaultdict(deque)
_RL_WINDOW = 60.0  # seconds


def _rate_ok(name: str) -> bool:
    limit = settings.external_api_rate_limit
    if limit <= 0:
        return True
    now = time.monotonic()
    dq = _rl_hits[name]
    while dq and dq[0] <= now - _RL_WINDOW:
        dq.popleft()
    if len(dq) >= limit:
        return False
    dq.append(now)
    return True


def _extract_token(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if x_api_key:
        return x_api_key.strip()
    return None


async def authenticate(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> ExternalApiKey:
    """Resolve the caller to a configured key, or raise 401/429."""
    keys = settings.external_api_keys
    if not keys:
        # Surface deliberately: the surface exists but no keys are provisioned.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="External API is not configured.",
        )

    presented = _extract_token(authorization, x_api_key)
    peer = request.client.host if request.client else "?"
    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Send 'Authorization: Bearer <key>' or 'X-API-Key: <key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    presented_hash = _sha256_hex(presented)
    matched: Optional[ExternalApiKey] = None
    for k in keys:
        if hmac.compare_digest(presented_hash, (k.key_sha256 or "").lower()):
            matched = k
            break

    if matched is None:
        logger.warning("ext-api: rejected key from %s", peer)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not _rate_ok(matched.name):
        logger.warning("ext-api: rate limit hit for client=%s", matched.name)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded.",
            headers={"Retry-After": "60"},
        )

    request.state.ext_client = matched
    return matched


def require_scope(scope: str):
    """Dependency factory: require `scope` on the authenticated key."""

    async def _dep(request: Request, principal: ExternalApiKey = Depends(authenticate)) -> ExternalApiKey:
        if scope not in principal.scopes and "*" not in principal.scopes:
            logger.warning("ext-api: client=%s lacks scope %s", principal.name, scope)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This key is not authorized for '{scope}'.",
            )
        logger.info("ext-api: client=%s %s %s", principal.name, request.method, request.url.path)
        return principal

    return _dep
