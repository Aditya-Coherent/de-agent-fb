"""FastAPI routes for email OTP auth."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from vendor_intel.auth.service import (
    AuthError,
    list_active_sessions,
    list_auth_events,
    logout,
    request_otp,
    validate_session,
    verify_otp,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class OtpRequestBody(BaseModel):
    email: EmailStr


class OtpVerifyBody(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=12)


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: str
    role: str
    user_id: int
    expires_at: datetime


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _raise(err: AuthError) -> None:
    status = 401 if err.code in {
        "not_authenticated",
        "session_expired",
        "session_revoked",
        "otp_mismatch",
        "otp_expired",
        "otp_locked",
        "no_challenge",
        "inactive",
    } else 400
    if err.code == "rate_limited":
        status = 429
    raise HTTPException(status_code=status, detail={"code": err.code, "message": err.message})


def _bearer(authorization: str | None, *, required: bool = True) -> str:
    if not authorization:
        if required:
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        return ""
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        if required:
            raise HTTPException(status_code=401, detail="Use Authorization: Bearer <token>")
        return ""
    return parts[1].strip()


@router.post("/otp/request")
def api_request_otp(body: OtpRequestBody, request: Request):
    try:
        return request_otp(str(body.email), ip=_client_ip(request))
    except AuthError as e:
        _raise(e)


@router.post("/otp/verify", response_model=SessionResponse)
def api_verify_otp(body: OtpVerifyBody, request: Request):
    try:
        info = verify_otp(
            str(body.email),
            body.code,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return SessionResponse(
            access_token=info.token,
            token_type="bearer",
            email=info.email,
            role=info.role,
            user_id=info.user_id,
            expires_at=info.expires_at,
        )
    except AuthError as e:
        _raise(e)


@router.get("/me")
def api_me(request: Request, authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    try:
        user = validate_session(token, ip=_client_ip(request))
        return {
            "user_id": user.user_id,
            "email": user.email,
            "role": user.role,
            "expires_at": user.expires_at.isoformat(),
        }
    except AuthError as e:
        _raise(e)


@router.post("/logout")
def api_logout(request: Request, authorization: str | None = Header(default=None)):
    token = _bearer(authorization, required=False)
    if token:
        logout(token, ip=_client_ip(request))
    return {"ok": True}


@router.get("/admin/events")
def api_admin_events(
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = 100,
):
    token = _bearer(authorization)
    try:
        user = validate_session(token, ip=_client_ip(request))
    except AuthError as e:
        _raise(e)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return {"events": list_auth_events(limit=min(limit, 500))}


@router.get("/admin/sessions")
def api_admin_sessions(
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = 100,
):
    token = _bearer(authorization)
    try:
        user = validate_session(token, ip=_client_ip(request))
    except AuthError as e:
        _raise(e)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return {"sessions": list_active_sessions(limit=min(limit, 500))}
