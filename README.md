# UGetFirst Engine

Scraping + notification worker for **UGetFirst** — *"First to Know. First to Win."*
Polls public Facebook groups for user keywords and sends email and/or SMS when
a matching post appears.

This repo shares Supabase with the website and admin repos. **Prod and Dev are
separate Supabase projects** (schema is always `public` in both).

## How it works

```
loop (every MIN_INTERVAL_SECONDS tick, non-overlapping):
  1. load groups with ≥1 alert-ready subscriber (+ keywords)  (db.py)
  2. filter to due groups (Free 30m / Speed 20m / Lightning 10m)
  3. scrape due URLs (batched Apify; LOOKBACK from max due interval)
  4. stamp facebook_groups.last_scraped_at
  5. upsert posts into scraped_posts (data asset)             (db.py)
  6. match keywords                                           (matcher.py)
  7. INSERT notification_logs (unique subscriber_id, post_url)
  8. SMS and/or email + sms_sendouts                          (notifier.py)
  9. log cycle metrics to engine_runs
```

- **Alert-ready:** keywords + (`notify_email`+email) and/or (Speed/Lightning
  billable + SMS consent). Free email-only watchers keep groups in Apify at ~30m.
- **Cadence:** fastest watcher on a group wins; undued groups are skipped each tick.
- **Data asset:** every scraped post upserted into `scraped_posts` (unique `post_url`).
- **Dedup:** unique `(subscriber_id, post_url)` on `notification_logs`.
- **Rate limit:** `SMS_MAX_PER_SUBSCRIBER_PER_CYCLE` across SMS+email sends.

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
# Mirror PROD account/catalog → DEV (skips notification_logs, sms_sendouts,
# engine_runs, scraped_posts)
python scripts/sync_prod_to_dev.py --dry-run
python scripts/sync_prod_to_dev.py --apply

# Replace facebook_groups catalog + assign all DEV subscribers to test groups
python scripts/seed_test_groups.py --apply
```

On the production droplet, the same sync runs automatically every day at **00:00 UTC** via user crontab (`scripts/vps-setup-sync-cron.sh`; see `deploy/DIGITALOCEAN_UPTIME.md`).

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
- Scale knobs: `RESULTS_PER_GROUP`, `SCRAPE_BATCH_SIZE`, `APIFY_MAX_RETRIES`,
  `SMS_MAX_PER_SUBSCRIBER_PER_CYCLE`, `SMS_SEND_DELAY_MS`.
- For real 24/7 reliability, run under systemd on the VPS (see `deploy/`).
