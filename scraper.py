"""Apify wrapper for the facebook-groups-scraper actor.

Runs actor call(s) for all distinct group URLs (batched), then normalizes each
dataset item into a small, stable Post shape the rest of the app relies on.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from apify_client import ApifyClient

import config
from groups import group_id

log = logging.getLogger("ugetfirst.scraper")


@dataclass
class Post:
    url: str
    text: str
    group_id: str | None
    raw: dict
    posted_at: str | None = None


@dataclass
class ScrapeResult:
    posts: list[Post]
    apify_run_id: str | None


# Locked field order from live Apify facebook-groups-scraper runs.
# Prefer facebookUrl / text / groupUrl; keep fallbacks for actor version drift.
_URL_FIELDS = ("facebookUrl", "url", "postUrl", "topLevelUrl", "link")
_TEXT_FIELDS = ("text", "message", "postText", "content")
_GROUP_FIELDS = ("groupUrl", "groupId", "facebookGroupUrl", "groupTitle")
_TIME_FIELDS = ("time", "timestamp", "date", "createdAt", "publishedAt", "postedAt")


def _first(item: dict, fields: tuple[str, ...]) -> str | None:
    for f in fields:
        val = item.get(f)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _posted_at(item: dict) -> str | None:
    """Best-effort ISO timestamp from Apify time fields (string or epoch ms/s)."""
    for f in _TIME_FIELDS:
        val = item.get(f)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)) and val > 0:
            # Apify sometimes returns epoch ms; treat large values as ms.
            secs = val / 1000.0 if val > 1_000_000_000_000 else float(val)
            return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
    return None


def _normalize(item: dict) -> Post | None:
    url = _first(item, _URL_FIELDS)
    text = _first(item, _TEXT_FIELDS) or ""
    if not url:
        return None
    gid = group_id(_first(item, _GROUP_FIELDS)) or group_id(url)
    return Post(
        url=url,
        text=text,
        group_id=gid,
        raw=item,
        posted_at=_posted_at(item),
    )


def _dataset_id(run) -> str:
    """Apify client v1 returns a dict; v3 returns a Run model with snake_case fields."""
    if isinstance(run, dict):
        return run["defaultDatasetId"]
    dataset_id = getattr(run, "default_dataset_id", None)
    if dataset_id:
        return dataset_id
    raise TypeError(f"Unexpected Apify run result type: {type(run)!r}")


def _run_id(run) -> str | None:
    if isinstance(run, dict):
        return run.get("id")
    return getattr(run, "id", None)


def _results_limit_for(group_count: int) -> int:
    """Scale Apify resultsLimit with group count so posts aren't starved."""
    scaled = max(config.RESULTS_LIMIT, group_count * config.RESULTS_PER_GROUP)
    return min(scaled, 500)


def _call_actor(
    apify: ApifyClient,
    group_urls: list[str],
    dump_raw_keys: bool,
    lookback: str | None = None,
):
    lookback_val = lookback if lookback is not None else config.LOOKBACK
    run_input = {
        "startUrls": [{"url": u} for u in group_urls],
        "resultsLimit": _results_limit_for(len(group_urls)),
        "viewOption": "CHRONOLOGICAL",
    }
    if lookback_val:
        run_input["onlyPostsNewerThan"] = lookback_val

    last_exc: Exception | None = None
    attempts = 1 + max(0, config.APIFY_MAX_RETRIES)
    for attempt in range(1, attempts + 1):
        try:
            log.info(
                "Starting Apify actor for %d group(s) (limit=%d, lookback=%s, attempt=%d/%d)",
                len(group_urls),
                run_input["resultsLimit"],
                lookback_val or "none",
                attempt,
                attempts,
            )
            run = apify.actor(config.APIFY_ACTOR_ID).call(run_input=run_input)
            posts: list[Post] = []
            dataset_id = _dataset_id(run)
            for item in apify.dataset(dataset_id).iterate_items():
                if dump_raw_keys:
                    log.info("Raw item keys: %s", sorted(item.keys()))
                post = _normalize(item)
                if post:
                    posts.append(post)
            return posts, _run_id(run)
        except Exception as exc:
            last_exc = exc
            log.warning("Apify call failed (attempt %d/%d): %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(min(30, 2 ** attempt))
    assert last_exc is not None
    raise last_exc


def scrape(
    group_urls: list[str],
    dump_raw_keys: bool = False,
    lookback: str | None = None,
) -> ScrapeResult:
    """Scrape recent posts for the given group URLs via batched actor runs.

    Fetches posts within the lookback time window (newest-first, capped per
    batch) and relies on the unique (subscriber_id, post_url) constraint on
    notification_logs to avoid re-notifying for posts we've already seen.
    """
    if not group_urls:
        return ScrapeResult(posts=[], apify_run_id=None)
    if not config.APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN is not set; cannot scrape.")

    apify = ApifyClient(config.APIFY_TOKEN)
    batch_size = max(1, config.SCRAPE_BATCH_SIZE)
    all_posts: list[Post] = []
    run_ids: list[str] = []

    for i in range(0, len(group_urls), batch_size):
        batch = group_urls[i : i + batch_size]
        posts, run_id = _call_actor(
            apify, batch, dump_raw_keys=dump_raw_keys, lookback=lookback
        )
        all_posts.extend(posts)
        if run_id:
            run_ids.append(run_id)

    log.info(
        "Scraped %d normalized post(s) across %d batch(es)",
        len(all_posts),
        max(1, (len(group_urls) + batch_size - 1) // batch_size),
    )
    return ScrapeResult(
        posts=all_posts,
        apify_run_id=run_ids[0] if len(run_ids) == 1 else (",".join(run_ids) or None),
    )
