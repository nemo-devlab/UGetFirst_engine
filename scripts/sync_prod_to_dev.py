#!/usr/bin/env python3
"""Wipe DEV and copy all prod data except notification_logs.

Preserves row UUIDs so FK relationships stay intact. Sets user_id to null on
subscribers (auth users differ between projects).

Usage:
  python scripts/sync_prod_to_dev.py --dry-run
  python scripts/sync_prod_to_dev.py --apply
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("sync_prod_to_dev")

PAGE = 1000

# Copy order: parents before children.
COPY_TABLES = [
    "subscribers",
    "facebook_groups",
    "keywords",
    "monitored_groups",
    "onboarding",
    "email_verifications",
]

# Wipe order: children before parents.
WIPE_TABLES = [
    "sms_sendouts",
    "notification_logs",
    "engine_runs",
    "keywords",
    "monitored_groups",
    "onboarding",
    "email_verifications",
    "facebook_groups",
    "subscribers",
]


def make_client(url: str, key: str) -> Client:
    if not url or not key:
        raise RuntimeError("Missing Supabase URL or service-role key")
    return create_client(url, key)


def fetch_all(client: Client, table: str) -> list[dict]:
    out: list[dict] = []
    start = 0
    while True:
        rows = (
            client.schema("public")
            .table(table)
            .select("*")
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


def table_exists(client: Client, table: str) -> bool:
    try:
        client.schema("public").table(table).select("id").limit(1).execute()
        return True
    except Exception as exc:
        if "42P01" in str(exc) or "does not exist" in str(exc).lower():
            return False
        # Some tables use different PK; try count via select *
        try:
            client.schema("public").table(table).select("*").limit(1).execute()
            return True
        except Exception:
            return False


def wipe_table(client: Client, table: str) -> None:
    if not table_exists(client, table):
        log.info("  skip wipe %s (missing)", table)
        return
    # PostgREST requires a filter on delete; id not null matches all uuid rows.
    client.schema("public").table(table).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
    log.info("  wiped %s", table)


def insert_batch(client: Client, table: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), 50):
        client.schema("public").table(table).insert(rows[i : i + 50]).execute()


def prepare_subscribers(rows: list[dict]) -> list[dict]:
    prepared = []
    for row in rows:
        copy = dict(row)
        copy["user_id"] = None
        prepared.append(copy)
    return prepared


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

    prod_data: dict[str, list[dict]] = {}
    for table in COPY_TABLES:
        if not table_exists(prod, table):
            log.warning("PROD table missing: %s", table)
            prod_data[table] = []
            continue
        rows = fetch_all(prod, table)
        prod_data[table] = rows
        log.info("PROD %s: %d row(s)", table, len(rows))

    log.info(
        "Would wipe DEV tables: %s; copy %d subscribers",
        ", ".join(WIPE_TABLES),
        len(prod_data.get("subscribers", [])),
    )

    if args.dry_run:
        log.info("Dry-run only. Re-run with --apply to overwrite DEV.")
        return

    log.info("Wiping DEV…")
    for table in WIPE_TABLES:
        wipe_table(dev, table)

    log.info("Copying PROD → DEV…")
    for table in COPY_TABLES:
        rows = prod_data.get(table, [])
        if not rows:
            continue
        if table == "subscribers":
            rows = prepare_subscribers(rows)
        insert_batch(dev, table, rows)
        log.info("  copied %d row(s) into %s", len(rows), table)

    log.info("Done. DEV now mirrors PROD (excluding notification_logs).")


if __name__ == "__main__":
    main()
