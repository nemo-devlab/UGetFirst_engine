"""Facebook group URL normalization and catalog metadata resolution."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from apify_client import ApifyClient

import config

log = logging.getLogger("ugetfirst.groups")

_GROUP_ID_RE = re.compile(r"/groups/(\d+)")
_GROUP_TITLE_FIELDS = ("groupTitle", "groupName", "title", "name")


def group_id(url_or_id: str | None) -> str | None:
    """Extract the numeric group id from a URL (or return digits as-is)."""
    if not url_or_id:
        return None
    m = _GROUP_ID_RE.search(url_or_id)
    if m:
        return m.group(1)
    return url_or_id if url_or_id.isdigit() else None


def is_placeholder_group_name(name: str | None, facebook_group_id: str) -> bool:
    if not name or not name.strip():
        return True
    return name.strip() == f"Facebook Group {facebook_group_id}"


def canonical_url(facebook_group_id: str) -> str:
    return f"https://www.facebook.com/groups/{facebook_group_id}"


def normalize_group_url(raw: str) -> dict[str, str] | None:
    """Normalize a pasted group URL to a stable id + canonical URL."""
    text = raw.strip()
    gid = group_id(text)
    if not gid:
        return None
    return {
        "facebook_group_id": gid,
        "canonical_url": canonical_url(gid),
    }


def _first(item: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    for field in fields:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _dataset_id(run: Any) -> str:
    if isinstance(run, dict):
        return run["defaultDatasetId"]
    dataset_id = getattr(run, "default_dataset_id", None)
    if dataset_id:
        return dataset_id
    raise TypeError(f"Unexpected Apify run result type: {type(run)!r}")


@dataclass
class GroupMetadata:
    name: str | None = None


def fetch_group_metadata(canonical_group_url: str) -> GroupMetadata:
    """Fetch group title via a lightweight Apify run (one post max)."""
    if not config.APIFY_TOKEN:
        log.warning("APIFY_TOKEN missing; skipping group metadata fetch")
        return GroupMetadata()

    apify = ApifyClient(config.APIFY_TOKEN)
    run_input = {
        "startUrls": [{"url": canonical_group_url}],
        "resultsLimit": 1,
        "viewOption": "CHRONOLOGICAL",
    }
    try:
        run = apify.actor(config.APIFY_ACTOR_ID).call(run_input=run_input)
        dataset_id = _dataset_id(run)
        for item in apify.dataset(dataset_id).iterate_items():
            name = _first(item, _GROUP_TITLE_FIELDS)
            if name:
                return GroupMetadata(name=name)
    except Exception:
        log.exception("Failed to fetch metadata for %s", canonical_group_url)
    return GroupMetadata()


def upsert_catalog_group(
    client: Any,
    *,
    facebook_group_id: str,
    canonical_group_url: str,
    name: str | None,
    discovery_source: str = "user_submitted",
    review_status: str = "approved",
) -> dict[str, Any]:
    """Insert or update facebook_groups keyed by facebook_group_id."""
    table = client.schema(config.DB_SCHEMA).table("facebook_groups")
    now = datetime.now(timezone.utc).isoformat()

    existing = (
        table.select("id, name")
        .eq("facebook_group_id", facebook_group_id)
        .limit(1)
        .execute()
    )
    rows = existing.data or []
    row = rows[0] if rows else None

    payload = {
        "facebook_group_id": facebook_group_id,
        "canonical_url": canonical_group_url,
        "group_url": canonical_group_url,
        "discovery_source": discovery_source,
        "review_status": review_status,
        "last_curated_at": now,
        "active": True,
    }
    if name:
        payload["name"] = name

    if row:
        update_payload = {k: v for k, v in payload.items() if k != "name"}
        existing_name = (row.get("name") or "").strip()
        if name and (
            not existing_name or is_placeholder_group_name(existing_name, facebook_group_id)
        ):
            update_payload["name"] = name
        updated = table.update(update_payload).eq("id", row["id"]).execute()
        return updated.data[0] if updated.data else row

    if not name:
        payload["name"] = f"Facebook Group {facebook_group_id}"

    inserted = table.insert(payload).execute()
    return inserted.data[0]


def resolve_catalog_group(
    client: Any,
    raw_url: str,
    *,
    discovery_source: str = "user_submitted",
    fetch_if_missing: bool = True,
) -> dict[str, Any] | None:
    """Normalize URL, resolve name, upsert catalog. Returns None if URL invalid."""
    normalized = normalize_group_url(raw_url)
    if not normalized:
        return None

    gid = normalized["facebook_group_id"]
    curl = normalized["canonical_url"]
    table = client.schema(config.DB_SCHEMA).table("facebook_groups")

    existing = (
        table.select("id, name, facebook_group_id, canonical_url, group_url")
        .eq("facebook_group_id", gid)
        .limit(1)
        .execute()
    )
    existing_rows = existing.data or []
    cached_name = existing_rows[0].get("name") if existing_rows else None
    name = (
        cached_name
        if cached_name and not is_placeholder_group_name(cached_name, gid)
        else None
    )

    if fetch_if_missing and not name:
        metadata = fetch_group_metadata(curl)
        name = metadata.name

    return upsert_catalog_group(
        client,
        facebook_group_id=gid,
        canonical_group_url=curl,
        name=name,
        discovery_source=discovery_source,
    )
