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

echo "==> restarting ugetfirst-engine and ugetfirst-health"
sudo systemctl restart ugetfirst-health
sudo systemctl restart ugetfirst-engine

echo
echo "==> service status"
systemctl status ugetfirst-health --no-pager -l || true
echo
systemctl status ugetfirst-engine --no-pager -l || true

echo
echo "==> health check (waiting 3s)"
sleep 3
if curl -sf "http://127.0.0.1:8080/health"; then
  echo
  echo "OK"
else
  echo
  echo "WARN: /health did not return 200 yet (engine may still be starting)"
  exit 1
fi
