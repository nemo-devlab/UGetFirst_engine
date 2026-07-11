"""Apify wrapper for the facebook-groups-scraper actor.

Runs one actor call for all distinct group URLs, then normalizes each dataset
item into a small, stable Post shape the rest of the app relies on.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from apify_client import ApifyClient

import config

log = logging.getLogger("ugetfirst.scraper")

_GROUP_ID_RE = re.compile(r"/groups/(\d+)")


def group_id(url_or_id: str | None) -> str | None:
    """Extract the numeric group id from a URL (or return digits as-is).
    Used to match a scraped post back to a monitored group_url robustly,
    regardless of URL formatting differences."""
    if not url_or_id:
        return None
    m = _GROUP_ID_RE.search(url_or_id)
    if m:
        return m.group(1)
    return url_or_id if url_or_id.isdigit() else None


@dataclass
class Post:
    url: str
    text: str
    group_id: str | None


# Actor output field names can vary; check these in order. Confirmed against a
# live test run during first build (logged via --dump-raw if needed).
_URL_FIELDS = ("url", "postUrl", "topLevelUrl", "facebookUrl", "link")
_TEXT_FIELDS = ("text", "message", "postText", "content")
_GROUP_FIELDS = ("groupUrl", "groupId", "facebookGroupUrl", "groupTitle")


def _first(item: dict, fields: tuple[str, ...]) -> str | None:
    for f in fields:
        val = item.get(f)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _normalize(item: dict) -> Post | None:
    url = _first(item, _URL_FIELDS)
    text = _first(item, _TEXT_FIELDS) or ""
    if not url:
        return None
    gid = group_id(_first(item, _GROUP_FIELDS)) or group_id(url)
    return Post(url=url, text=text, group_id=gid)


def _dataset_id(run) -> str:
    """Apify client v1 returns a dict; v3 returns a Run model with snake_case fields."""
    if isinstance(run, dict):
        return run["defaultDatasetId"]
    dataset_id = getattr(run, "default_dataset_id", None)
    if dataset_id:
        return dataset_id
    raise TypeError(f"Unexpected Apify run result type: {type(run)!r}")


def scrape(group_urls: list[str], dump_raw_keys: bool = False) -> list[Post]:
    """Scrape recent posts for the given group URLs via one actor run.

    Fetches posts within the LOOKBACK time window (newest-first, capped at
    RESULTS_LIMIT) and relies on the unique (subscriber_id, post_url) constraint
    on notification_logs to avoid re-notifying for posts we've already seen.
    """
    if not group_urls:
        return []
    if not config.APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN is not set; cannot scrape.")

    run_input = {
        "startUrls": [{"url": u} for u in group_urls],
        "resultsLimit": config.RESULTS_LIMIT,
        "viewOption": "CHRONOLOGICAL",
    }
    # Only include the time filter when set; the actor rejects a null value.
    if config.LOOKBACK:
        run_input["onlyPostsNewerThan"] = config.LOOKBACK

    apify = ApifyClient(config.APIFY_TOKEN)
    log.info(
        "Starting Apify actor for %d group(s) (onlyPostsNewerThan=%s)",
        len(group_urls),
        config.LOOKBACK or "none",
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
    log.info("Scraped %d normalized post(s)", len(posts))
    return posts
