"""Central configuration loaded from environment variables.

Prod and Dev are separate Supabase projects. Schema is always `public`.
`ENV=dev|prod` selects which project's URL + service-role key to use.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


ENV = os.getenv("ENV", "dev").strip().lower()
if ENV not in ("dev", "prod"):
    raise RuntimeError(f"ENV must be 'dev' or 'prod', got {ENV!r}")

# Always public — separate projects, not a shared-project "dev" schema.
DB_SCHEMA = "public"

if ENV == "dev":
    SUPABASE_URL = os.getenv("DEV_SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    SUPABASE_SERVICE_ROLE_KEY = (
        os.getenv("DEV_SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "Missing DEV_SUPABASE_URL / DEV_SUPABASE_SERVICE_ROLE_KEY "
            "(or fallback NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY)"
        )
else:
    SUPABASE_URL = os.getenv("PROD_SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    SUPABASE_SERVICE_ROLE_KEY = (
        os.getenv("PROD_SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "Missing PROD_SUPABASE_URL / PROD_SUPABASE_SERVICE_ROLE_KEY "
            "(or fallback NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY)"
        )

# Explicit prod/dev creds (used by one-off scripts that talk to both projects).
PROD_SUPABASE_URL = os.getenv("PROD_SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")
PROD_SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("PROD_SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
)
DEV_SUPABASE_URL = os.getenv("DEV_SUPABASE_URL", "")
DEV_SUPABASE_SERVICE_ROLE_KEY = os.getenv("DEV_SUPABASE_SERVICE_ROLE_KEY", "")

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
APIFY_ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "apify/facebook-groups-scraper")

# SMS is simulated for now: the notifier writes one .txt file per message into
# outbox/. A real provider will be added once 10DLC registration clears.

MIN_INTERVAL_SECONDS = int(os.getenv("MIN_INTERVAL_SECONDS", "120"))
RESULTS_LIMIT = int(os.getenv("RESULTS_LIMIT", "20"))

# Time-based scrape window passed to the actor's `onlyPostsNewerThan`.
# Prefer LOOKBACK ("10 minutes", "1 day"). LOOKBACK_MINUTES is accepted as a
# convenience alias from older .env files.
_lookback = os.getenv("LOOKBACK", "").strip()
if not _lookback:
    _minutes = os.getenv("LOOKBACK_MINUTES", "").strip()
    if _minutes.isdigit():
        _lookback = f"{int(_minutes)} minutes"
    else:
        _lookback = "1 day"
LOOKBACK = _lookback
