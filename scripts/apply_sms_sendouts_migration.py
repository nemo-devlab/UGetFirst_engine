#!/usr/bin/env python3
"""Apply sms_sendouts migration to prod and dev Supabase projects.

Usage:
  python scripts/apply_sms_sendouts_migration.py
"""
from __future__ import annotations

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

import config  # noqa: E402

MIGRATION = """
create table if not exists public.sms_sendouts (
  id uuid primary key default gen_random_uuid(),
  subscriber_id uuid not null references public.subscribers(id) on delete cascade,
  notification_log_id uuid references public.notification_logs(id) on delete set null,
  phone text not null,
  body text not null,
  keyword text not null,
  post_url text not null,
  channel text not null default 'simulated'
    check (channel in ('simulated', 'twilio')),
  status text not null default 'sent'
    check (status in ('sent', 'failed', 'skipped')),
  provider_message_id text,
  error text,
  created_at timestamptz not null default now()
);

create index if not exists sms_sendouts_created_at_idx
  on public.sms_sendouts (created_at desc);

create index if not exists sms_sendouts_subscriber_id_idx
  on public.sms_sendouts (subscriber_id);

alter table public.sms_sendouts enable row level security;
"""


def apply_via_psycopg2(db_url: str, label: str) -> None:
    try:
        import psycopg2
    except ImportError:
        raise SystemExit("psycopg2 required: pip install psycopg2-binary")

    if not db_url:
        print(f"Skip {label}: no DATABASE_URL")
        return
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(MIGRATION)
        print(f"Applied sms_sendouts on {label}")
    finally:
        conn.close()


def main() -> None:
    prod_url = os.getenv("PROD_DATABASE_URL", "")
    dev_url = os.getenv("DEV_DATABASE_URL", "")
    apply_via_psycopg2(prod_url, "PROD")
    apply_via_psycopg2(dev_url, "DEV")


if __name__ == "__main__":
    main()
