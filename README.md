# UGetFirst Engine

Scraping + notification worker for **UGetFirst** — *"First to Know. First to Win."*
Polls public Facebook groups for user keywords and sends an SMS the moment a
matching post appears.

This repo shares Supabase with the website and admin repos. **Prod and Dev are
separate Supabase projects** (schema is always `public` in both).

## How it works

```
loop (every MIN_INTERVAL_SECONDS, non-overlapping):
  1. load monitored groups + subscribers + keywords   (db.py)
  2. scrape all distinct group URLs in ONE Apify run   (scraper.py)
  3. for each post, case-insensitively match keywords  (matcher.py)
  4. INSERT notification_logs (unique subscriber_id, post_url)  -> idempotency
  5. if newly inserted AND sms_enabled (notify_sms + consent + phone): send SMS + log sms_sendouts (notifier.py)
  6. log cycle metrics to engine_runs (posts_scraped, matches_found, apify_run_id)
```

- **Dedup / idempotency:** the unique `(subscriber_id, post_url)` constraint on
  `notification_logs` guarantees a subscriber is never texted twice for the same
  post. We only send when the insert is new.
- **New posts:** each cycle we fetch posts within the `LOOKBACK` time window
  (`onlyPostsNewerThan`, newest-first, capped at `RESULTS_LIMIT`). Dedup above
  ensures we only text on posts we haven't seen before. Stateless — no
  high-water-mark table needed.

## Environments (dev vs prod)

Controlled by `ENV`. Schema is always `public`; the project URL + key change:

| ENV    | Project        | Data             |
| ------ | -------------- | ---------------- |
| `dev`  | UGetFirst-dev  | test subscribers |
| `prod` | UGetFirst      | real subscribers |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the repo root with these variables:

```
ENV=dev

PROD_SUPABASE_URL=https://vtumfvzvnrfwfkkwskhk.supabase.co
PROD_SUPABASE_SERVICE_ROLE_KEY=

DEV_SUPABASE_URL=https://pmzohuovdlsigroztcgq.supabase.co
DEV_SUPABASE_SERVICE_ROLE_KEY=

APIFY_TOKEN=
APIFY_ACTOR_ID=apify/facebook-groups-scraper
MIN_INTERVAL_SECONDS=600
RESULTS_LIMIT=20
# Time window for scraping, e.g. "10 minutes", "6 hours", "1 day". Empty = off.
LOOKBACK=30 minutes
```

Required for the active `ENV`: that project's URL + service-role key, plus
`APIFY_TOKEN`.

**SMS** uses Twilio when `SMS_MODE=twilio` (or when `TWILIO_*` are set and
`SMS_MODE` is unset). Otherwise messages are written to `outbox/` (`simulated`).
Apply `../UGetFirst_web/supabase/migrations/012_sms_sendouts.sql`
on **both** Supabase projects before running the engine.

Each cycle also writes one row to `engine_runs` (`posts_scraped`, `matches_found`,
`sms_dispatched`, `apify_run_id`). Apply
`../UGetFirst_web/supabase/migrations/013_engine_runs.sql` on both projects, or:

```bash
PROD_DATABASE_URL=... DEV_DATABASE_URL=... python scripts/apply_remote_migrations.py --apply
```

## Run

```bash
# one cycle, and log raw Apify item keys (use once to confirm output fields)
python main.py --once --dump-raw-keys

# one cycle
python main.py --once

# continuous 24/7 loop (HTTP health on :8080 — see deploy/DIGITALOCEAN_UPTIME.md)
python main.py
```

### DEV test setup (prod mirror + curated groups)

```bash
# Wipe DEV and copy prod subscribers/keywords/etc. (excludes notification_logs)
python scripts/sync_prod_to_dev.py --dry-run
python scripts/sync_prod_to_dev.py --apply

# Replace facebook_groups catalog + assign all DEV subscribers to test groups
python scripts/seed_test_groups.py --apply
```

Edit `FACEBOOK_GROUPS` in `scripts/seed_test_groups.py` to change the curated list.

### One-time keyword cleanup (prod → cleaned copy on dev)

```bash
# Preview only
python scripts/cleanup_keywords.py --dry-run

# Write cleaned keywords into the DEV project (does not modify prod)
python scripts/cleanup_keywords.py --apply
```

## Notes / TODO before production

- **Wire Twilio:** set `SMS_MODE=twilio` + `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` /
  `TWILIO_FROM_NUMBER` after 10DLC clears. Until then keep simulated outbox.
- **Inbound STOP/HELP:** configure Twilio Messaging webhook to the web app
  `POST /api/twilio/inbound` (see `UGetFirst_web/docs/A2P_RESUBMIT.md`).
- Confirm Apify output with `--once --dump-raw-keys` after actor upgrades.
- Many waitlist users have **no group URL** yet — those subscribers simply won't
  match anything until a group is added.
- Scale knobs: `RESULTS_PER_GROUP`, `SCRAPE_BATCH_SIZE`, `APIFY_MAX_RETRIES`.
- For real 24/7 reliability, run under systemd on the VPS (see `deploy/`).
