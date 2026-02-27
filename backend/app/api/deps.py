"""
FastAPI dependencies — DB session, optional auth (enforced in Phase 3).
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.security import decode_access_token
from app.core.config import get_settings

settings = get_settings()

# Auth is wired but NOT enforced yet (no Depends(get_current_user) on routes).
# Flip AUTH_REQUIRED=true in .env to enforce in Phase 3.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login", auto_error=False)


async def get_current_user(token: str = Depends(oauth2_scheme)):
    """Returns username or raises 401. Wire into routes when auth is ready."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = decode_access_token(token)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username
