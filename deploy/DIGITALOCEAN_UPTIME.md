# DigitalOcean uptime monitoring

The engine exposes a plain HTTP health endpoint for external monitors. **Do not use
PING alone** — it only confirms the droplet is reachable, not that the scraper
loop is running.

## Health endpoint

When `python main.py` runs under systemd (not `--once`), it listens on:

```
http://<droplet-ip>:8080/health
```

| Response | Meaning |
|---|---|
| `200` + `status=ok` | Last cycle finished within ~5 minutes (2.5× 120s interval) |
| `503` + `status=stale` | Engine stuck, crashed, or cycles failing repeatedly |
| `401` | Wrong/missing token (only if `HEALTH_TOKEN` is set) |

Optional env vars in `.env`:

```env
HEALTH_PORT=8080
HEALTH_TOKEN=choose-a-long-random-string
```

If `HEALTH_TOKEN` is set, append `?token=...` to the monitor URL or send header
`X-Health-Token: ...`.

## 1. Open the port on the droplet

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8080/tcp
sudo ufw enable
sudo ufw status
```

## 2. Create the uptime check in DigitalOcean

1. **Monitoring → Uptime → Create Uptime Check**
2. **Type:** HTTP (HTTPS optional — see below)
3. **URL:** `http://157.230.234.67:8080/health`
   - With token: `http://157.230.234.67:8080/health?token=YOUR_TOKEN`
4. **Regions:** pick 2–3 (e.g. NYC, SF, AMS)
5. **Check interval:** 1 minute
6. **Alert policy:** email/SMS/Slack when check fails
7. **Advanced (if available):**
   - Expected status code: **200**
   - Treat 503 as down (default for failed HTTP checks)

## PING vs HTTP

| Check | Detects |
|---|---|
| **PING** | Droplet powered on / network reachable |
| **HTTP /health** | Engine loop completing cycles on schedule |

Use **HTTP** for the engine. Keep PING only if you also want bare-metal alerts.

## HTTPS (optional)

DO uptime supports HTTPS. Options:

- Point a subdomain (e.g. `engine-health.yourdomain.com`) at the droplet, put
  **Caddy/nginx** in front, terminate TLS, proxy to `127.0.0.1:8080`.
- Or stay on **HTTP** for an internal ops URL — acceptable for a non-public
  health port with `HEALTH_TOKEN` set.

## Verify manually

```bash
curl -i http://127.0.0.1:8080/health
curl -i "http://127.0.0.1:8080/health?token=YOUR_TOKEN"   # if HEALTH_TOKEN set
```

After deploy + systemd restart, confirm `status=ok` and create the DO check.
