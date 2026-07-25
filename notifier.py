"""Notification output.

SMS via Twilio (or simulated outbox/). Email match alerts via Resend
(or simulated email_*.txt outbox when RESEND_API_KEY is unset).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import config

log = logging.getLogger("ugetfirst.notifier")

OUTBOX_DIR = Path(__file__).resolve().parent / "outbox"
LOGO_MARK_PATH = Path(__file__).resolve().parent / "assets" / "logo-mark.png"
LOGO_CID = "ugetfirst-logo"
DASHBOARD_URL = "https://ugetfirst.com/dashboard"
# Hosted fallback for clients/previews; live sends also embed via CID.
LOGO_URL = "https://ugetfirst.com/email/logo-mark.png"

HELP_REPLY = (
    "UGetFirst: Job alert texts when keywords match in your watched Facebook "
    "groups. Msg & data rates may apply. Reply STOP to cancel. "
    "Support: support@ugetfirst.com"
)


@dataclass
class SendResult:
    channel: str  # "twilio" | "simulated" | "resend" | "email"
    status: str  # "sent" | "failed"
    provider_message_id: str | None = None
    error: str | None = None


def to_e164(phone: str) -> str:
    """Subscribers store digits only (e.g. 15551234567); render as +E.164."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return "+" + digits


def is_live_destination(channel: str, destination: str) -> bool:
    """Return whether a provider call is allowed for this destination.

    PROD sends normally. DEV fails closed: only the configured QA phone/email
    can leave the process; all other destinations use the local outbox.
    """
    if config.ENV == "prod":
        return True
    if channel == "sms":
        allowed = "".join(ch for ch in config.QA_TEST_PHONE if ch.isdigit())
        actual = "".join(ch for ch in destination if ch.isdigit())
        return bool(allowed and actual == allowed)
    if channel == "email":
        allowed = config.QA_TEST_EMAIL.strip().lower()
        actual = destination.strip().lower()
        return bool(allowed and actual == allowed)
    raise ValueError(f"Unsupported notification channel: {channel!r}")


def build_message(keyword: str, post_url: str) -> str:
    return (
        f'UGetFirst: "{keyword}" just posted in your group.\n'
        f"{post_url}\n"
        "Reply STOP to unsubscribe or HELP for help."
    )


def build_email_subject(keyword: str) -> str:
    return f'UGetFirst alert: "{keyword}" matched'


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _logo_mark_base64() -> str | None:
    if not LOGO_MARK_PATH.is_file():
        return None
    return base64.b64encode(LOGO_MARK_PATH.read_bytes()).decode("ascii")


def _logo_attachment() -> dict[str, str] | None:
    content = _logo_mark_base64()
    if not content:
        log.warning("Email logo mark missing at %s", LOGO_MARK_PATH)
        return None
    return {
        "filename": "logo-mark.png",
        "content": content,
        "content_type": "image/png",
        "content_id": LOGO_CID,
    }


def html_with_previewable_logo(html: str) -> str:
    """Swap cid: logo refs for a data URI so admin iframes can render it."""
    content = _logo_mark_base64()
    if not content:
        return html.replace(f"cid:{LOGO_CID}", LOGO_URL)
    return html.replace(f"cid:{LOGO_CID}", f"data:image/png;base64,{content}")


def _brand_header_html() -> str:
    """Wordmark matching UGetFirst_web: green mark + Lucide Zap PNG + UGetFirst."""
    # Prefer CID so Gmail/etc. don't strip SVG; hosted URL is alt text fallback only.
    return f"""
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
          <tr>
            <td style="vertical-align:middle;padding-right:10px;">
              <img src="cid:{LOGO_CID}"
                   width="32"
                   height="32"
                   alt="UGetFirst"
                   style="display:block;width:32px;height:32px;border:0;outline:none;text-decoration:none;border-radius:8px;" />
            </td>
            <td style="vertical-align:middle;font-size:20px;font-weight:800;letter-spacing:-0.02em;color:#171717;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
              UGet<span style="color:#00C805;">First</span>
            </td>
          </tr>
        </table>"""


def build_email_bodies(keyword: str, post_url: str) -> tuple[str, str]:
    text = (
        f'A new post matched your keyword "{keyword}".\n\n'
        f"{post_url}\n\n"
        "Manage alerts in your UGetFirst dashboard.\n"
        f"{DASHBOARD_URL}\n"
    )
    safe_keyword = _html_escape(keyword)
    safe_url = _html_escape(post_url)
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>UGetFirst alert</title>
  </head>
  <body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f5f5f5;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:480px;background:#ffffff;border:1px solid #e5e5e5;border-radius:20px;overflow:hidden;">
            <tr>
              <td style="height:4px;background:#00C805;font-size:0;line-height:0;">&nbsp;</td>
            </tr>
            <tr>
              <td style="padding:32px 28px 28px;text-align:center;">
                {_brand_header_html()}
                <p style="margin:28px 0 0;font-size:13px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#00A804;">
                  Keyword match
                </p>
                <p style="margin:10px 0 0;font-size:22px;font-weight:800;line-height:1.3;color:#171717;letter-spacing:-0.02em;">
                  &ldquo;{safe_keyword}&rdquo;
                </p>
                <p style="margin:14px 0 0;font-size:15px;line-height:1.55;color:#525252;">
                  A new post in your watched Facebook group just matched this keyword.
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:28px auto 0;">
                  <tr>
                    <td style="border-radius:14px;background:#00C805;box-shadow:0 8px 24px -6px rgba(0,200,5,0.45);">
                      <a href="{safe_url}"
                         style="display:inline-block;padding:14px 28px;font-size:16px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:14px;">
                        Open post
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="margin:28px 0 0;padding-top:20px;border-top:1px solid #f0f0f0;font-size:13px;line-height:1.5;color:#a3a3a3;">
                  Manage alerts in your
                  <a href="{DASHBOARD_URL}" style="color:#00A804;font-weight:600;text-decoration:none;">UGetFirst dashboard</a>.
                </p>
              </td>
            </tr>
          </table>
          <p style="margin:20px 0 0;font-size:12px;color:#a3a3a3;">
            You&rsquo;re receiving this because email alerts are on for your account.
          </p>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    return text, html


