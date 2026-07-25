#!/usr/bin/env bash
# Install (or refresh) the weekly PROD→DEV sync cron for the ugetfirst user.
# Prefer this when passwordless sudo/systemd is unavailable.
#
# Usage (on the VPS):
#   bash scripts/vps-setup-sync-cron.sh
set -euo pipefail

REPO="${REPO:-$HOME/UGetFirst_engine}"
LOG_DIR="$REPO/logs"
PY="$REPO/.venv/bin/python"
SCRIPT="$REPO/scripts/sync_prod_to_dev.py"

mkdir -p "$LOG_DIR"

if [[ ! -x "$PY" ]]; then
  echo "ERROR: missing venv python at $PY" >&2
  exit 1
fi
if [[ ! -f "$SCRIPT" ]]; then
  echo "ERROR: missing $SCRIPT" >&2
  exit 1
fi

MARKER="sync_prod_to_dev.py"
LINE="0 0 * * 0 cd $REPO && $PY $SCRIPT --apply >> $LOG_DIR/sync_prod_to_dev.log 2>&1"

(crontab -l 2>/dev/null | grep -v "$MARKER" || true
 echo "# Weekly PROD → DEV Supabase overwrite, Sunday 00:00 UTC"
 echo "$LINE"
) | crontab -

echo "==> installed crontab:"
crontab -l
echo
echo "Logs: $LOG_DIR/sync_prod_to_dev.log"
echo "Dry-run now: cd $REPO && $PY $SCRIPT --dry-run"
