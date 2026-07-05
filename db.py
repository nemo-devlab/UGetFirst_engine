"""Supabase access layer. All reads/writes go through the service-role client
scoped to the configured schema (`dev` or `public`)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from supabase import Client, create_client

import config

log = logging.getLogger("ugetfirst.db")

_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)
    return _client


def _table(name: str):
    """Query builder scoped to the active schema (dev/public)."""
    return client().schema(config.DB_SCHEMA).table(name)


@dataclass
class Subscriber:
    id: str
    phone: str | None
    notify_sms: bool
    keywords: list[str] = field(default_factory=list)


def load_monitoring() -> dict[str, list[Subscriber]]:
    """Return a mapping of group_url -> subscribers (each with their keywords).

    Small data volumes, so we fetch the three tables and join in memory.
    """
    subs_rows = _table("subscribers").select("id, phone, notify_sms").execute().data
    kw_rows = _table("keywords").select("subscriber_id, keyword").execute().data
    grp_rows = _table("monitored_groups").select("subscriber_id, group_url").execute().data

    keywords_by_sub: dict[str, list[str]] = {}
    for row in kw_rows:
        keywords_by_sub.setdefault(row["subscriber_id"], []).append(row["keyword"])

    subscribers: dict[str, Subscriber] = {
        row["id"]: Subscriber(
            id=row["id"],
            phone=row.get("phone"),
            notify_sms=bool(row.get("notify_sms")),
            keywords=keywords_by_sub.get(row["id"], []),
        )
        for row in subs_rows
    }

    by_group: dict[str, list[Subscriber]] = {}
    for row in grp_rows:
        sub = subscribers.get(row["subscriber_id"])
        # Skip subscribers with no keywords; nothing could ever match.
        if sub and sub.keywords:
            by_group.setdefault(row["group_url"], []).append(sub)
    return by_group


def log_notification(subscriber_id: str, post_url: str, matched_keyword: str) -> bool:
    """Insert a notification log row. Returns True if newly inserted, False if it
    already existed (unique (subscriber_id, post_url)). This is the idempotency
    guard: only send an SMS when this returns True."""
    try:
        result = (
            _table("notification_logs")
            .insert(
                {
                    "subscriber_id": subscriber_id,
                    "post_url": post_url,
                    "matched_keyword": matched_keyword,
                }
            )
            .execute()
        )
        return bool(result.data)
    except Exception as exc:  # supabase-py raises APIError on unique violation (23505)
        if "23505" in str(exc) or "duplicate key" in str(exc).lower():
            return False
        raise
