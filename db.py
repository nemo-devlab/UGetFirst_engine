"""Supabase access layer. Service-role client against the active project's
`public` schema (ENV selects prod vs dev project)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from supabase import Client, create_client

import config
import matcher

log = logging.getLogger("ugetfirst.db")

_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)
    return _client


def _table(name: str):
    """Query builder scoped to public schema on the active project."""
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
        kw = matcher.normalize_keyword(row["keyword"])
        if not kw:
            continue
        keywords_by_sub.setdefault(row["subscriber_id"], []).append(kw)

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


def log_notification(subscriber_id: str, post_url: str, matched_keyword: str) -> str | None:
    """Insert a notification log row. Returns the new row id if inserted, None if
    duplicate (unique subscriber_id, post_url). This is the idempotency guard."""
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
        if result.data:
            return result.data[0]["id"]
        return None
    except Exception as exc:  # supabase-py raises APIError on unique violation (23505)
        if "23505" in str(exc) or "duplicate key" in str(exc).lower():
            return None
        raise


def log_sendout(
    *,
    subscriber_id: str,
    notification_log_id: str | None,
    phone: str,
    body: str,
    keyword: str,
    post_url: str,
    channel: str = "simulated",
    status: str = "sent",
    provider_message_id: str | None = None,
    error: str | None = None,
) -> None:
    """Record an SMS send attempt (sent, skipped, or failed)."""
    row = {
        "subscriber_id": subscriber_id,
        "notification_log_id": notification_log_id,
        "phone": phone,
        "body": body,
        "keyword": keyword,
        "post_url": post_url,
        "channel": channel,
        "status": status,
    }
    if provider_message_id:
        row["provider_message_id"] = provider_message_id
    if error:
        row["error"] = error[:2000]
    try:
        _table("sms_sendouts").insert(row).execute()
    except Exception as exc:
        if "42P01" in str(exc) or "sms_sendouts" in str(exc).lower():
            log.warning("sms_sendouts table missing; run migration 012_sms_sendouts.sql")
            return
        raise


def _engine_runs_missing(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "42p01" in msg or "engine_runs" in msg or "pgrst205" in msg


def start_engine_run(groups_count: int) -> str | None:
    """Insert a cycle-start row. Returns run id, or None if engine_runs is missing."""
    try:
        result = (
            _table("engine_runs")
            .insert(
                {
                    "env": config.ENV,
                    "groups_count": groups_count,
                }
            )
            .execute()
        )
        if result.data:
            return result.data[0]["id"]
        return None
    except Exception as exc:
        if _engine_runs_missing(exc):
            log.warning("engine_runs table missing; run migration 013_engine_runs.sql")
            return None
        raise


def finish_engine_run(
    run_id: str | None,
    *,
    posts_scraped: int = 0,
    matches_found: int = 0,
    sms_dispatched: int = 0,
    apify_run_id: str | None = None,
    error: str | None = None,
) -> None:
    """Update a cycle row with scrape/match/SMS metrics (and optional error)."""
    if not run_id:
        return
    row: dict = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "posts_scraped": posts_scraped,
        "matches_found": matches_found,
        "sms_dispatched": sms_dispatched,
    }
    if apify_run_id:
        row["apify_run_id"] = apify_run_id
    if error:
        row["error"] = error[:2000]
    try:
        _table("engine_runs").update(row).eq("id", run_id).execute()
    except Exception as exc:
        if _engine_runs_missing(exc):
            log.warning("engine_runs table missing; run migration 013_engine_runs.sql")
            return
        raise
