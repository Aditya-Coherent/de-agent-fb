"""Send OTP emails (console | smtp | resend)."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

import httpx

from vendor_intel.auth.config import AuthSettings


def send_otp_email(settings: AuthSettings, to_email: str, code: str) -> str:
    """
    Send the OTP. Returns a short note for logs (never includes the code in API responses).
    """
    subject = f"{settings.app_name} login code"
    body = (
        f"Your login code for {settings.app_name} is:\n\n"
        f"    {code}\n\n"
        f"This code expires in {settings.otp_ttl_minutes} minutes.\n"
        f"If you did not request this, ignore this email.\n"
    )
    backend = settings.email_backend

    if backend == "console":
        print(f"\n[auth] OTP for {to_email}: {code}  (AUTH_EMAIL_BACKEND=console)\n", flush=True)
        return "console"

    if backend == "resend":
        if not settings.resend_api_key:
            raise RuntimeError("RESEND_API_KEY is required when AUTH_EMAIL_BACKEND=resend")
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.smtp_from,
                "to": [to_email],
                "subject": subject,
                "text": body,
            },
            timeout=30.0,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Resend error {resp.status_code}: {resp.text[:300]}")
        return "resend"

    if backend == "smtp":
        if not settings.smtp_host:
            raise RuntimeError("SMTP_HOST is required when AUTH_EMAIL_BACKEND=smtp")
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to_email
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return "smtp"

    raise RuntimeError(f"Unknown AUTH_EMAIL_BACKEND={backend!r} (use console|smtp|resend)")
