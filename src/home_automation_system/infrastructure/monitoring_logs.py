"""Bounded in-memory terminal-style server and device logs for the admin UI."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Final

SERVER_LOG_LIMIT: Final[int] = 5000
DEVICE_LOG_LIMIT: Final[int] = 5000
LOG_FORMAT: Final[str] = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
LOG_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"


class _LineBuffer(logging.Handler):
    """Capture formatted log records in a thread-safe bounded buffer."""

    def __init__(
        self, lines: deque[str], logger_prefixes: tuple[str, ...] = ()
    ) -> None:
        super().__init__(level=logging.INFO)
        self._lines: deque[str] = lines
        self._lines_lock: threading.Lock = threading.Lock()
        self._logger_prefixes: tuple[str, ...] = logger_prefixes
        self.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))

    def filter(self, record: logging.LogRecord) -> bool:
        return not self._logger_prefixes or any(
            record.name == prefix or record.name.startswith(f"{prefix}.")
            for prefix in self._logger_prefixes
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line: str = self.format(record)
            with self._lines_lock:
                self._lines.append(line)
        except Exception:
            self.handleError(record)

    def snapshot(self, limit: int) -> list[str]:
        with self._lines_lock:
            return list(self._lines)[-max(1, min(limit, 500)):]


SERVER_LINES: deque[str] = deque(maxlen=SERVER_LOG_LIMIT)
DEVICE_LINES: deque[str] = deque(maxlen=DEVICE_LOG_LIMIT)
_SERVER_BUFFER_HANDLER: _LineBuffer = _LineBuffer(
    SERVER_LINES, ("home_automation_system", "uvicorn")
)
_DEVICE_BUFFER_HANDLER: _LineBuffer = _LineBuffer(
    DEVICE_LINES, ("home_automation_system.device",)
)
_CONFIG_LOCK: threading.Lock = threading.Lock()


def configure_monitor_logging() -> None:
    """Attach timestamped monitor buffers and timestamped terminal output."""
    with _CONFIG_LOCK:
        root: logging.Logger = logging.getLogger()
        root.setLevel(logging.INFO)
        if not any(handler is _SERVER_BUFFER_HANDLER for handler in root.handlers):
            root.addHandler(_SERVER_BUFFER_HANDLER)
        if not any(
            isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler)
            for handler in root.handlers
        ):
            console: logging.StreamHandler = logging.StreamHandler()
            console.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
            root.addHandler(console)
        access_logger: logging.Logger = logging.getLogger("uvicorn.access")
        access_logger.setLevel(logging.INFO)
        access_logger.propagate = False
        if not any(handler is _SERVER_BUFFER_HANDLER for handler in access_logger.handlers):
            access_logger.addHandler(_SERVER_BUFFER_HANDLER)
        if not any(
            isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler)
            for handler in access_logger.handlers
        ):
            access_console: logging.StreamHandler = logging.StreamHandler()
            access_console.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
            access_logger.addHandler(access_console)
        device_logger: logging.Logger = logging.getLogger("home_automation_system.device")
        device_logger.setLevel(logging.INFO)
        device_logger.propagate = True
        if not any(handler is _DEVICE_BUFFER_HANDLER for handler in device_logger.handlers):
            device_logger.addHandler(_DEVICE_BUFFER_HANDLER)


def append_device_log(message: str, *, level: int = logging.INFO) -> None:
    """Write one already-sanitized device event to console and device log view."""
    configure_monitor_logging()
    logging.getLogger("home_automation_system.device").log(level, message)


def read_monitor_lines(source: str, limit: int = 500) -> list[str]:
    """Return the newest bounded log lines for one allowed source."""
    configure_monitor_logging()
    bounded_limit: int = max(1, min(limit, 500))
    if source == "server":
        return _SERVER_BUFFER_HANDLER.snapshot(bounded_limit)
    if source == "device":
        return _DEVICE_BUFFER_HANDLER.snapshot(bounded_limit)
    raise ValueError("source must be 'server' or 'device'")
