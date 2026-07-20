"""Minimal HTTP health endpoint for external uptime monitors (e.g. DigitalOcean).

The engine writes a heartbeat file after each successful cycle. A separate
`ugetfirst-health.service` (or the embedded server) reads it and exposes
GET /health on HEALTH_PORT (default 8080).

POST /admin/start|stop|restart control ugetfirst-engine (token required).
POST /admin/env {"env":"prod"|"dev"} updates .env ENV and restarts if running.
Install deploy/sudoers-ugetfirst-engine on the VPS for passwordless systemctl.
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
ENV_FILE_PATH = Path(__file__).resolve().parent / ".env"
ENGINE_UNIT = "ugetfirst-engine"


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


def _read_env_file_value(key: str) -> str | None:
    """Read KEY=value from the engine .env without importing config (hot-readable)."""
    try:
        lines = ENV_FILE_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    prefix = f"{key}="
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip().strip('"').strip("'")
    return None


def read_configured_env() -> str:
    """Current engine DB target from .env (falls back to imported config.ENV)."""
    raw = (_read_env_file_value("ENV") or config.ENV or "dev").strip().lower()
    return raw if raw in ("dev", "prod") else "dev"


def set_configured_env(target: str) -> tuple[bool, str]:
    """Update ENV= in .env. Returns (ok, message). Does not restart services."""
    target = target.strip().lower()
    if target not in ("dev", "prod"):
        return False, "env must be 'dev' or 'prod'"
    if not ENV_FILE_PATH.is_file():
        return False, f".env not found at {ENV_FILE_PATH}"

    try:
        original = ENV_FILE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return False, str(exc)

    lines = original.splitlines(keepends=True)
    found = False
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("ENV="):
            # Preserve indentation / newline style
            nl = "\n" if line.endswith("\n") else ""
            if line.endswith("\r\n"):
                nl = "\r\n"
            out.append(f"ENV={target}{nl}")
            found = True
        else:
            out.append(line)
    if not found:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append(f"ENV={target}\n")

    try:
        ENV_FILE_PATH.write_text("".join(out), encoding="utf-8")
    except OSError as exc:
        return False, str(exc)

    return True, f"ENV set to {target}"


def switch_engine_env(target: str) -> tuple[bool, str]:
    """Write ENV to .env and restart the engine if it was running."""
    was_active = _engine_active()
    ok, message = set_configured_env(target)
    if not ok:
        return False, message
    if was_active:
        restarted, detail = restart_engine_service()
        if not restarted:
            return False, f"{message}, but restart failed: {detail}"
        return True, f"{message}; engine restarted on {target}"
    return True, f"{message} (engine stopped — will use {target} on next start)"


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


def _engine_active() -> bool:
    """True when ugetfirst-engine systemd unit is active (no sudo needed)."""
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", "--quiet", ENGINE_UNIT],
            timeout=5,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def health_payload() -> dict[str, object]:
    """Serializable health snapshot for /health and admin dashboards."""
    engine_active = _engine_active()
    cycle_ok = is_healthy()
    since = _seconds_since_success()
    last = _last_success_at()
    if not engine_active:
        status = "stopped"
    elif cycle_ok:
        status = "ok"
    else:
        status = "stale"
    return {
        "status": status,
        # Healthy for uptime monitors = service running and recent successful cycle
        "healthy": engine_active and cycle_ok,
        "engine_active": engine_active,
        "cycle_ok": cycle_ok,
        "env": read_configured_env(),
        "min_interval_seconds": config.MIN_INTERVAL_SECONDS,
        "last_success_at": last.isoformat() if last else None,
        "last_success_seconds_ago": round(since, 1) if since is not None else None,
        "stale_after_seconds": config.MIN_INTERVAL_SECONDS * 2.5,
    }


def _systemctl_engine(action: str) -> tuple[bool, str]:
    """Run systemctl <action> on ugetfirst-engine (requires passwordless sudo)."""
    if action not in ("start", "stop", "restart"):
        return False, f"unsupported action: {action}"
    try:
        proc = subprocess.run(
            ["sudo", "systemctl", action, ENGINE_UNIT],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"{action} timed out after 30s"
    except OSError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "systemctl failed").strip()
        return False, detail[:500]
    return True, f"{ENGINE_UNIT} {action}ed"


def restart_engine_service() -> tuple[bool, str]:
    return _systemctl_engine("restart")


def start_engine_service() -> tuple[bool, str]:
    return _systemctl_engine("start")


def stop_engine_service() -> tuple[bool, str]:
    return _systemctl_engine("stop")


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

        if route == "/admin/env":
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "invalid JSON"})
                return
            target = str(body.get("env", "")).strip().lower()
            ok, message = switch_engine_env(target)
            if ok:
                log.info("Admin env switch: %s", message)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "message": message,
                        "env": read_configured_env(),
                        "engine_active": _engine_active(),
                    },
                )
                return
            log.warning("Admin env switch failed: %s", message)
            self._send_json(500, {"ok": False, "error": message})
            return

        actions = {
            "/admin/restart": ("restart", restart_engine_service),
            "/admin/start": ("start", start_engine_service),
            "/admin/stop": ("stop", stop_engine_service),
        }
        entry = actions.get(route)
        if entry is None:
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        label, fn = entry
        ok, message = fn()
        if ok:
            log.info("Admin %s: %s", label, message)
            self._send_json(200, {"ok": True, "message": message})
            return
        log.warning("Admin %s failed: %s", label, message)
        self._send_json(500, {"ok": False, "error": message})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def run_health_server() -> None:
    """Block forever serving /health (for ugetfirst-health.service)."""
    port = int(os.getenv("HEALTH_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    log.info("Health server listening on 0.0.0.0:%d (/health)", port)
    server.serve_forever()
