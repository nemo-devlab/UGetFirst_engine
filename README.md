# UGetFirst Engine

Scraping + notification worker for **UGetFirst** — *"First to Know. First to Win."*
Polls public Facebook groups for user keywords and sends an SMS the moment a
matching post appears.

This repo shares one Supabase database with the website repo. The database is
the contract between them.

## How it works

```
loop (every MIN_INTERVAL_SECONDS, non-overlapping):
  1. load monitored groups + subscribers + keywords   (db.py)
  2. scrape all distinct group URLs in ONE Apify run   (scraper.py)
  3. for each post, case-insensitively match keywords  (matcher.py)
  4. INSERT notification_logs (unique subscriber_id, post_url)  -> idempotency
  5. if newly inserted AND notify_sms: send SMS         (notifier.py)
```

- **Dedup / idempotency:** the unique `(subscriber_id, post_url)` constraint on
  `notification_logs` guarantees a subscriber is never texted twice for the same
  post. We only send when the insert is new.
- **New posts:** each cycle we fetch posts within the `LOOKBACK` time window
  (`onlyPostsNewerThan`, newest-first, capped at `RESULTS_LIMIT`). Dedup above
  ensures we only text on posts we haven't seen before. Stateless — no
  high-water-mark table needed.

## Environments (dev vs prod)

Controlled by a single env var `ENV`:

| ENV    | Postgres schema | Data                     |
| ------ | --------------- | ------------------------ |
| `dev`  | `dev`           | test rows (safe)         |
| `prod` | `public`        | real subscribers         |

The `dev` schema mirrors `public` exactly (same tables, indexes, RLS). Switching
environments changes nothing but the schema the client points at.

> **One-time setup for `dev`:** add `dev` to Supabase → Settings → API →
> **Exposed schemas** so the client can query it via PostgREST.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the repo root with these variables:

```
ENV=dev
NEXT_PUBLIC_SUPABASE_URL=https://vtumfvzvnrfwfkkwskhk.supabase.co
SUPABASE_SERVICE_ROLE_KEY=
APIFY_TOKEN=
APIFY_ACTOR_ID=apify/facebook-groups-scraper
MIN_INTERVAL_SECONDS=120
RESULTS_LIMIT=20
# Time window for scraping, e.g. "10 minutes", "6 hours", "1 day". Empty = off.
LOOKBACK=1 day
```

Required: `SUPABASE_SERVICE_ROLE_KEY`, `APIFY_TOKEN`.

**SMS is simulated for now.** Instead of sending a text, the notifier writes one
`.txt` file per message into `outbox/` (git-ignored). A real SMS provider will be
wired in once 10DLC registration clears.

## Run

```bash
# one cycle, and log raw Apify item keys (use once to confirm output fields)
python main.py --once --dump-raw-keys

# one cycle
python main.py --once

# continuous 24/7 loop
python main.py
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
