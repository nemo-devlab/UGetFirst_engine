#!/usr/bin/env bash
# Restart the UGetFirst engine and health services on the droplet.
#
# Usage (on the VPS):
#   bash deploy/restart.sh
#
# Requires sudo (you'll be prompted for your password).
set -euo pipefail

REPO="${REPO:-$HOME/UGetFirst_engine}"
cd "$REPO"

HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/health}"
# Cold start (imports + bind + mark_engine_started) often exceeds a few seconds.
HEALTH_WAIT_SECONDS="${HEALTH_WAIT_SECONDS:-90}"

echo "==> restarting ugetfirst-engine and ugetfirst-health"
sudo systemctl restart ugetfirst-health
sudo systemctl restart ugetfirst-engine

echo
echo "==> service status"
systemctl status ugetfirst-health --no-pager -l || true
echo
systemctl status ugetfirst-engine --no-pager -l || true

echo
echo "==> health check (up to ${HEALTH_WAIT_SECONDS}s)"
deadline=$((SECONDS + HEALTH_WAIT_SECONDS))
while (( SECONDS < deadline )); do
  if curl -sf "$HEALTH_URL" >/dev/null; then
    echo
    echo "OK"
    exit 0
  fi
  sleep 2
done

echo
echo "ERROR: /health did not return 200 within ${HEALTH_WAIT_SECONDS}s"
echo
echo "==> last /health response"
curl -si "$HEALTH_URL" || true
echo
echo "==> recent health journal"
journalctl -u ugetfirst-health -n 40 --no-pager || true
echo
echo "==> recent engine journal"
journalctl -u ugetfirst-engine -n 40 --no-pager || true
exit 1
