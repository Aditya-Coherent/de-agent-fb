"""OTP request/verify, 24h sessions, auth audit."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from vendor_intel.auth.config import AuthSettings, get_auth_settings
from vendor_intel.auth.db import session_scope
from vendor_intel.auth.emailer import send_otp_email
from vendor_intel.auth.models import AuthEvent, OtpChallenge, User, UserSession
from vendor_intel.auth.jwt_tokens import create_access_token, decode_access_token
from vendor_intel.auth.security import (
    constant_time_equals,
    generate_jti,
    generate_otp,
    hash_password,
    hash_secret,
    normalize_email,
    verify_password,
)


class AuthError(Exception):
    def __init__(self, message: str, *, code: str = "auth_error"):
        super().__init__(message)
        self.code = code
        self.message = message


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _domain_allowed(email: str, settings: AuthSettings) -> bool:
    if not settings.allowed_email_domains:
        return True
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in settings.allowed_email_domains


def _audit(
    db: Session,
    *,
    event: str,
    email: str | None = None,
    user_id: int | None = None,
    ip: str | None = None,
    detail: str | None = None,
) -> None:
    db.add(
        AuthEvent(
            user_id=user_id,
            email=email,
            event=event,
            ip=ip,
            detail=detail,
        )
    )


def _get_or_create_user(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    admin_emails = {
        e.strip().lower()
        for e in (os.getenv("AUTH_ADMIN_EMAILS") or "").split(",")
        if e.strip()
    }
    if user:
        if email in admin_emails and user.role != "admin":
            user.role = "admin"
        return user
    role = "admin" if email in admin_emails else "researcher"
    user = User(email=email, role=role, is_active=True)
    db.add(user)
    db.flush()
    return user


@dataclass
class SessionInfo:
    token: str
    user_id: int
    email: str
    role: str
    expires_at: datetime


@dataclass
class CurrentUser:
    user_id: int
    email: str
    role: str
    session_id: int
    expires_at: datetime


def request_otp(email: str, *, ip: str | None = None) -> dict[str, Any]:
    """Send a login OTP to email. Creates the user row on first successful send."""
    settings = get_auth_settings()
    email_n = normalize_email(email)
    if "@" not in email_n or "." not in email_n.rsplit("@", 1)[-1]:
        raise AuthError("Enter a valid email address.", code="invalid_email")
    if not _domain_allowed(email_n, settings):
        raise AuthError(
            "This email domain is not allowed. Use your company email.",
            code="domain_not_allowed",
        )

    now = _utcnow()
    with session_scope() as db:
        window_start = now - timedelta(minutes=settings.otp_window_minutes)
        recent = db.scalars(
            select(OtpChallenge).where(
                OtpChallenge.email == email_n,
                OtpChallenge.created_at >= window_start,
            )
        ).all()
        if len(recent) >= settings.otp_max_per_window:
            _audit(
                db,
                event="otp_rate_limited",
                email=email_n,
                ip=ip,
                detail=f"{len(recent)} in window",
            )
            raise AuthError(
                "Too many codes requested. Try again in a few minutes.",
                code="rate_limited",
            )

        # Enforce short resend cooldown based on newest open challenge
        open_challenges = [
            c for c in recent if c.consumed_at is None and c.expires_at > now
        ]
        if open_challenges:
            newest = max(open_challenges, key=lambda c: c.created_at)
            created = newest.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            elapsed = (now - created).total_seconds()
            if elapsed < settings.otp_resend_seconds:
                wait = int(settings.otp_resend_seconds - elapsed)
                raise AuthError(
                    f"Please wait {wait}s before requesting another code.",
                    code="resend_cooldown",
                )

        # Invalidate previous open challenges
        for c in db.scalars(
            select(OtpChallenge).where(
                OtpChallenge.email == email_n,
                OtpChallenge.consumed_at.is_(None),
            )
        ).all():
            c.consumed_at = now

        code = generate_otp(6)
        challenge = OtpChallenge(
            email=email_n,
            code_hash=hash_secret(code),
            expires_at=now + timedelta(minutes=settings.otp_ttl_minutes),
            attempts=0,
            request_ip=ip,
        )
        db.add(challenge)
        db.flush()

        try:
            channel = send_otp_email(settings, email_n, code)
        except Exception as exc:
            _audit(
                db,
                event="otp_failed",
                email=email_n,
                ip=ip,
                detail=f"send_failed:{exc}",
            )
            raise AuthError(
                "Could not send the email code. Try again or contact admin.",
                code="email_send_failed",
            ) from exc

        _audit(
            db,
            event="otp_sent",
            email=email_n,
            ip=ip,
            detail=f"via:{channel}",
        )

    return {
        "ok": True,
        "email": email_n,
        "expires_in_minutes": settings.otp_ttl_minutes,
        "message": "If that email is valid, a login code was sent.",
    }


def _issue_session(
    db: Session,
    *,
    email_n: str,
    ip: str | None,
    user_agent: str | None,
    audit_detail: str,
) -> SessionInfo:
    """Create user (if needed) + JWT session. Caller must be inside session_scope."""
    settings = get_auth_settings()
    now = _utcnow()
    user = _get_or_create_user(db, email_n)
    if not user.is_active:
        _audit(
            db,
            event="otp_failed",
            email=email_n,
            user_id=user.id,
            ip=ip,
            detail="inactive_user",
        )
        raise AuthError("This account is disabled.", code="inactive")

    jti = generate_jti()
    expires_at = now + timedelta(hours=settings.session_hours)
    session = UserSession(
        user_id=user.id,
        token_hash=hash_secret(jti),
        expires_at=expires_at,
        last_seen_at=now,
        ip=ip,
        user_agent=(user_agent or "")[:400] or None,
    )
    db.add(session)
    user.last_login_at = now
    _audit(db, event="login", email=email_n, user_id=user.id, ip=ip, detail=audit_detail)
    db.flush()

    jwt_token = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        jti=jti,
        expires_at=expires_at,
    )
    return SessionInfo(
        token=jwt_token,
        user_id=user.id,
        email=user.email,
        role=user.role,
        expires_at=expires_at,
    )


def login_without_otp(
    email: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SessionInfo:
    """Work-email login: domain check only, no password/OTP.

    Current production path until company SMTP + password/OTP are enabled.
    (Password/OTP helpers remain in this module for that later switch.)
    """
    settings = get_auth_settings()

    email_n = normalize_email(email)
    if "@" not in email_n or "." not in email_n.rsplit("@", 1)[-1]:
        raise AuthError("Enter a valid email address.", code="invalid_email")
    if not _domain_allowed(email_n, settings):
        raise AuthError(
            "This email domain is not allowed. Use your company email.",
            code="domain_not_allowed",
        )

    with session_scope() as db:
        return _issue_session(
            db,
            email_n=email_n,
            ip=ip,
            user_agent=user_agent,
            audit_detail="skip_otp",
        )


def _consume_valid_otp(db: Session, email_n: str, code: str, *, ip: str | None) -> None:
    """Validate + mark-consumed the newest open OTP challenge for this email.

    Raises AuthError on any failure. Caller must already be inside session_scope.
    Shared by verify_otp() (login, legacy path) and verify_signup_otp() /
    login_step2_otp() — the OTP-checking rules (expiry, attempt cap, hash
    compare) are identical regardless of what happens after a successful check.
    """
    settings = get_auth_settings()
    code_clean = (code or "").strip().replace(" ", "")
    if not code_clean.isdigit() or len(code_clean) != 6:
        raise AuthError("Enter the 6-digit code from your email.", code="invalid_otp_format")

    now = _utcnow()
    challenge = db.scalar(
        select(OtpChallenge)
        .where(OtpChallenge.email == email_n, OtpChallenge.consumed_at.is_(None))
        .order_by(OtpChallenge.created_at.desc())
    )
    if not challenge:
        _audit(db, event="otp_failed", email=email_n, ip=ip, detail="no_challenge")
        raise AuthError("No active code. Request a new one.", code="no_challenge")

    expires = challenge.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < now:
        challenge.consumed_at = now
        _audit(db, event="otp_failed", email=email_n, ip=ip, detail="expired")
        raise AuthError("That code has expired. Request a new one.", code="otp_expired")

    if challenge.attempts >= settings.otp_max_attempts:
        challenge.consumed_at = now
        _audit(db, event="otp_failed", email=email_n, ip=ip, detail="max_attempts")
        raise AuthError("Too many wrong attempts. Request a new code.", code="otp_locked")

    if not constant_time_equals(challenge.code_hash, hash_secret(code_clean)):
        challenge.attempts += 1
        _audit(
            db,
            event="otp_failed",
            email=email_n,
            ip=ip,
            detail=f"bad_code_attempt_{challenge.attempts}",
        )
        left = settings.otp_max_attempts - challenge.attempts
        raise AuthError(
            f"Incorrect code. {left} attempt(s) left." if left > 0 else "Incorrect code.",
            code="otp_mismatch",
        )

    challenge.consumed_at = now


def verify_otp(
    email: str,
    code: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SessionInfo:
    """Legacy path (AUTH_SKIP_OTP=false, no password): verify OTP, create session directly."""
    email_n = normalize_email(email)
    with session_scope() as db:
        _consume_valid_otp(db, email_n, code, ip=ip)
        return _issue_session(db, email_n=email_n, ip=ip, user_agent=user_agent, audit_detail="otp_jwt")


# ---------------------------------------------------------------------------
# Sign-up (email verify -> set password) and password+OTP login
# ---------------------------------------------------------------------------

def signup_request_otp(email: str, *, ip: str | None = None) -> dict[str, Any]:
    """Step 1 of sign-up: send OTP. Rejects if the account already has a password."""
    email_n = normalize_email(email)
    with session_scope() as db:
        existing = db.scalar(select(User).where(User.email == email_n))
        if existing and existing.password_hash:
            raise AuthError(
                "An account with this email already exists. Log in instead.",
                code="already_registered",
            )
    return request_otp(email_n, ip=ip)


def verify_signup_otp(email: str, code: str, *, ip: str | None = None) -> None:
    """Step 2 of sign-up: verify the code, mark email_verified. No session yet —
    password still needs to be set (complete_signup)."""
    email_n = normalize_email(email)
    settings = get_auth_settings()
    if not _domain_allowed(email_n, settings):
        raise AuthError("This email domain is not allowed.", code="domain_not_allowed")
    with session_scope() as db:
        _consume_valid_otp(db, email_n, code, ip=ip)
        user = _get_or_create_user(db, email_n)
        user.email_verified = True
        _audit(db, event="signup_otp_verified", email=email_n, user_id=user.id, ip=ip)


def complete_signup(
    email: str,
    password: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SessionInfo:
    """Step 3 of sign-up: set the password now that email_verified is true.
    Issues a session immediately — no need to log in again right after signing up."""
    email_n = normalize_email(email)
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters.", code="weak_password")

    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email_n))
        if not user or not user.email_verified:
            raise AuthError(
                "Verify your email with the OTP code first.",
                code="email_not_verified",
            )
        if user.password_hash:
            raise AuthError(
                "This account already has a password. Log in instead.",
                code="already_registered",
            )
        user.password_hash = hash_password(password)
        _audit(db, event="signup_complete", email=email_n, user_id=user.id, ip=ip)
        return _issue_session(
            db, email_n=email_n, ip=ip, user_agent=user_agent, audit_detail="signup_complete"
        )


def login_step1_password(email: str, password: str, *, ip: str | None = None) -> dict[str, Any]:
    """Step 1 of login: check password, then send a fresh OTP (2nd factor)."""
    email_n = normalize_email(email)
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email_n))
        # Same generic error for "no such user" and "wrong password" — do not
        # reveal which emails have accounts.
        if not user or not user.password_hash:
            _audit(db, event="login_failed", email=email_n, ip=ip, detail="no_account")
            raise AuthError("Incorrect email or password.", code="bad_credentials")
        if not user.is_active:
            raise AuthError("This account is disabled.", code="inactive")
        if not verify_password(password, user.password_hash):
            _audit(db, event="login_failed", email=email_n, user_id=user.id, ip=ip, detail="bad_password")
            raise AuthError("Incorrect email or password.", code="bad_credentials")
        _audit(db, event="login_password_ok", email=email_n, user_id=user.id, ip=ip)
    return request_otp(email_n, ip=ip)


def login_step2_otp(
    email: str,
    code: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SessionInfo:
    """Step 2 of login: verify the OTP sent after a correct password, issue session."""
    email_n = normalize_email(email)
    with session_scope() as db:
        _consume_valid_otp(db, email_n, code, ip=ip)
        return _issue_session(
            db, email_n=email_n, ip=ip, user_agent=user_agent, audit_detail="password_otp_jwt"
        )


def _session_from_jwt(token: str, db: Session):
    """Decode JWT and load matching DB session row. Raises AuthError."""
    import jwt as pyjwt

    try:
        payload = decode_access_token(token.strip())
    except pyjwt.ExpiredSignatureError:
        raise AuthError(
            "Your session expired after 24 hours. Sign in again.",
            code="session_expired",
        ) from None
    except pyjwt.PyJWTError:
        raise AuthError("Not signed in.", code="not_authenticated") from None

    jti = str(payload.get("jti") or "")
    if not jti:
        raise AuthError("Not signed in.", code="not_authenticated")

    sess = db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_secret(jti))
    )
    if not sess:
        raise AuthError("Not signed in.", code="not_authenticated")
    return sess, payload


def validate_session(token: str, *, ip: str | None = None) -> CurrentUser:
    """Validate JWT access token + DB session (revoke / 24h expiry)."""
    if not (token or "").strip():
        raise AuthError("Not signed in.", code="not_authenticated")

    now = _utcnow()
    with session_scope() as db:
        sess, payload = _session_from_jwt(token, db)

        expires = sess.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)

        user = db.get(User, sess.user_id)
        if not user or not user.is_active:
            raise AuthError("Not signed in.", code="not_authenticated")

        if sess.revoked_at is not None:
            raise AuthError("Session ended. Sign in again.", code="session_revoked")

        if expires <= now:
            sess.revoked_at = now
            _audit(
                db,
                event="expire",
                email=user.email,
                user_id=user.id,
                ip=ip,
                detail="24h_absolute_jwt",
            )
            raise AuthError(
                "Your session expired after 24 hours. Sign in again.",
                code="session_expired",
            )

        # Prefer live DB role/email over stale JWT claims
        sess.last_seen_at = now
        return CurrentUser(
            user_id=user.id,
            email=user.email,
            role=user.role,
            session_id=sess.id,
            expires_at=expires,
        )


def logout(token: str, *, ip: str | None = None) -> None:
    """Revoke JWT session (jti) in the database."""
    if not (token or "").strip():
        return
    now = _utcnow()
    with session_scope() as db:
        try:
            sess, _payload = _session_from_jwt(token, db)
        except AuthError:
            return
        if sess.revoked_at is not None:
            return
        sess.revoked_at = now
        user = db.get(User, sess.user_id)
        _audit(
            db,
            event="logout",
            email=user.email if user else None,
            user_id=sess.user_id,
            ip=ip,
            detail="user_logout_jwt",
        )


def list_auth_events(limit: int = 100) -> list[dict[str, Any]]:
    """Admin helper — recent auth events."""
    with session_scope() as db:
        rows = db.scalars(
            select(AuthEvent).order_by(AuthEvent.created_at.desc()).limit(limit)
        ).all()
        return [
            {
                "id": r.id,
                "user_id": r.user_id,
                "email": r.email,
                "event": r.event,
                "ip": r.ip,
                "detail": r.detail,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def list_active_sessions(limit: int = 100) -> list[dict[str, Any]]:
    now = _utcnow()
    with session_scope() as db:
        rows = db.scalars(
            select(UserSession)
            .where(UserSession.revoked_at.is_(None), UserSession.expires_at > now)
            .order_by(UserSession.created_at.desc())
            .limit(limit)
        ).all()
        out = []
        for s in rows:
            user = db.get(User, s.user_id)
            out.append(
                {
                    "session_id": s.id,
                    "user_id": s.user_id,
                    "email": user.email if user else None,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                    "ip": s.ip,
                }
            )
        return out
