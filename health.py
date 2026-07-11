"""Minimal HTTP health endpoint for external uptime monitors (e.g. DigitalOcean).

The engine writes a heartbeat file after each successful cycle. A separate
`ugetfirst-health.service` (or the embedded server) reads it and exposes
GET /health on HEALTH_PORT (default 8080).

POST /admin/restart restarts ugetfirst-engine (token required). Install
deploy/sudoers-ugetfirst-engine on the VPS for passwordless restart.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import config

log = logging.getLogger("ugetfirst.health")

HEARTBEAT_PATH = Path(__file__).resolve().parent / ".engine-heartbeat"


def _write_heartbeat(at: datetime) -> None:
    HEARTBEAT_PATH.write_text(at.isoformat(), encoding="utf-8")


def mark_engine_started() -> None:
    """Call when the 24/7 loop begins (before the first cycle completes)."""
    _write_heartbeat(datetime.now(timezone.utc))


def mark_cycle_success() -> None:
    """Call after each engine cycle finishes without raising."""
    _write_heartbeat(datetime.now(timezone.utc))


def _last_success_at() -> datetime | None:
    try:
        raw = HEARTBEAT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    return datetime.fromisoformat(raw)


def _seconds_since_success() -> float | None:
    last = _last_success_at()
    if last is None:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds()


def is_healthy() -> bool:
    """Healthy if a cycle succeeded within 2.5× MIN_INTERVAL_SECONDS."""
    stale_after = config.MIN_INTERVAL_SECONDS * 2.5
    since = _seconds_since_success()
    if since is None:
        return False
    return since <= stale_after


def _admin_token() -> str:
    return (
        os.getenv("ENGINE_ADMIN_TOKEN", "").strip()
        or os.getenv("HEALTH_TOKEN", "").strip()
    )


def _authorized(path: str, headers: dict[str, str]) -> bool:
    token = _admin_token()
    if not token:
        return True
    parsed = urlparse(path)
    query = parse_qs(parsed.query)
    if token in query.get("token", []):
        return True
    auth = headers.get("Authorization", "")
    if auth == f"Bearer {token}":
        return True
    if headers.get("X-Health-Token") == token:
        return True
    if headers.get("X-Engine-Admin-Token") == token:
        return True
    return False


def health_payload() -> dict[str, object]:
    """Serializable health snapshot for /health and admin dashboards."""
    healthy = is_healthy()
    since = _seconds_since_success()
    last = _last_success_at()
    return {
        "status": "ok" if healthy else "stale",
        "healthy": healthy,
        "env": config.ENV,
        "last_success_at": last.isoformat() if last else None,
        "last_success_seconds_ago": round(since, 1) if since is not None else None,
        "stale_after_seconds": config.MIN_INTERVAL_SECONDS * 2.5,
    }


def restart_engine_service() -> tuple[bool, str]:
    """Restart the scraper systemd unit (requires passwordless sudo on VPS)."""
    try:
        proc = subprocess.run(
            ["sudo", "systemctl", "restart", "ugetfirst-engine"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "restart timed out after 30s"
    except OSError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "systemctl failed").strip()
        return False, detail[:500]
    return True, "ugetfirst-engine restarted"


class _HealthHandler(BaseHTTPRequestHandler):
    def _header_map(self) -> dict[str, str]:
        return {k: v for k, v in self.headers.items()}

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not _authorized(self.path, self._header_map()):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized\n")
            return

        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found\n")
            return

        payload = health_payload()
        query = parse_qs(parsed.query)
        wants_json = (
            "json" in query.get("format", [])
            or "application/json" in self.headers.get("Accept", "")
        )
        if wants_json:
            status = 200 if payload["healthy"] else 503
            self._send_json(status, payload)
            return

        healthy = bool(payload["healthy"])
        since = payload["last_success_seconds_ago"]
        body = (
            f"status={payload['status']}\n"
            f"env={payload['env']}\n"
            f"last_success_seconds_ago={since if since is not None else 'pending'}\n"
        ).encode()
        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not _authorized(self.path, self._header_map()):
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        route = urlparse(self.path).path.rstrip("/") or "/"
        if route != "/admin/restart":
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        ok, message = restart_engine_service()
        if ok:
            log.info("Admin restart: %s", message)
            self._send_json(200, {"ok": True, "message": message})
            return
        log.warning("Admin restart failed: %s", message)
        self._send_json(500, {"ok": False, "error": message})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def run_health_server() -> None:
    """Block forever serving /health (for ugetfirst-health.service)."""
    port = int(os.getenv("HEALTH_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    log.info("Health server listening on 0.0.0.0:%d (/health)", port)
    server.serve_forever()
