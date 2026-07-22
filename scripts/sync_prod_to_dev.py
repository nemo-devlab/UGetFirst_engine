#!/usr/bin/env python3
"""Mirror account/catalog tables + Auth users from PROD → DEV.

Large volume / asset tables are excluded from wipe and copy (each env keeps
its own history):
  notification_logs, sms_sendouts, engine_runs, scraped_posts

Subscribers and facebook_groups are upserted (not wiped) so CASCADE does not
erase excluded child rows that still reference surviving UUIDs.

Preserves row UUIDs so FK relationships stay intact. Auth users (with password
hashes) are synced first via PROD_DATABASE_URL so subscribers.user_id stays
valid for Dev logins with the same passwords.

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
from typing import Any

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
NIL_UUID = "00000000-0000-0000-0000-000000000000"

# Never wipe or copy — high volume / data assets stay per-environment.
SKIP_TABLES = (
    "notification_logs",
    "sms_sendouts",
    "engine_runs",
    "scraped_posts",
)

# Upsert parents (avoids CASCADE wipe of SKIP_TABLES).
UPSERT_TABLES = [
    "subscribers",
    "facebook_groups",
]

# Wipe + re-insert children (safe; no large logs).
WIPE_COPY_TABLES = [
    "keywords",
    "monitored_groups",
    "onboarding",
    "email_verifications",
]

# Fetch order for dry-run counts / apply payload.
COPY_TABLES = UPSERT_TABLES + WIPE_COPY_TABLES

# Wipe order: children before any parent orphan cleanup.
WIPE_TABLES = list(WIPE_COPY_TABLES)

AUTH_SELECT = """
  SELECT
    id::text AS id,
    email,
    encrypted_password,
    email_confirmed_at,
    phone,
    phone_confirmed_at,
    raw_user_meta_data,
    raw_app_meta_data,
    COALESCE(is_sso_user, false) AS is_sso_user,
    COALESCE(is_anonymous, false) AS is_anonymous
  FROM auth.users
"""

AUTH_SELECT_LEGACY = """
  SELECT
    id::text AS id,
    email,
    encrypted_password,
    email_confirmed_at,
    phone,
    phone_confirmed_at,
    raw_user_meta_data,
    raw_app_meta_data,
    false AS is_sso_user,
    false AS is_anonymous
  FROM auth.users
