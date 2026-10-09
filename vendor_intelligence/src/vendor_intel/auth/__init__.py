"""Email OTP auth: Postgres models, sessions (24h), audit trail."""
from __future__ import annotations

from vendor_intel.auth.service import (
    AuthError,
    list_active_sessions,
    list_auth_events,
    logout,
    request_otp,
    validate_session,
    verify_otp,
)
from vendor_intel.auth.db import get_engine, init_db

__all__ = [
    "AuthError",
    "get_engine",
    "init_db",
    "list_active_sessions",
    "list_auth_events",
    "logout",
    "request_otp",
    "validate_session",
    "verify_otp",
]
