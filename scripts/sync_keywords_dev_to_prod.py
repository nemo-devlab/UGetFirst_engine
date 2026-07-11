#!/usr/bin/env python3
"""Overwrite PROD keywords with the cleaned set from DEV (match by phone).

- Does not create/delete prod subscribers
- Skips phones that exist only on DEV
- Replaces each overlapping subscriber's keyword rows entirely

Usage:
  python scripts/sync_keywords_dev_to_prod.py --dry-run
  python scripts/sync_keywords_dev_to_prod.py --apply
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("sync_keywords_dev_to_prod")

PAGE = 1000


def make_client(url: str, key: str) -> Client:
    if not url or not key:
        raise RuntimeError("Missing Supabase URL or service-role key")
    return create_client(url, key)


def fetch_all(client: Client, table: str, cols: str) -> list[dict]:
    out: list[dict] = []
    start = 0
    while True:
        rows = (
            client.schema("public")
            .table(table)
            .select(cols)
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


def keywords_by_phone(
    subs: list[dict], kws: list[dict]
) -> dict[str, list[str]]:
    by_id: dict[str, list[str]] = defaultdict(list)
    for row in kws:
        by_id[row["subscriber_id"]].append(row["keyword"] or "")
    out: dict[str, list[str]] = {}
    for sub in subs:
        phone = (sub.get("phone") or "").strip()
        if not phone:
            continue
        # Preserve order, drop empties
        out[phone] = [k for k in by_id.get(sub["id"], []) if k]
    return out


def replace_keywords(client: Client, subscriber_id: str, keywords: list[str]) -> None:
    client.schema("public").table("keywords").delete().eq(
        "subscriber_id", subscriber_id
    ).execute()
    if not keywords:
        return
    rows = [{"subscriber_id": subscriber_id, "keyword": kw} for kw in keywords]
    for i in range(0, len(rows), 50):
        client.schema("public").table("keywords").insert(rows[i : i + 50]).execute()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not config.PROD_SUPABASE_URL or not config.PROD_SUPABASE_SERVICE_ROLE_KEY:
        raise SystemExit("PROD_SUPABASE_URL / PROD_SUPABASE_SERVICE_ROLE_KEY required")
    if not config.DEV_SUPABASE_URL or not config.DEV_SUPABASE_SERVICE_ROLE_KEY:
        raise SystemExit("DEV_SUPABASE_URL / DEV_SUPABASE_SERVICE_ROLE_KEY required")

    prod = make_client(config.PROD_SUPABASE_URL, config.PROD_SUPABASE_SERVICE_ROLE_KEY)
    dev = make_client(config.DEV_SUPABASE_URL, config.DEV_SUPABASE_SERVICE_ROLE_KEY)

    log.info("Reading DEV: %s", config.DEV_SUPABASE_URL)
    dev_subs = fetch_all(dev, "subscribers", "id, phone")
    dev_kws = fetch_all(dev, "keywords", "subscriber_id, keyword")
    log.info("DEV: %d subscribers, %d keywords", len(dev_subs), len(dev_kws))

    log.info("Reading PROD: %s", config.PROD_SUPABASE_URL)
    prod_subs = fetch_all(prod, "subscribers", "id, phone")
    prod_kws = fetch_all(prod, "keywords", "subscriber_id, keyword")
    log.info("PROD: %d subscribers, %d keywords", len(prod_subs), len(prod_kws))

    dev_by_phone = keywords_by_phone(dev_subs, dev_kws)
    prod_by_phone = keywords_by_phone(prod_subs, prod_kws)
    prod_id_by_phone = {
        (s.get("phone") or "").strip(): s["id"]
        for s in prod_subs
        if s.get("phone")
    }

    overlap = sorted(set(dev_by_phone) & set(prod_id_by_phone))
    skipped_dev_only = sorted(set(dev_by_phone) - set(prod_id_by_phone))
    log.info(
        "Will overwrite %d prod subscribers; skip %d DEV-only phone(s): %s",
        len(overlap),
        len(skipped_dev_only),
        skipped_dev_only or "—",
    )

    changed = 0
    for phone in overlap:
        before = prod_by_phone.get(phone, [])
        after = dev_by_phone.get(phone, [])
        if [k.lower() for k in before] != [k.lower() for k in after] or before != after:
            changed += 1
            log.info(
                "  %s: %d -> %d  %s => %s",
                phone,
                len(before),
                len(after),
                before[:5],
                after[:5],
            )

    log.info("%d/%d overlapping phones have different keywords", changed, len(overlap))

    if args.dry_run:
        log.info("Dry-run only. Re-run with --apply to overwrite PROD keywords.")
        return

    for phone in overlap:
        sid = prod_id_by_phone[phone]
        after = dev_by_phone.get(phone, [])
        replace_keywords(prod, sid, after)

    after_kws = fetch_all(prod, "keywords", "id")
    log.info(
        "Done. PROD keywords now %d (was %d). Overwrote %d subscribers. DEV untouched.",
        len(after_kws),
        len(prod_kws),
        len(overlap),
    )


if __name__ == "__main__":
    main()
