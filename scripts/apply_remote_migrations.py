#!/usr/bin/env python3
"""Apply DDL migrations to prod/dev via direct Postgres connection.

Requires database URLs from Supabase Dashboard → Project Settings → Database:
  PROD_DATABASE_URL=postgresql://postgres.[ref]:[password]@...
  DEV_DATABASE_URL=postgresql://postgres.[ref]:[password]@...

Usage:
  python scripts/apply_remote_migrations.py --dry-run
  python scripts/apply_remote_migrations.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_MIGRATIONS = ROOT.parent / "UGetFirst_web" / "supabase" / "migrations"

MIGRATIONS = [
    ("both", WEB_MIGRATIONS / "012_sms_sendouts.sql"),
    ("both", WEB_MIGRATIONS / "013_engine_runs.sql"),
    ("both", WEB_MIGRATIONS / "014_facebook_groups_curation.sql"),
    ("prod", WEB_MIGRATIONS / "013_drop_dev_schema.sql"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    prod_url = os.getenv("PROD_DATABASE_URL", "").strip()
    dev_url = os.getenv("DEV_DATABASE_URL", "").strip()

    for scope, path in MIGRATIONS:
        sql = path.read_text(encoding="utf-8")
        targets: list[tuple[str, str]] = []
        if scope in ("both", "prod") and prod_url:
            targets.append(("PROD", prod_url))
        if scope in ("both", "dev") and dev_url:
            targets.append(("DEV", dev_url))
        if not targets:
            label = scope.upper() if scope != "both" else "PROD+DEV"
            print(f"Skip {path.name} ({label}): missing DATABASE_URL env var(s)")
            continue
        for label, url in targets:
            print(f"{'Would apply' if args.dry_run else 'Applying'} {path.name} → {label}")
            if args.dry_run:
                continue
            try:
                import psycopg2
            except ImportError:
                raise SystemExit("pip install psycopg2-binary")
            conn = psycopg2.connect(url)
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                print(f"  OK {label}")
            finally:
                conn.close()

    if args.dry_run:
        print("Dry-run only. Set PROD_DATABASE_URL / DEV_DATABASE_URL and re-run with --apply.")


if __name__ == "__main__":
    main()
