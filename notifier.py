"""Notification output.

SMS via Twilio (or simulated outbox/). Email match alerts via Resend
(or simulated email_*.txt outbox when RESEND_API_KEY is unset).
"""
from __future__ import annotations

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


def build_email_bodies(keyword: str, post_url: str) -> tuple[str, str]:
    text = (
        f'A new post matched your keyword "{keyword}".\n\n'
        f"{post_url}\n\n"
        "Manage alerts in your UGetFirst dashboard.\n"
    )
    html = (
        f"<p>A new post matched your keyword <strong>{keyword}</strong>.</p>"
        f'<p><a href="{post_url}">Open the Facebook post</a></p>'
        "<p style=\"color:#737373;font-size:13px;\">"
        "Manage alerts in your UGetFirst dashboard."
        "</p>"
    )
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

    if not _twilio_ready() or not is_live_destination("sms", phone):
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

    if not config.RESEND_API_KEY or not is_live_destination("email", email):
        _write_email_outbox(email, keyword, post_url, text)
        return SendResult(channel="email", status="sent")

    payload = {
        "from": config.ALERT_FROM_EMAIL,
        "to": [email],
        "subject": subject,
        "text": text,
        "html": html,
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.RESEND_API_KEY}",
            "Content-Type": "application/json",
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
