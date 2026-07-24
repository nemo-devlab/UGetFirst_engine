# Environments: DEV vs PROD

`main` is PROD and `dev` is DEV. GitHub Actions deploy each branch to its
dedicated DigitalOcean droplet.

## Engine-specific

| Setting | DEV droplet | Production droplet |
|---|---|---|
| `ENV` | `dev` | `prod` |
| Git branch | `dev` | `main` |
| Supabase | `DEV_SUPABASE_*` | `PROD_SUPABASE_*` (+ DEV credentials for sync) |
| SMS/email | Providers enabled only for `QA_TEST_*`; all others use `outbox/` | Normal live providers |
| Health | optional | `HEALTH_TOKEN`, `ENGINE_ADMIN_TOKEN` |
| PROD→DEV sync | Receives mirror | Sunday 00:00 UTC weekly timer |

Never point a local experiment at `ENV=prod` unless you intend to affect real users.

## Instant DEV notification test

The DEV engine fails closed: only `QA_TEST_EMAIL` and `QA_TEST_PHONE` can call
Resend/Twilio. Synced customer destinations are written to `outbox/`.

Run the real matcher, dedup, tier gate, notifier, and sendout logger without
waiting for Apify:

```bash
python scripts/dev_notify_test.py --channel eligible
```

Use `--channel email`, `--channel sms`, or `--channel both` to require a
specific eligible channel. The selected DEV subscriber must use the allowlisted
contact and have the required tier/consent.

## Weekly PROD → DEV sync

The weekly job fully mirrors Auth, account, catalog, verification, feedback,
and QA data. `engine_runs`, `notification_logs`, `sms_sendouts`, and
`scraped_posts` keep only the latest seven days.

```bash
python scripts/sync_prod_to_dev.py --dry-run
python scripts/sync_prod_to_dev.py --apply
```

Install either `deploy/ugetfirst-sync-prod-to-dev.timer` or the cron fallback
in `scripts/vps-setup-sync-cron.sh` on the PROD droplet, not both.
