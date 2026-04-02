"""Email service — send transactional emails via Resend."""

import secrets
import time
from threading import Lock

import resend

from ..config import get_settings

# In-memory rate limiter: {email: last_sent_timestamp}
_send_log: dict[str, float] = {}
_send_log_lock = Lock()
_COOLDOWN_SECONDS = 60


class EmailRateLimited(Exception):
    """Raised when an email send is attempted too soon."""

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Try again in {retry_after}s.")


def generate_verification_token() -> str:
    return secrets.token_urlsafe(32)


def send_verification_email(to_email: str, full_name: str, token: str) -> None:
    settings = get_settings()
    if not settings.RESEND_API_KEY:
        return

    _enforce_rate_limit(to_email)

    resend.api_key = settings.RESEND_API_KEY
    verify_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"

    resend.Emails.send(
        {
            "from": settings.EMAIL_FROM,
            "to": [to_email],
            "subject": "Verify your PineForge account",
            "html": _verification_html(full_name, verify_url),
        }
    )


def _enforce_rate_limit(email: str) -> None:
    now = time.monotonic()
    with _send_log_lock:
        last_sent = _send_log.get(email)
        if last_sent is not None:
            elapsed = now - last_sent
            if elapsed < _COOLDOWN_SECONDS:
                raise EmailRateLimited(retry_after=int(_COOLDOWN_SECONDS - elapsed))
        _send_log[email] = now


def _verification_html(name: str, verify_url: str) -> str:
    return f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#030712;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#030712;padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="480" cellpadding="0" cellspacing="0" style="background-color:#111827;border:1px solid #1f2937;border-radius:16px;overflow:hidden;">
          <!-- Header -->
          <tr>
            <td style="padding:32px 32px 0 32px;text-align:center;">
              <span style="font-size:24px;font-weight:700;color:#ffffff;letter-spacing:-0.5px;">
                &#127794; PineForge
              </span>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:32px;">
              <h1 style="margin:0 0 8px 0;font-size:20px;font-weight:600;color:#ffffff;">
                Verify your email
              </h1>
              <p style="margin:0 0 24px 0;font-size:14px;color:#9ca3af;line-height:1.6;">
                Hi {name}, thanks for signing up! Click the button below to verify your email address and activate your account.
              </p>
              <table cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td align="center">
                    <a href="{verify_url}"
                       style="display:inline-block;padding:12px 32px;background-color:#059669;color:#ffffff;font-size:14px;font-weight:600;text-decoration:none;border-radius:8px;">
                      Verify Email Address
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:24px 0 0 0;font-size:12px;color:#6b7280;line-height:1.6;">
                If the button doesn't work, copy and paste this link into your browser:<br>
                <a href="{verify_url}" style="color:#34d399;word-break:break-all;">{verify_url}</a>
              </p>
              <p style="margin:16px 0 0 0;font-size:12px;color:#6b7280;">
                This link expires in 24 hours. If you didn't create an account, you can ignore this email.
              </p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding:0 32px 32px 32px;text-align:center;">
              <p style="margin:0;font-size:11px;color:#4b5563;">
                &copy; PineForge &mdash; Automated Trading Platform
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
