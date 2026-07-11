#!/usr/bin/env python3
"""Seed facebook_groups catalog and replace monitored_groups for all DEV subscribers.

Usage:
  python scripts/seed_test_groups.py --dry-run
  python scripts/seed_test_groups.py --apply
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

for _proxy_var in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
):
    os.environ.pop(_proxy_var, None)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from supabase import Client, create_client  # noqa: E402

import config  # noqa: E402
import groups  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("seed_test_groups")

PAGE = 1000

# Curated launch catalog (name, url)
FACEBOOK_GROUPS = [
    ("Cleaning Services DFW", "https://www.facebook.com/groups/759663799503246"),
    ("Local Cleaners Needed", "https://www.facebook.com/groups/633906523464388"),
    (
        "Get Hired: San Antonio Contractors, Sub-Contractors, or Handyman",
        "https://www.facebook.com/groups/468246445370981",
    ),
    (
        "Contractor Underground - Austin and Surrounding",
        "https://www.facebook.com/groups/2228707617600292",
    ),
    ("Socal contractors", "https://www.facebook.com/groups/1366597080531550"),
    ("Austin Contractor Connections", "https://www.facebook.com/groups/6854249724619896"),
]


def make_client(url: str, key: str) -> Client:
    if not url or not key:
        raise RuntimeError("Missing Supabase URL or service-role key")
    return create_client(url, key)


def fetch_subscriber_ids(client: Client) -> list[str]:
    out: list[str] = []
    start = 0
    while True:
        rows = (
            client.schema("public")
            .table("subscribers")
            .select("id")
            .range(start, start + PAGE - 1)
            .execute()
            .data
            or []
        )
        out.extend(r["id"] for r in rows)
        if len(rows) < PAGE:
            break
        start += PAGE
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not config.DEV_SUPABASE_URL or not config.DEV_SUPABASE_SERVICE_ROLE_KEY:
        raise SystemExit("DEV_SUPABASE_URL / DEV_SUPABASE_SERVICE_ROLE_KEY required")

    dev = make_client(config.DEV_SUPABASE_URL, config.DEV_SUPABASE_SERVICE_ROLE_KEY)
    subscriber_ids = fetch_subscriber_ids(dev)
    group_urls = [url for _, url in FACEBOOK_GROUPS]

    log.info(
        "DEV subscribers: %d; catalog groups: %d; monitored rows to insert: %d",
        len(subscriber_ids),
        len(FACEBOOK_GROUPS),
        len(subscriber_ids) * len(group_urls),
    )

    if args.dry_run:
        for name, url in FACEBOOK_GROUPS:
            log.info("  catalog: %s -> %s", name, url)
        log.info("Dry-run only. Re-run with --apply.")
        return

    # Replace facebook_groups catalog
    dev.schema("public").table("facebook_groups").delete().neq(
        "id", "00000000-0000-0000-0000-000000000000"
    ).execute()
    catalog_rows = []
    monitored: list[dict] = []
    for name, url in FACEBOOK_GROUPS:
        normalized = groups.normalize_group_url(url)
        if not normalized:
            raise SystemExit(f"Invalid catalog URL: {url}")
        catalog_rows.append(
            {
                "name": name,
                "group_url": normalized["canonical_url"],
                "canonical_url": normalized["canonical_url"],
                "facebook_group_id": normalized["facebook_group_id"],
                "discovery_source": "manual",
                "review_status": "approved",
                "active": True,
            }
        )

    dev.schema("public").table("facebook_groups").insert(catalog_rows).execute()
    log.info("Inserted %d facebook_groups catalog row(s)", len(catalog_rows))

    # Replace all monitored_groups
    dev.schema("public").table("monitored_groups").delete().neq(
        "id", "00000000-0000-0000-0000-000000000000"
    ).execute()

    for sid in subscriber_ids:
        for name, url in FACEBOOK_GROUPS:
            normalized = groups.normalize_group_url(url)
            if not normalized:
                continue
            monitored.append(
                {
                    "subscriber_id": sid,
                    "group_url": normalized["canonical_url"],
                    "facebook_group_id": normalized["facebook_group_id"],
                    "group_name": name,
                }
            )

    for i in range(0, len(monitored), 100):
        dev.schema("public").table("monitored_groups").insert(monitored[i : i + 100]).execute()

    log.info(
        "Done. %d subscriber(s) now monitor %d group(s) each (%d total rows).",
        len(subscriber_ids),
        len(group_urls),
        len(monitored),
    )


if __name__ == "__main__":
    main()