"""


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


def fetch_ids(client: Client, table: str) -> set[str]:
    ids: set[str] = set()
    start = 0
    while True:
        rows = (
            client.schema("public")
            .table(table)
            .select("id")
            .range(start, start + PAGE - 1)
            .execute()
            .data
            or []
        )
        for row in rows:
            if row.get("id"):
                ids.add(row["id"])
        if len(rows) < PAGE:
            break
        start += PAGE
    return ids


def table_exists(client: Client, table: str) -> bool:
    try:
        client.schema("public").table(table).select("id").limit(1).execute()
        return True
    except Exception as exc:
        if "42P01" in str(exc) or "does not exist" in str(exc).lower():
            return False
        try:
            client.schema("public").table(table).select("*").limit(1).execute()
            return True
        except Exception:
            return False


def wipe_table(client: Client, table: str) -> None:
    if not table_exists(client, table):
        log.info("  skip wipe %s (missing)", table)
        return
    client.schema("public").table(table).delete().neq("id", NIL_UUID).execute()
    log.info("  wiped %s", table)


def insert_batch(client: Client, table: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), 50):
        client.schema("public").table(table).insert(rows[i : i + 50]).execute()


def upsert_batch(client: Client, table: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), 50):
        client.schema("public").table(table).upsert(rows[i : i + 50]).execute()


def delete_orphans(client: Client, table: str, keep_ids: set[str]) -> int:
    """Delete DEV rows whose id is not in keep_ids. Returns deleted count estimate."""
    if not table_exists(client, table):
        return 0
    dev_ids = fetch_ids(client, table)
    orphans = list(dev_ids - keep_ids)
    for i in range(0, len(orphans), 50):
        chunk = orphans[i : i + 50]
        client.schema("public").table(table).delete().in_("id", chunk).execute()
    if orphans:
        log.info("  removed %d orphan %s row(s)", len(orphans), table)
    return len(orphans)


def fetch_prod_auth_users(database_url: str) -> list[dict[str, Any]]:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as exc:
        raise SystemExit("pip install psycopg2-binary") from exc

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(AUTH_SELECT)
            except Exception as exc:
                msg = str(exc).lower()
                if "is_sso_user" in msg or "is_anonymous" in msg or "does not exist" in msg:
                    conn.rollback()
                    cur.execute(AUTH_SELECT_LEGACY)
                else:
                    raise
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _meta(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    return None


def build_auth_attrs(user: dict[str, Any], *, include_id: bool) -> dict[str, Any]:
    email = (user.get("email") or "").strip() or None
    phone = (user.get("phone") or "").strip() or None
    password_hash = (user.get("encrypted_password") or "").strip() or None
    attrs: dict[str, Any] = {
        "email_confirm": bool(user.get("email_confirmed_at")) or bool(email),
        "phone_confirm": bool(user.get("phone_confirmed_at")),
    }
    if include_id:
        attrs["id"] = user["id"]
    if email:
        attrs["email"] = email
    if phone:
        attrs["phone"] = phone
    if password_hash:
        attrs["password_hash"] = password_hash
    user_meta = _meta(user.get("raw_user_meta_data"))
    app_meta = _meta(user.get("raw_app_meta_data"))
    if user_meta is not None:
        attrs["user_metadata"] = user_meta
    if app_meta is not None:
        attrs["app_metadata"] = app_meta
    return attrs


def auth_user_exists(dev: Client, user_id: str) -> bool:
    try:
        resp = dev.auth.admin.get_user_by_id(user_id)
        return bool(getattr(resp, "user", None) or (isinstance(resp, dict) and resp.get("user")))
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "user not found" in msg:
            return False
        # Some GoTrue versions raise for missing users — treat as missing.
        if "404" in msg:
            return False
        raise


def sync_auth_users(dev: Client, database_url: str, *, dry_run: bool) -> dict[str, int]:
    rows = fetch_prod_auth_users(database_url)
    candidates: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        if row.get("is_sso_user") or row.get("is_anonymous"):
            skipped += 1
            continue
        email = (row.get("email") or "").strip()
        phone = (row.get("phone") or "").strip()
        password_hash = (row.get("encrypted_password") or "").strip()
        if not (email or phone) or not password_hash:
            skipped += 1
            continue
        candidates.append(row)

    stats = {
        "prod_users": len(candidates),
        "created": 0,
        "updated": 0,
        "synced": 0,
        "skipped": skipped,
    }
    log.info(
        "Auth: %d candidate user(s) from PROD (%d skipped filters)",
        len(candidates),
        skipped,
    )
    if dry_run:
        return stats

    for user in candidates:
        try:
            exists = auth_user_exists(dev, user["id"])
            if exists:
                dev.auth.admin.update_user_by_id(
                    user["id"], build_auth_attrs(user, include_id=False)
                )
                stats["updated"] += 1
            else:
                dev.auth.admin.create_user(build_auth_attrs(user, include_id=True))
                stats["created"] += 1
            stats["synced"] += 1
        except Exception as exc:
            stats["skipped"] += 1
            log.warning(
                "  Auth skip %s (%s): %s",
                user.get("id"),
                user.get("email") or user.get("phone"),
                exc,
            )
    log.info(
        "Auth synced %d (created %d, updated %d, skipped %d)",
        stats["synced"],
        stats["created"],
        stats["updated"],
        stats["skipped"],
    )
    return stats


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

    prod_database_url = os.getenv("PROD_DATABASE_URL", "").strip()
    if args.apply and not prod_database_url:
        raise SystemExit(
            "PROD_DATABASE_URL is required to sync Auth users with password hashes. "
            "Add the Prod Postgres URI (Dashboard → Database → Connection string)."
        )

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
        "Skip (no wipe/copy): %s",
        ", ".join(SKIP_TABLES),
    )
    log.info(
        "Would wipe DEV: %s; upsert: %s; then copy children",
        ", ".join(WIPE_TABLES),
        ", ".join(UPSERT_TABLES),
    )
    log.info(
        "PROD subscribers to sync: %d",
        len(prod_data.get("subscribers", [])),
    )

    if prod_database_url:
        sync_auth_users(dev, prod_database_url, dry_run=True)
    else:
        log.warning("PROD_DATABASE_URL unset — Auth sync skipped in dry-run counts")

    if args.dry_run:
        log.info(
            "Dry-run only. Re-run with --apply to overwrite DEV account/catalog + Auth."
        )
        return

    log.info("Syncing Auth users (password hashes) PROD → DEV…")
    sync_auth_users(dev, prod_database_url, dry_run=False)

    log.info("Wiping DEV child tables…")
    for table in WIPE_TABLES:
        wipe_table(dev, table)

    log.info("Upserting PROD parents → DEV…")
    for table in UPSERT_TABLES:
        rows = prod_data.get(table, [])
        if not rows:
            continue
        upsert_batch(dev, table, rows)
        log.info("  upserted %d row(s) into %s", len(rows), table)
        keep = {r["id"] for r in rows if r.get("id")}
        delete_orphans(dev, table, keep)

    log.info("Copying PROD children → DEV…")
    for table in WIPE_COPY_TABLES:
        rows = prod_data.get(table, [])
        if not rows:
            continue
        insert_batch(dev, table, rows)
        log.info("  copied %d row(s) into %s", len(rows), table)

    log.info(
        "Done. DEV account/catalog + Auth mirror PROD; skipped %s.",
        ", ".join(SKIP_TABLES),
    )


if __name__ == "__main__":
    main()
