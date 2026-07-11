#!/usr/bin/env python3
"""Backfill facebook_group_id, canonical_url, and group_name on existing rows.

Usage:
  python scripts/backfill_group_ids.py --dry-run
  python scripts/backfill_group_ids.py --apply
  python scripts/backfill_group_ids.py --apply --fetch-names
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import groups  # noqa: E402
from db import client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("backfill_group_ids")

PAGE = 1000


def fetch_all(table: str, columns: str) -> list[dict]:
    out: list[dict] = []
    start = 0
    while True:
        rows = (
            client()
            .schema(config.DB_SCHEMA)
            .table(table)
            .select(columns)
            .range(start, start + PAGE - 1)
            .execute()
            .data
            or []
        )
        out.extend(rows)
        if len(rows) < PAGE:
            break
        start += PAGE
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--fetch-names",
        action="store_true",
        help="Call Apify for catalog rows still missing a real name.",
    )
    args = parser.parse_args()

    catalog = fetch_all(
        "facebook_groups",
        "id, name, group_url, facebook_group_id, canonical_url",
    )
    monitored = fetch_all(
        "monitored_groups",
        "id, group_url, facebook_group_id, group_name",
    )

    catalog_by_gid: dict[str, str] = {}
    catalog_updates = 0
    for row in catalog:
        normalized = groups.normalize_group_url(row.get("group_url") or "")
        if not normalized:
            log.warning("Skip catalog row %s: invalid URL %r", row["id"], row.get("group_url"))
            continue
        gid = normalized["facebook_group_id"]
        curl = normalized["canonical_url"]
        catalog_by_gid[gid] = row.get("name") or ""
        needs_update = (
            row.get("facebook_group_id") != gid
            or row.get("canonical_url") != curl
            or row.get("group_url") != curl
        )
        if needs_update:
            catalog_updates += 1
            log.info("Catalog %s -> gid=%s name=%r", row["id"], gid, row.get("name"))
            if args.apply:
                (
                    client()
                    .schema(config.DB_SCHEMA)
                    .table("facebook_groups")
                    .update(
                        {
                            "facebook_group_id": gid,
                            "canonical_url": curl,
                            "group_url": curl,
                            "discovery_source": "manual",
                        }
                    )
                    .eq("id", row["id"])
                    .execute()
                )

    if args.fetch_names and args.apply:
        for row in catalog:
            normalized = groups.normalize_group_url(row.get("group_url") or "")
            if not normalized:
                continue
            name = (row.get("name") or "").strip()
            if name and not name.startswith("Facebook Group "):
                continue
            groups.resolve_catalog_group(
                client(),
                normalized["canonical_url"],
                discovery_source="manual",
                fetch_if_missing=True,
            )

    monitored_updates = 0
    for row in monitored:
        normalized = groups.normalize_group_url(row.get("group_url") or "")
        if not normalized:
            log.warning(
                "Skip monitored row %s: invalid URL %r",
                row["id"],
                row.get("group_url"),
            )
            continue
        gid = normalized["facebook_group_id"]
        curl = normalized["canonical_url"]
        name = catalog_by_gid.get(gid) or row.get("group_name")
        needs_update = (
            row.get("facebook_group_id") != gid
            or row.get("group_url") != curl
            or (name and row.get("group_name") != name)
        )
        if needs_update:
            monitored_updates += 1
            log.info(
                "Monitored %s -> gid=%s name=%r url=%s",
                row["id"],
                gid,
                name,
                curl,
            )
            if args.apply:
                payload = {
                    "facebook_group_id": gid,
                    "group_url": curl,
                }
                if name:
                    payload["group_name"] = name
                (
                    client()
                    .schema(config.DB_SCHEMA)
                    .table("monitored_groups")
                    .update(payload)
                    .eq("id", row["id"])
                    .execute()
                )

    log.info(
        "Done (%s). catalog_updates=%d monitored_updates=%d",
        "apply" if args.apply else "dry-run",
        catalog_updates,
        monitored_updates,
    )


if __name__ == "__main__":
    main()
