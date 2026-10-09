"""JWT access tokens (HS256) for authenticated API / Streamlit sessions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from vendor_intel.auth.config import get_auth_settings


def create_access_token(
    *,
    user_id: int,
    email: str,
    role: str,
    jti: str,
    expires_at: datetime,
) -> str:
    settings = get_auth_settings()
    algo = settings.jwt_algorithm or "HS256"
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "jti": jti,
        "iat": datetime.now(timezone.utc),
        "exp": expires_at,
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=algo)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate signature + exp. Raises jwt.PyJWTError on failure."""
    settings = get_auth_settings()
    algo = settings.jwt_algorithm or "HS256"
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[algo],
        options={"require": ["exp", "sub", "jti"]},
    )
