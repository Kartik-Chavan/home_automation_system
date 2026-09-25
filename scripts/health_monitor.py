"""Continuously probe the private API and persist health evidence."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def build_logger(log_directory: Path) -> logging.Logger:
    """Create the rotating health-monitor JSONL logger."""
    directory: Path = log_directory / "health"
    directory.mkdir(parents=True, exist_ok=True)
    logger: logging.Logger = logging.getLogger("home_automation_system.health_monitor")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(
            directory / "checks.jsonl",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


def probe(base_url: str, timeout_seconds: float) -> dict[str, Any]:
    """Run non-mutating process and device probes against the API."""
    checks: dict[str, Any] = {}
    for name, path in (
        ("process", "/api/health"),
        ("device_info", "/api/health/device"),
        ("device_status", "/api/device/status"),
    ):
        started: float = time.monotonic()
        request: Request = Request(f"{base_url.rstrip('/')}{path}", method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body: str = response.read().decode("utf-8")
                checks[name] = {
                    "ok": 200 <= response.status < 300,
                    "status": response.status,
                    "latency_ms": round((time.monotonic() - started) * 1000, 1),
                    "body": json.loads(body),
                }
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
            checks[name] = {
                "ok": False,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "error_type": type(error).__name__,
                "error": str(error),
            }
    return checks


def main() -> None:
    """Run probes once or until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run one check and exit")
    parser.add_argument("--interval", type=float, default=float(os.getenv("HEALTH_INTERVAL", "60")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("HEALTH_TIMEOUT", "15")))
    parser.add_argument("--base-url", default=os.getenv("HOME_AUTOMATION_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--log-dir", default=os.getenv("HOME_AUTOMATION_LOG_DIR", "logs"))
    args: argparse.Namespace = parser.parse_args()
    logger: logging.Logger = build_logger(Path(args.log_dir))
    while True:
        checks: dict[str, Any] = probe(args.base_url, args.timeout)
        logger.info(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url,
            "checks": checks,
            "healthy": all(bool(check.get("ok")) for check in checks.values()),
        }, sort_keys=True, default=str))
        if args.once:
            return
        time.sleep(max(args.interval, 1.0))


if __name__ == "__main__":
    main()
