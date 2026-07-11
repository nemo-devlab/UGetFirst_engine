#!/usr/bin/env python3
"""Standalone health HTTP server — run via ugetfirst-health.service."""
from __future__ import annotations

import logging

from health import run_health_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

if __name__ == "__main__":
    run_health_server()
