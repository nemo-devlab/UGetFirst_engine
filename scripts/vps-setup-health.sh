#!/usr/bin/env bash
# One-time (or after deploy) setup for DO HTTP uptime checks on the VPS.
# Run on the droplet: bash scripts/vps-setup-health.sh
set -euo pipefail

REPO="${REPO:-$HOME/UGetFirst_engine}"
cd "$REPO"

echo "==> git pull"
git pull

echo "==> install health + engine systemd units"
sudo cp deploy/ugetfirst-health.service /etc/systemd/system/
sudo cp deploy/ugetfirst-engine.service /etc/systemd/system/
sudo cp deploy/sudoers-ugetfirst-engine /etc/sudoers.d/ugetfirst-engine
sudo chmod 440 /etc/sudoers.d/ugetfirst-engine
sudo visudo -cf /etc/sudoers.d/ugetfirst-engine
sudo systemctl daemon-reload
sudo systemctl enable ugetfirst-health ugetfirst-engine
sudo systemctl restart ugetfirst-health
sudo systemctl restart ugetfirst-engine

echo "==> open port 8080 (ufw)"
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow OpenSSH || true
  sudo ufw allow 8080/tcp || true
  sudo ufw --force enable || true
  sudo ufw status || true
fi

echo "==> local health check"
sleep 2
curl -sf "http://127.0.0.1:8080/health" || {
  echo "WARN: /health not 200 yet — engine may still be in first cycle"
  curl -i "http://127.0.0.1:8080/health" || true
}

echo ""
echo "Done. Point DO uptime at: http://$(curl -sf ifconfig.me 2>/dev/null || echo YOUR_DROPLET_IP):8080/health"
echo "Also open TCP 8080 in DigitalOcean → Networking → Firewalls if you use a cloud firewall."
