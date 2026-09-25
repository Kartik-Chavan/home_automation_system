"""SQLite persistence for resumable agent conversations."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SessionStore:
    """Persist provider-compatible messages using only sqlite3."""

    def __init__(self, database_path: Path) -> None:
        self._database_path: Path = database_path
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def load_messages(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        """Load messages in their original conversation order."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content_json, tool_calls_json, tool_plan, tool_call_id
                FROM messages
                WHERE user_id = ? AND session_id = ?
                ORDER BY sequence_id
                """,
                (user_id, session_id),
            ).fetchall()
        messages: list[dict[str, Any]] = []
        for role, content_json, tool_calls_json, tool_plan, tool_call_id in rows:
            message: dict[str, Any] = {"role": role, "content": json.loads(content_json)}
            if tool_calls_json:
                message["tool_calls"] = json.loads(tool_calls_json)
            if tool_plan is not None:
                message["tool_plan"] = tool_plan
            if tool_call_id is not None:
                message["tool_call_id"] = tool_call_id
            messages.append(message)
        return messages

    def append_message(
        self,
        user_id: str,
        session_id: str,
        message: dict[str, Any],
        request_id: str,
    ) -> None:
        """Append one provider message and its trace metadata atomically."""
        content: object = message.get("content", "")
        tool_calls: object = message.get("tool_calls")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (
                    user_id, session_id, role, content_json, tool_calls_json,
                    tool_plan, tool_call_id, request_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    str(message["role"]),
                    json.dumps(content, default=str),
                    json.dumps(tool_calls, default=str) if tool_calls is not None else None,
                    message.get("tool_plan"),
                    message.get("tool_call_id"),
                    request_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with foreign-key enforcement enabled."""
        connection: sqlite3.Connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        """Create the append-only message table if needed."""
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    tool_calls_json TEXT,
                    tool_plan TEXT,
                    tool_call_id TEXT,
                    request_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_messages_session
                ON messages (user_id, session_id, sequence_id)
                """
            )
