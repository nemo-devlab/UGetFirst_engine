"""Health endpoint auth: public GET /health, gated admin POSTs."""

from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import health


class HealthAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), health._HealthHandler)
        self._port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._port}{path}"

    def test_get_health_is_public_when_admin_token_set(self) -> None:
        payload = {
            "status": "ok",
            "healthy": True,
            "engine_active": True,
            "cycle_ok": True,
            "env": "dev",
            "min_interval_seconds": 600,
            "last_success_at": datetime.now(timezone.utc).isoformat(),
            "last_success_seconds_ago": 1.0,
            "stale_after_seconds": 1500.0,
        }
        with patch.object(health, "_admin_token", return_value="secret-token"):
            with patch.object(health, "health_payload", return_value=payload):
                with urlopen(self._url("/health"), timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    body = resp.read().decode()
        self.assertIn("status=ok", body)

    def test_admin_post_still_requires_token(self) -> None:
        with patch.object(health, "_admin_token", return_value="secret-token"):
            req = Request(
                self._url("/admin/restart"),
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req, timeout=5)
            self.assertEqual(ctx.exception.code, 401)
            detail = json.loads(ctx.exception.read().decode())
            self.assertFalse(detail.get("ok", True))


if __name__ == "__main__":
    unittest.main()
