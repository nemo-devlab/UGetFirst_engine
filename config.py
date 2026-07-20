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

# SMS: "simulated" (outbox/) or "twilio". Twilio also requires TWILIO_* below.
_sms_mode = os.getenv("SMS_MODE", "").strip().lower()
if _sms_mode in ("simulated", "twilio"):
    SMS_MODE = _sms_mode
else:
    # Default: twilio when creds present, else simulated.
    SMS_MODE = (
        "twilio"
        if (
            os.getenv("TWILIO_ACCOUNT_SID", "").strip()
            and os.getenv("TWILIO_AUTH_TOKEN", "").strip()
            and os.getenv("TWILIO_FROM_NUMBER", "").strip()
        )
        else "simulated"
    )

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "").strip()
# Shared secret for inbound STOP/HELP webhook (optional but recommended).
TWILIO_WEBHOOK_AUTH_TOKEN = os.getenv("TWILIO_WEBHOOK_AUTH_TOKEN", "").strip() or TWILIO_AUTH_TOKEN

# Cap SMS sends per subscriber within a single engine cycle (0 = unlimited).
SMS_MAX_PER_SUBSCRIBER_PER_CYCLE = int(
    os.getenv("SMS_MAX_PER_SUBSCRIBER_PER_CYCLE", "5")
)
# Pause between Twilio/outbox sends in a cycle (Twilio 429 cushion). 0 = no delay.
SMS_SEND_DELAY_MS = int(os.getenv("SMS_SEND_DELAY_MS", "250"))

MIN_INTERVAL_SECONDS = int(os.getenv("MIN_INTERVAL_SECONDS", "600"))
# Per-group post cap. Total resultsLimit for an Apify run = max(RESULTS_LIMIT, groups * RESULTS_PER_GROUP).
RESULTS_LIMIT = int(os.getenv("RESULTS_LIMIT", "20"))
RESULTS_PER_GROUP = int(os.getenv("RESULTS_PER_GROUP", "10"))
# Max groups per Apify actor call (batches when catalog is large).
SCRAPE_BATCH_SIZE = int(os.getenv("SCRAPE_BATCH_SIZE", "25"))
APIFY_MAX_RETRIES = int(os.getenv("APIFY_MAX_RETRIES", "1"))

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
