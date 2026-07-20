# Environments: DEV vs PROD

See the shared guide:

[UGetFirst_web/docs/ENVIRONMENTS.md](../UGetFirst_web/docs/ENVIRONMENTS.md)

## Engine-specific

| Setting | Local / DEV | Production droplet |
|---|---|---|
| `ENV` | `dev` | `prod` |
| Supabase | `DEV_SUPABASE_*` | `PROD_SUPABASE_*` (+ `DEV_SUPABASE_*` for sync timer) |
| `SMS_MODE` | `simulated` (default) | `twilio` after A2P |
| Health | optional | `HEALTH_TOKEN`, `ENGINE_ADMIN_TOKEN` |
| PROD→DEV sync | manual script | user crontab @ 00:00 UTC (`vps-setup-sync-cron.sh`) |

Never point a local experiment at `ENV=prod` unless you intend to affect real users.
