"""Notification output.

Sends via Twilio when TWILIO_* credentials are set and SMS_MODE is not
"simulated". Otherwise writes one .txt file per message into outbox/.
"""
from __future__ import annotations

import hashlib
import logging
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
    channel: str  # "twilio" | "simulated"
    status: str  # "sent" | "failed"
    provider_message_id: str | None = None
    error: str | None = None


def to_e164(phone: str) -> str:
    """Subscribers store digits only (e.g. 15551234567); render as +E.164."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return "+" + digits


def build_message(keyword: str, post_url: str) -> str:
    return (
        f'UGetFirst: "{keyword}" just posted in your group.\n'
        f"{post_url}\n"
        "Reply STOP to unsubscribe or HELP for help."
    )


def _outbox_filename(phone: str, post_url: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    digest = hashlib.sha1(f"{phone}|{post_url}".encode()).hexdigest()[:10]
    return f"{ts}_{digest}.txt"


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


def _twilio_ready() -> bool:
    return bool(
        config.SMS_MODE == "twilio"
        and config.TWILIO_ACCOUNT_SID
        and config.TWILIO_AUTH_TOKEN
        and config.TWILIO_FROM_NUMBER
    )


def send(phone: str, keyword: str, post_url: str) -> SendResult:
    body = build_message(keyword, post_url)

    if not _twilio_ready():
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
