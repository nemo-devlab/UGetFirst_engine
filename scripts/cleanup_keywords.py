#!/usr/bin/env python3
"""One-time keyword cleanup: read prod → clean → write into the DEV project.

Does NOT modify prod. Seeds / updates DEV subscribers matched by phone.

Safe transforms:
  - strip wrapping quotes / zero-width / collapse whitespace
  - soft-split on explicit separators only (, * ; | newlines)
  - never auto-split space-only phrases
  - cap at MAX_KEYWORDS (5)

Usage:
  python scripts/cleanup_keywords.py --dry-run
  python scripts/cleanup_keywords.py --apply
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

# Avoid corporate/sandbox HTTP proxies breaking Supabase calls.
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
from matcher import normalize_keyword, soft_split_keywords  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("cleanup_keywords")

MAX_KEYWORDS = 5
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


def clean_keyword_list(raw_keywords: list[str]) -> tuple[list[str], list[str]]:
    """Return (cleaned_deduped capped, notes)."""
    notes: list[str] = []
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in raw_keywords:
        parts = soft_split_keywords(raw)
        if not parts:
            n = normalize_keyword(raw)
            if n and len(n) <= 60:
                parts = [n]
            else:
                notes.append(f"dropped empty/junk: {raw!r}")
                continue
        if len(parts) > 1:
            notes.append(f"split {raw!r} -> {len(parts)} tokens (cap {MAX_KEYWORDS})")
        elif parts and parts[0] != raw:
            notes.append(f"normalize {raw!r} -> {parts[0]!r}")
        for p in parts:
            key = p.lower()
            if key in seen:
                continue
            seen.add(key)
            if len(cleaned) >= MAX_KEYWORDS:
                continue
            cleaned.append(p)
    skipped = len(seen) - len(cleaned)
    if skipped > 0:
        notes.append(f"capped: kept {MAX_KEYWORDS}, skipped {skipped} extras")
    return cleaned, notes


def build_prod_plans(prod_subs: list[dict], prod_kws: list[dict]) -> list[dict]:
    by_sub: dict[str, list[str]] = defaultdict(list)
    for row in prod_kws:
        by_sub[row["subscriber_id"]].append(row["keyword"] or "")

    plans: list[dict] = []
    for sub in prod_subs:
        phone = (sub.get("phone") or "").strip()
        if not phone:
            continue
        raws = by_sub.get(sub["id"], [])
        cleaned, notes = clean_keyword_list(raws)
        plans.append(
            {
                "phone": phone,
                "email": sub.get("email"),
                "notify_sms": bool(sub.get("notify_sms", True)),
                "prod_subscriber_id": sub["id"],
                "raw": raws,
                "cleaned": cleaned,
                "notes": notes,
                "changed": [normalize_keyword(r) for r in raws if normalize_keyword(r)]
                != cleaned
                or any(n.startswith("split") or n.startswith("normalize") for n in notes),
            }
        )
    return plans


def ensure_dev_subscriber(dev: Client, plan: dict, existing_by_phone: dict[str, dict]) -> str:
    """Return DEV subscriber id, creating the row if needed."""
    phone = plan["phone"]
    if phone in existing_by_phone:
        return existing_by_phone[phone]["id"]

    payload: dict = {
        "phone": phone,
        "notify_sms": plan["notify_sms"],
    }
    if plan.get("email"):
        payload["email"] = plan["email"]

    created = (
        dev.schema("public")
        .table("subscribers")
        .insert(payload)
        .select("id, phone")
        .execute()
        .data
    )
    if not created:
        raise RuntimeError(f"Failed to create DEV subscriber for {phone}")
    row = created[0]
    existing_by_phone[phone] = row
    log.info("Created DEV subscriber %s for phone %s", row["id"][:8], phone)
    return row["id"]


def replace_dev_keywords(dev: Client, subscriber_id: str, keywords: list[str]) -> None:
    dev.schema("public").table("keywords").delete().eq(
        "subscriber_id", subscriber_id
    ).execute()
    if not keywords:
        return
    rows = [{"subscriber_id": subscriber_id, "keyword": kw} for kw in keywords]
    for i in range(0, len(rows), 50):
        dev.schema("public").table("keywords").insert(rows[i : i + 50]).execute()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--changed-only",
        action="store_true",
        help="Only write subscribers whose keywords needed normalize/split",
    )
    args = parser.parse_args()

    if not config.PROD_SUPABASE_URL or not config.PROD_SUPABASE_SERVICE_ROLE_KEY:
        raise SystemExit("PROD_SUPABASE_URL / PROD_SUPABASE_SERVICE_ROLE_KEY required")
    if not config.DEV_SUPABASE_URL or not config.DEV_SUPABASE_SERVICE_ROLE_KEY:
        raise SystemExit("DEV_SUPABASE_URL / DEV_SUPABASE_SERVICE_ROLE_KEY required")

    prod = make_client(config.PROD_SUPABASE_URL, config.PROD_SUPABASE_SERVICE_ROLE_KEY)
    dev = make_client(config.DEV_SUPABASE_URL, config.DEV_SUPABASE_SERVICE_ROLE_KEY)

    log.info("Reading prod: %s", config.PROD_SUPABASE_URL)
    prod_subs = fetch_all(
        prod, "subscribers", "id, phone, email, notify_sms"
    )
    prod_kws = fetch_all(prod, "keywords", "id, subscriber_id, keyword")
    log.info("Prod: %d subscribers, %d keywords", len(prod_subs), len(prod_kws))

    log.info("Reading dev: %s", config.DEV_SUPABASE_URL)
    dev_subs = fetch_all(dev, "subscribers", "id, phone")
    dev_kws_before = fetch_all(dev, "keywords", "id")
    log.info("Dev: %d subscribers, %d keywords", len(dev_subs), len(dev_kws_before))

    plans = build_prod_plans(prod_subs, prod_kws)
    changed = [p for p in plans if p["changed"] or p["notes"]]
    log.info(
        "Cleanup: %d/%d prod phones with keywords; %d need normalize/split/cap",
        sum(1 for p in plans if p["raw"]),
        len(plans),
        len(changed),
    )

    for plan in changed[:30]:
        log.info(
            "  %s raw=%d cleaned=%s notes=%s",
            plan["phone"],
            len(plan["raw"]),
            plan["cleaned"],
            plan["notes"][:4],
        )
    if len(changed) > 30:
        log.info("  … %d more", len(changed) - 30)

    to_write = changed if args.changed_only else [p for p in plans if p["raw"] or p["cleaned"]]
    # Always include changed; if not changed-only, seed everyone who has keywords
    if not args.changed_only:
        to_write = [p for p in plans if p["cleaned"] or p["raw"]]

    log.info("Would write %d subscriber keyword sets into DEV", len(to_write))

    if args.dry_run:
        log.info("Dry-run only. Re-run with --apply to seed DEV.")
        return

    existing = {s["phone"]: s for s in dev_subs if s.get("phone")}
    written = 0
    for plan in to_write:
        sid = ensure_dev_subscriber(dev, plan, existing)
        replace_dev_keywords(dev, sid, plan["cleaned"])
        written += 1
        if plan["notes"]:
            log.info(
                "Wrote %s (%s): %d -> %d keywords",
                sid[:8],
                plan["phone"],
                len(plan["raw"]),
                len(plan["cleaned"]),
            )

    after = fetch_all(dev, "keywords", "id")
    after_subs = fetch_all(dev, "subscribers", "id")
    log.info(
        "Done. DEV subscribers=%d (was %d), keywords=%d (was %d). Wrote %d sets. Prod untouched.",
        len(after_subs),
        len(dev_subs),
        len(after),
        len(dev_kws_before),
        written,
    )


if __name__ == "__main__":
    main()
