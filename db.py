"""Supabase access layer. Service-role client against the active project's
`public` schema (ENV selects prod vs dev project)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

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
    email: str | None
    notify_sms: bool
    notify_email: bool
    sms_consent_at: str | None = None
    plan_tier: str = "free"
    plan_status: str = "active"
    keywords: list[str] = field(default_factory=list)

    @property
    def sms_enabled(self) -> bool:
        """SMS only when opted in (notify_sms) with recorded consent + a phone."""
        return bool(self.notify_sms and self.phone and self.sms_consent_at)

    @property
    def email_enabled(self) -> bool:
        return bool(self.notify_email and self.email)

    @property
    def effective_tier(self) -> str:
        """Past-due / canceled paid accounts behave like free for SMS + cadence."""
        tier = (self.plan_tier or "free").lower()
        status = (self.plan_status or "active").lower()
        if tier == "free":
            return "free"
        if status not in ("active", "trialing"):
            return "free"
        if tier in ("speed", "lightning"):
            return tier
        return "free"

    @property
    def sms_ok(self) -> bool:
        return self.effective_tier in ("speed", "lightning") and self.sms_enabled

    @property
    def email_ok(self) -> bool:
        return self.email_enabled

    @property
    def alert_ready(self) -> bool:
        return bool(self.keywords) and (self.sms_ok or self.email_ok)

    @property
    def poll_seconds(self) -> int:
        return config.TIER_POLL_SECONDS.get(
            self.effective_tier, config.TIER_POLL_SECONDS["free"]
        )


def _fetch_all(table: str, columns: str, page_size: int = 1000) -> list[dict]:
    """Paginated select — Supabase/PostgREST defaults to 1000 rows."""
    rows: list[dict] = []
    start = 0
    while True:
        end = start + page_size - 1
        result = _table(table).select(columns).range(start, end).execute()
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def load_monitoring() -> dict[str, list[Subscriber]]:
    """Return group_url -> alert-ready subscribers (keywords + SMS and/or email).

    Only includes catalog groups with scrape_enabled=true (and active/approved).
    Free email-only watchers keep groups in the scrape set.
    """
    subs_rows = _fetch_all(
        "subscribers",
        "id, phone, email, notify_sms, notify_email, sms_consent_at, plan_tier, plan_status",
    )
    kw_rows = _fetch_all("keywords", "subscriber_id, keyword")
    grp_rows = _fetch_all(
        "monitored_groups",
        "subscriber_id, group_url, facebook_group_uuid, status",
    )
    catalog_rows = (
        _table("facebook_groups")
        .select(
            "id, group_url, canonical_url, scrape_enabled, active, "
            "review_status, last_scraped_at"
        )
        .eq("scrape_enabled", True)
        .eq("active", True)
        .eq("review_status", "approved")
        .execute()
        .data
    )

    scrape_by_id = {row["id"]: row for row in catalog_rows or []}
    scrape_urls = {
        (row.get("canonical_url") or row["group_url"])
        for row in catalog_rows or []
        if row.get("canonical_url") or row.get("group_url")
    }

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
            email=row.get("email"),
            notify_sms=bool(row.get("notify_sms")),
            notify_email=bool(row.get("notify_email", True)),
            sms_consent_at=row.get("sms_consent_at"),
            plan_tier=row.get("plan_tier") or "free",
            plan_status=row.get("plan_status") or "active",
            keywords=keywords_by_sub.get(row["id"], []),
        )
        for row in subs_rows
    }

    by_group: dict[str, list[Subscriber]] = {}
    for row in grp_rows or []:
        if row.get("status") and row["status"] != "active":
            continue
        catalog_id = row.get("facebook_group_uuid")
        group_url = row.get("group_url")
        allowed = False
        if catalog_id and catalog_id in scrape_by_id:
            allowed = True
            catalog = scrape_by_id[catalog_id]
            group_url = catalog.get("canonical_url") or catalog.get("group_url") or group_url
        elif group_url and group_url in scrape_urls:
            allowed = True
        if not allowed or not group_url:
            continue
        sub = subscribers.get(row["subscriber_id"])
        if sub and sub.alert_ready:
            by_group.setdefault(group_url, []).append(sub)
    return by_group


def group_poll_seconds(subs: list[Subscriber]) -> int:
    """Fastest watcher wins (lowest poll seconds)."""
    if not subs:
        return config.TIER_POLL_SECONDS["free"]
    return min(s.poll_seconds for s in subs)


def catalog_last_scraped_map() -> dict[str, datetime | None]:
    """Map canonical/group URL -> last_scraped_at for scrape-enabled catalog."""
    rows = (
        _table("facebook_groups")
        .select("group_url, canonical_url, last_scraped_at")
        .eq("scrape_enabled", True)
        .eq("active", True)
        .eq("review_status", "approved")
        .execute()
        .data
    )
    out: dict[str, datetime | None] = {}
    for row in rows or []:
        ts = row.get("last_scraped_at")
        parsed: datetime | None = None
        if isinstance(ts, str) and ts:
            try:
                parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
        for key in (row.get("canonical_url"), row.get("group_url")):
            if key:
                out[key] = parsed
    return out


def filter_due_groups(
    by_group: dict[str, list[Subscriber]],
) -> tuple[dict[str, list[Subscriber]], int, int]:
    """Keep groups whose cadence interval has elapsed since last_scraped_at.

    Returns (due_by_group, due_count, skipped_count).
    """
    last_map = catalog_last_scraped_map()
    now = datetime.now(timezone.utc)
    due: dict[str, list[Subscriber]] = {}
    skipped = 0
    for url, subs in by_group.items():
        interval = group_poll_seconds(subs)
        last = last_map.get(url)
        if last is None:
            due[url] = subs
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (now - last).total_seconds()
        if elapsed >= interval:
            due[url] = subs
        else:
            skipped += 1
    return due, len(due), skipped


def mark_groups_scraped(group_urls: list[str]) -> None:
    """Stamp last_scraped_at on catalog rows matching these URLs."""
    if not group_urls:
        return
    now = datetime.now(timezone.utc).isoformat()
    for url in group_urls:
        try:
            _table("facebook_groups").update({"last_scraped_at": now}).eq(
                "canonical_url", url
            ).execute()
            _table("facebook_groups").update({"last_scraped_at": now}).eq(
                "group_url", url
            ).execute()
        except Exception:
            log.exception("Failed to update last_scraped_at for %s", url)


def max_lookback_for_groups(by_group: dict[str, list[Subscriber]]) -> str:
    """Apify onlyPostsNewerThan string from slowest due group + buffer."""
    if not by_group:
        return config.LOOKBACK
    max_secs = max(group_poll_seconds(subs) for subs in by_group.values())
    minutes = max(15, int(max_secs / 60) + config.LOOKBACK_BUFFER_MINUTES)
    return f"{minutes} minutes"


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


def _scraped_posts_missing(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "42p01" in msg or "scraped_posts" in msg or "pgrst205" in msg


def _catalog_uuid_by_facebook_group_id(group_ids: set[str]) -> dict[str, str]:
    """Map facebook_group_id -> facebook_groups.id for catalog linkage."""
    if not group_ids:
        return {}
    mapping: dict[str, str] = {}
    ids = list(group_ids)
    page_size = 200
    for i in range(0, len(ids), page_size):
        chunk = ids[i : i + page_size]
        result = (
            _table("facebook_groups")
            .select("id, facebook_group_id")
            .in_("facebook_group_id", chunk)
            .execute()
        )
        for row in result.data or []:
            gid = row.get("facebook_group_id")
            if gid:
                mapping[gid] = row["id"]
    return mapping


def upsert_scraped_posts(
    posts: list,
    *,
    engine_run_id: str | None = None,
    apify_run_id: str | None = None,
) -> int:
    """Persist scraped posts as a data asset (upsert on post_url).

    Returns the number of rows sent to upsert. Re-scrapes bump seen_count via
    a DB trigger; first_seen_at is preserved. No-op if the table is missing.
    """
    if not posts:
        return 0

    # Dedup within this batch so upsert doesn't fight itself.
    by_url: dict[str, Any] = {}
    for post in posts:
        url = getattr(post, "url", None)
        if isinstance(url, str) and url:
            by_url[url] = post
    unique_posts = list(by_url.values())

    group_ids = {
        gid
        for p in unique_posts
        if (gid := getattr(p, "group_id", None))
    }
    try:
        uuid_by_gid = _catalog_uuid_by_facebook_group_id(group_ids)
    except Exception as exc:
        if _scraped_posts_missing(exc):
            log.warning(
                "scraped_posts catalog lookup failed; run migration 021_scraped_posts.sql"
            )
            return 0
        # Catalog lookup failure shouldn't block asset write.
        log.warning("Could not resolve facebook_group_uuid mapping: %s", exc)
        uuid_by_gid = {}

    now_iso = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for post in unique_posts:
        gid = getattr(post, "group_id", None)
        row: dict = {
            "post_url": post.url,
            "text": getattr(post, "text", None) or None,
            "raw": getattr(post, "raw", None) or {},
            "facebook_group_id": gid,
            "env": config.ENV,
            "scraped_at": now_iso,
            "last_seen_at": now_iso,
            "seen_count": 1,
        }
        posted_at = getattr(post, "posted_at", None)
        if posted_at:
            row["posted_at"] = posted_at
        if gid and gid in uuid_by_gid:
            row["facebook_group_uuid"] = uuid_by_gid[gid]
        if engine_run_id:
            row["engine_run_id"] = engine_run_id
        if apify_run_id:
            row["apify_run_id"] = apify_run_id
        rows.append(row)

    batch_size = 100
    written = 0
    try:
        for i in range(0, len(rows), batch_size):
            chunk = rows[i : i + batch_size]
            _table("scraped_posts").upsert(chunk, on_conflict="post_url").execute()
            written += len(chunk)
    except Exception as exc:
        if _scraped_posts_missing(exc):
            log.warning(
                "scraped_posts table missing; run migration 021_scraped_posts.sql"
            )
            return 0
        raise

    log.info("Upserted %d scraped post(s) into data asset", written)
    return written
