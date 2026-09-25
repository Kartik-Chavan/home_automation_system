"""Durable, structured audit events for every application operation."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from uuid import uuid4


class AuditLogger:
    """Write append-only JSONL audit events with bounded log rotation."""

    def __init__(self, log_directory: Path | None = None) -> None:
        configured_directory: str = os.getenv("HOME_AUTOMATION_LOG_DIR", "logs")
        self._log_directory: Path = log_directory or Path(configured_directory)
        self._logger: logging.Logger = self._build_logger()

    def event(
        self,
        operation: str,
        *,
        request_id: str,
        actor: str,
        source: str,
        outcome: str,
        details: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Persist one sanitized operation event."""
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_id": str(uuid4()),
            "request_id": request_id,
            "actor": actor,
            "source": source,
            "operation": operation,
            "outcome": outcome,
        }
        if details:
            payload["details"] = details
        if error:
            payload["error_type"] = type(error).__name__
            payload["error"] = str(error)
        self._logger.info(json.dumps(payload, default=str, sort_keys=True))

    def _build_logger(self) -> logging.Logger:
        """Create the audit logger once per process without changing root logging."""
        directory: Path = self._log_directory / "audit"
        directory.mkdir(parents=True, exist_ok=True)
        logger: logging.Logger = logging.getLogger(
            f"home_automation_system.audit.{directory.resolve()}"
        )
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            handler = RotatingFileHandler(
                directory / "events.jsonl",
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        return logger