def _outbox_filename(dest: str, post_url: str, prefix: str = "") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    digest = hashlib.sha1(f"{dest}|{post_url}".encode()).hexdigest()[:10]
    return f"{prefix}{ts}_{digest}.txt"


def _write_outbox(phone: str, keyword: str, post_url: str, body: str) -> None:
    to = to_e164(phone)
    OUTBOX_DIR.mkdir(exist_ok=True)
    path = OUTBOX_DIR / _outbox_filename(phone, post_url)
    contents = (
        f"to: {to}\n"
        f"keyword: {keyword}\n"
        f"post_url: {post_url}\n"
        f"time: {datetime.now(timezone.utc).isoformat()}\n"
        "---\n"
        f"{body}\n"
    )
    path.write_text(contents, encoding="utf-8")
    log.info("[SIMULATED SMS] wrote %s (to=%s, keyword=%s)", path.name, to, keyword)


def _write_email_outbox(email: str, keyword: str, post_url: str, body: str) -> None:
    OUTBOX_DIR.mkdir(exist_ok=True)
    path = OUTBOX_DIR / _outbox_filename(email, post_url, prefix="email_")
    contents = (
        f"to: {email}\n"
        f"keyword: {keyword}\n"
        f"post_url: {post_url}\n"
        f"time: {datetime.now(timezone.utc).isoformat()}\n"
        "---\n"
        f"{body}\n"
    )
    path.write_text(contents, encoding="utf-8")
    log.info(
        "[SIMULATED EMAIL] wrote %s (to=%s, keyword=%s)", path.name, email, keyword
    )


def _twilio_ready() -> bool:
    return bool(
        config.SMS_MODE == "twilio"
        and config.TWILIO_ACCOUNT_SID
        and config.TWILIO_AUTH_TOKEN
        and config.TWILIO_FROM_NUMBER
    )


def send(phone: str, keyword: str, post_url: str) -> SendResult:
    body = build_message(keyword, post_url)
    live = is_live_destination("sms", phone)

    # DEV fail-closed: never call Twilio unless destination is the QA allowlist.
    if not _twilio_ready() or not live:
        if config.ENV != "prod" and not live:
            log.warning(
                "[DEV BLOCKED SMS] non-QA destination forced to simulated (to=%s)",
                to_e164(phone) if phone else "(empty)",
            )
        _write_outbox(phone, keyword, post_url, body)
        return SendResult(channel="simulated", status="sent")

    to = to_e164(phone)
    try:
        from twilio.rest import Client

        client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=body,
            from_=config.TWILIO_FROM_NUMBER,
            to=to,
        )
        log.info(
            "[TWILIO SMS] sid=%s to=%s keyword=%s",
            message.sid,
            to,
            keyword,
        )
        return SendResult(
            channel="twilio",
            status="sent",
            provider_message_id=message.sid,
        )
    except Exception as exc:
        log.exception("Twilio send failed to=%s", to)
        return SendResult(
            channel="twilio",
            status="failed",
            error=str(exc)[:2000],
        )


def send_email_alert(email: str, keyword: str, post_url: str) -> SendResult:
    text, html = build_email_bodies(keyword, post_url)
    subject = build_email_subject(keyword)
    live = is_live_destination("email", email)

    # DEV fail-closed: never call Resend unless destination is the QA allowlist.
    if not config.RESEND_API_KEY or not live:
        if config.ENV != "prod" and not live:
            log.warning(
                "[DEV BLOCKED EMAIL] non-QA destination forced to simulated (to=%s)",
                email or "(empty)",
            )
        _write_email_outbox(email, keyword, post_url, text)
        return SendResult(channel="simulated", status="sent")

    payload = {
        "from": config.ALERT_FROM_EMAIL,
        "to": [email],
        "subject": subject,
        "text": text,
        "html": html,
    }
    logo = _logo_attachment()
    if logo:
        payload["attachments"] = [logo]
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "UGetFirst-Engine/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            msg_id = data.get("id") if isinstance(data, dict) else None
            log.info("[RESEND] id=%s to=%s keyword=%s", msg_id, email, keyword)
            return SendResult(
                channel="resend",
                status="sent",
                provider_message_id=msg_id,
            )
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:2000]
        log.error("Resend HTTP %s: %s", exc.code, err_body)
        return SendResult(channel="resend", status="failed", error=err_body)
    except Exception as exc:
        log.exception("Resend send failed to=%s", email)
        return SendResult(channel="resend", status="failed", error=str(exc)[:2000])
