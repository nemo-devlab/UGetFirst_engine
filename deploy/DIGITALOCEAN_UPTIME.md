# DigitalOcean uptime monitoring

Use **HTTP** (not PING) against `/health`. PING only confirms the droplet is up.

## Architecture

| Service | Role |
|---|---|
| `ugetfirst-engine.service` | Scraper loop; writes `.engine-heartbeat` after each successful cycle |
| `ugetfirst-health.service` | Always listens on `:8080`, serves `GET /health` |

Splitting health into its own service means port 8080 stays up across engine deploys/restarts.

## One-time VPS setup

SSH into the droplet and run:

```bash
cd ~/UGetFirst_engine
git pull
bash scripts/vps-setup-health.sh
```

That script installs both systemd units, opens UFW port 8080, and restarts services.

**Also check DigitalOcean cloud firewall:** Networking → Firewalls → allow **Inbound TCP 8080** to the droplet (UFW alone is not enough if a DO firewall is attached).

## DO uptime check

1. **Monitoring → Uptime → Create**
2. **Type:** HTTP
3. **URL:** `http://157.230.234.67:8080/health`
4. **Expected status:** 200
5. **Interval:** 1 minute

Optional `.env`:

```env
HEALTH_PORT=8080
HEALTH_TOKEN=long-random-string   # append ?token=... to monitor URL
```

## Verify

```bash
sudo systemctl status ugetfirst-health ugetfirst-engine
curl -i http://127.0.0.1:8080/health
curl -i http://157.230.234.67:8080/health   # from your laptop
```

| Response | Meaning |
|---|---|
| `200 status=ok` | Engine completed a cycle recently |
| `503 status=stale` | Engine stuck or not running |
| Connection refused | `ugetfirst-health` down or firewall blocking 8080 |

## Troubleshooting

**Alert firing but engine runs:** the running `ugetfirst-engine` process was started before a deploy — restart both services:

```bash
sudo systemctl restart ugetfirst-engine ugetfirst-health
```

**Works locally on VPS (`curl 127.0.0.1`) but not externally:** open **DO cloud firewall** port 8080 and/or `sudo ufw allow 8080/tcp`.
