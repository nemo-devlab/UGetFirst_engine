"""Central configuration loaded from environment variables."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


ENV = os.getenv("ENV", "dev").strip().lower()

# The `dev` schema mirrors `public` for safe testing (see README).
DB_SCHEMA = "dev" if ENV == "dev" else "public"

SUPABASE_URL = _require("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = _require("SUPABASE_SERVICE_ROLE_KEY")

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
APIFY_ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "apify/facebook-groups-scraper")

# SMS is simulated for now: the notifier writes one .txt file per message into
# outbox/. A real provider will be added once 10DLC registration clears.

MIN_INTERVAL_SECONDS = int(os.getenv("MIN_INTERVAL_SECONDS", "120"))
RESULTS_LIMIT = int(os.getenv("RESULTS_LIMIT", "20"))

# Time-based scrape window passed to the actor's `onlyPostsNewerThan`.
# Relative format, e.g. "10 minutes", "6 hours", "1 day". Leave empty to disable
# the time filter (fetch newest posts by count only).
LOOKBACK = os.getenv("LOOKBACK", "1 day").strip()
