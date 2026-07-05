"""Notification output.

Until a real SMS provider is wired up, every "sent" message is written as one
plain-text file into the outbox/ folder. This is the simulation stand-in for SMS
while the provider's 10DLC registration is under review.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import config

log = logging.getLogger("ugetfirst.notifier")

OUTBOX_DIR = Path(__file__).resolve().parent / "outbox"


def to_e164(phone: str) -> str:
    """Subscribers store digits only (e.g. 15551234567); render as +E.164."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return "+" + digits


def build_message(keyword: str, post_url: str) -> str:
    return (
        f'UGetFirst: "{keyword}" just posted in your group.\n'
        f"{post_url}\n"
        "Reply STOP to unsubscribe."
    )


def _outbox_filename(phone: str, post_url: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    digest = hashlib.sha1(f"{phone}|{post_url}".encode()).hexdigest()[:10]
    return f"{ts}_{digest}.txt"


def send(phone: str, keyword: str, post_url: str) -> None:
    body = build_message(keyword, post_url)
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
