"""OTP + session token helpers."""
from __future__ import annotations

import hashlib
import hmac
import secrets


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def generate_otp(digits: int = 6) -> str:
    upper = 10**digits
    return f"{secrets.randbelow(upper):0{digits}d}"


def hash_secret(value: str) -> str:
    """One-way hash for OTP codes and session tokens (SHA-256 hex)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def generate_session_token() -> str:
    """Opaque random id used as JWT jti (session id)."""
    return secrets.token_urlsafe(32)


def generate_jti() -> str:
    return secrets.token_urlsafe(24)


def _bcrypt():
    # Lazy — email-only login doesn't need bcrypt; avoids import crash if
    # the package isn't in an older base image yet.
    import bcrypt as _bcrypt_mod

    return _bcrypt_mod


def hash_password(plain: str) -> str:
    """Salted, slow hash (bcrypt) — NOT the same as hash_secret() above, which
    is a fast SHA-256 meant only for short-lived OTP codes / session ids.
    A real password needs a deliberately slow, per-hash-salted algorithm so
    a leaked users table can't be brute-forced with a rainbow table."""
    bcrypt = _bcrypt()
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        bcrypt = _bcrypt()
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
