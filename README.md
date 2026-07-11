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
  5. if newly inserted AND notify_sms: send SMS + log sms_sendouts (notifier.py)
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
MIN_INTERVAL_SECONDS=120
RESULTS_LIMIT=20
# Time window for scraping, e.g. "10 minutes", "6 hours", "1 day". Empty = off.
LOOKBACK=30 minutes
```

Required for the active `ENV`: that project's URL + service-role key, plus
`APIFY_TOKEN`.

**SMS is simulated for now.** The notifier writes one `.txt` file per message into
`outbox/` and inserts a row into `sms_sendouts` (full message body for the admin
Sendouts page). Apply `../UGetFirst_web/supabase/migrations/012_sms_sendouts.sql`
on **both** Supabase projects before running the engine.

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

- **Confirm Apify output fields.** `scraper.py` normalizes post URL/text/group
  from a list of candidate field names; run `--once --dump-raw-keys` against a
  real public group to verify and trim the candidates.
- **Wire a real SMS provider** (Text Request / Twilio) into `notifier.py` once
  10DLC registration clears. Until then messages are written to `outbox/`. We
  already respect `notify_sms = false`.
- Many waitlist users have **no group URL** yet — those subscribers simply won't
  match anything until a group is added.
- For real 24/7 reliability, move off the local machine to a small VPS running
  `python main.py` under systemd/Docker with auto-restart.
