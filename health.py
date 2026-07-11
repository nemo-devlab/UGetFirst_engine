"""Minimal HTTP health endpoint for external uptime monitors (e.g. DigitalOcean).

Returns 200 while the engine loop is completing cycles on schedule.
Returns 503 when the last successful cycle is too old (likely stuck or crashed).
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config

log = logging.getLogger("ugetfirst.health")

_started_at = datetime.now(timezone.utc)
_last_success_at: datetime | None = None
_lock = threading.Lock()


def mark_cycle_success() -> None:
    """Call after each engine cycle finishes without raising."""
    global _last_success_at
    with _lock:
        _last_success_at = datetime.now(timezone.utc)


def _seconds_since_success() -> float | None:
    with _lock:
        if _last_success_at is None:
            return None
        return (datetime.now(timezone.utc) - _last_success_at).total_seconds()


def is_healthy() -> bool:
    """Healthy if a cycle succeeded within 2.5× MIN_INTERVAL_SECONDS."""
    stale_after = config.MIN_INTERVAL_SECONDS * 2.5
    since = _seconds_since_success()
    if since is None:
        # Grace period while the first cycle runs (Apify can take ~30s+).
        startup_grace = config.MIN_INTERVAL_SECONDS * 3
        age = (datetime.now(timezone.utc) - _started_at).total_seconds()
        return age <= startup_grace
    return since <= stale_after


def _authorized(path: str, headers: dict[str, str]) -> bool:
    token = os.getenv("HEALTH_TOKEN", "").strip()
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
    return False


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if not _authorized(self.path, {k: v for k, v in self.headers.items()}):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized\n")
            return

        route = urlparse(self.path).path.rstrip("/") or "/"
        if route not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found\n")
            return

        healthy = is_healthy()
        since = _seconds_since_success()
        body = (
            f"status={'ok' if healthy else 'stale'}\n"
            f"env={config.ENV}\n"
            f"last_success_seconds_ago={since if since is not None else 'pending'}\n"
        ).encode()
        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def start_health_server() -> None:
    port = int(os.getenv("HEALTH_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="health-http")
    thread.start()
    log.info("Health server listening on 0.0.0.0:%d (/health)", port)
