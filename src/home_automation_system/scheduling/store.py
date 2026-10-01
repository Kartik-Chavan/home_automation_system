"""SQLite source of truth for recurring schedules and one-shot timers."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_database_path() -> Path:
    """Return the same SQLite file used by persistent agent conversations."""
    return Path(os.getenv("AGENT_DATABASE_PATH", "logs/agent/sessions.db"))


class ScheduleStore:
    """Persist schedule and timer rows independently from scheduler memory."""

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path: Path = database_path or default_database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        """Apply additive, idempotent schema migrations."""
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    time TEXT NOT NULL,
                    days TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('on','off')),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS timers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('on','off')),
                    fire_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','fired','cancelled','missed')),
                    created_at TEXT NOT NULL,
                    claimed_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_schedules_enabled ON schedules(enabled)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_timers_status_fire_at ON timers(status, fire_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduler_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('schedule','timer')),
                    event_id INTEGER NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('on','off')),
                    repeat_days TEXT NOT NULL DEFAULT '',
                    scheduled_for TEXT NOT NULL,
                    triggered_at TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome IN ('succeeded','failed')),
                    reason TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_scheduler_events_device_id ON scheduler_events(device_id, id DESC)"
            )
            timer_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(timers)")
            }
            if "claimed_at" not in timer_columns:
                connection.execute("ALTER TABLE timers ADD COLUMN claimed_at TEXT")
            event_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(scheduler_events)")
            }
            if "repeat_days" not in event_columns:
                connection.execute(
                    "ALTER TABLE scheduler_events ADD COLUMN repeat_days TEXT NOT NULL DEFAULT ''"
                )

    def create_schedule(
        self, device_id: str, time: str, days: str, action: str
    ) -> dict[str, Any]:
        """Insert an enabled recurring rule before scheduler registration."""
        created_at: str = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_no_schedule_conflict(
                connection, device_id, time, days, action
            )
            cursor = connection.execute(
                """INSERT INTO schedules(device_id, time, days, action, enabled, created_at)
                   VALUES (?, ?, ?, ?, 1, ?)""",
                (device_id, time, days, action, created_at),
            )
            schedule_id: int = int(cursor.lastrowid)
        return self.get_schedule(schedule_id)  # type: ignore[return-value]

    def list_schedules(self, device_id: str) -> list[dict[str, Any]]:
        """List a device's schedule rules in creation order."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM schedules WHERE device_id=? ORDER BY id", (device_id,)
            ).fetchall()
        return [_schedule_row(row) for row in rows]

    def list_enabled_schedules(self) -> list[dict[str, Any]]:
        """Load enabled schedule rules for startup reconciliation."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM schedules WHERE enabled=1 ORDER BY id"
            ).fetchall()
        return [_schedule_row(row) for row in rows]

    def disable_conflicting_schedules(self) -> list[dict[str, Any]]:
        """Pause every enabled rule involved in a pre-existing action conflict."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM schedules WHERE enabled=1 ORDER BY id"
            ).fetchall()
            conflicts: dict[int, set[int]] = {}
            for index, current in enumerate(rows):
                for other in rows[index + 1:]:
                    if (
                        current["device_id"] == other["device_id"]
                        and current["time"] == other["time"]
                        and current["action"] != other["action"]
                        and _selected_days(current["days"]).intersection(
                            _selected_days(other["days"])
                        )
                    ):
                        conflicts.setdefault(current["id"], set()).add(other["id"])
                        conflicts.setdefault(other["id"], set()).add(current["id"])
            if not conflicts:
                return []
            ids = sorted(conflicts)
            placeholders = ",".join("?" for _ in ids)
            connection.execute(
                f"UPDATE schedules SET enabled=0 WHERE id IN ({placeholders})", ids
            )
            by_id = {row["id"]: dict(row) for row in rows}
            return [
                {
                    **by_id[schedule_id],
                    "enabled": False,
                    "conflicts_with": sorted(conflicts[schedule_id]),
                }
                for schedule_id in ids
            ]

    def get_schedule(self, schedule_id: int) -> dict[str, Any] | None:
        """Fetch one schedule, returning None if it does not exist."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
        return _schedule_row(row) if row else None

    def update_schedule(
        self,
        schedule_id: int,
        *,
        enabled: bool | None = None,
        time: str | None = None,
        days: str | None = None,
        action: str | None = None,
    ) -> dict[str, Any] | None:
        """Update supplied rule fields durably before scheduler synchronization."""
        existing: dict[str, Any] | None = self.get_schedule(schedule_id)
        if existing is None:
            return None
        values: dict[str, Any] = {
            "enabled": int(enabled) if enabled is not None else existing["enabled"],
            "time": time if time is not None else existing["time"],
            "days": days if days is not None else existing["days"],
            "action": action if action is not None else existing["action"],
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if values["enabled"]:
                self._assert_no_schedule_conflict(
                    connection,
                    existing["device_id"],
                    values["time"],
                    values["days"],
                    values["action"],
                    exclude_id=schedule_id,
                )
            connection.execute(
                "UPDATE schedules SET enabled=?, time=?, days=?, action=? WHERE id=?",
                (
                    values["enabled"], values["time"], values["days"],
                    values["action"], schedule_id,
                ),
            )
        return self.get_schedule(schedule_id)

    def delete_schedule(self, schedule_id: int) -> dict[str, Any] | None:
        """Delete a schedule and return its prior row for auditing."""
        row: dict[str, Any] | None = self.get_schedule(schedule_id)
        if row is not None:
            with self._connect() as connection:
                connection.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
        return row

    def create_timer(self, device_id: str, action: str, fire_at: str) -> dict[str, Any]:
        """Insert a pending one-shot timer before registering its date job."""
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO timers(device_id, action, fire_at, status, created_at)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (device_id, action, fire_at, _now()),
            )
            timer_id: int = int(cursor.lastrowid)
        return self.get_timer(timer_id)  # type: ignore[return-value]

    def get_timer(self, timer_id: int) -> dict[str, Any] | None:
        """Fetch one timer, returning None if it does not exist."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM timers WHERE id=?", (timer_id,)).fetchone()
        return _timer_row(row) if row else None

    def list_timers(self, device_id: str) -> list[dict[str, Any]]:
        """List active timers for a device."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM timers WHERE device_id=? AND status='pending'
                   AND claimed_at IS NULL ORDER BY fire_at, id""",
                (device_id,),
            ).fetchall()
        return [_timer_row(row) for row in rows]

    def list_pending_timers(self) -> list[dict[str, Any]]:
        """Load pending timers for startup reconciliation."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM timers WHERE status='pending' ORDER BY fire_at, id"
            ).fetchall()
        return [_timer_row(row) for row in rows]

    def claim_timer(self, timer_id: int) -> dict[str, Any] | None:
        """Claim one pending timer so cancellation and execution cannot both win."""
        claimed_at: str = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM timers WHERE id=? AND status='pending'
                   AND claimed_at IS NULL""",
                (timer_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """UPDATE timers SET claimed_at=?
                   WHERE id=? AND status='pending' AND claimed_at IS NULL""",
                (claimed_at, timer_id),
            )
            result: dict[str, Any] = dict(row)
            result["claimed_at"] = claimed_at
            return result

    def reset_pending_timer_claims(self) -> None:
        """Make interrupted in-progress timers eligible for startup recovery."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE timers SET claimed_at=NULL WHERE status='pending'"
            )

    def record_execution(
        self,
        *,
        device_id: str,
        event_type: str,
        event_id: int,
        action: str,
        repeat_days: str = "",
        scheduled_for: str,
        triggered_at: str,
        outcome: str,
        reason: str,
    ) -> dict[str, Any]:
        """Persist the result and explanation for one scheduled execution."""
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO scheduler_events(
                       device_id, event_type, event_id, action, repeat_days, scheduled_for,
                       triggered_at, outcome, reason
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    device_id, event_type, event_id, action, repeat_days, scheduled_for,
                    triggered_at, outcome, reason,
                ),
            )
            row = connection.execute(
                "SELECT * FROM scheduler_events WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return dict(row)

    def list_executions(
        self, device_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return recent execution acknowledgements for one device."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM scheduler_events WHERE device_id=?
                   ORDER BY id DESC LIMIT ?""",
                (device_id, max(1, min(limit, 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_execution(self, device_id: str, execution_id: int) -> bool:
        """Permanently dismiss one history item belonging to the device."""
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM scheduler_events WHERE device_id=? AND id=?",
                (device_id, execution_id),
            )
        return cursor.rowcount > 0

    def set_timer_status(self, timer_id: int, status: str) -> dict[str, Any] | None:
        """Persist a timer terminal status."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE timers SET status=?, claimed_at=NULL WHERE id=?",
                (status, timer_id),
            )
        return self.get_timer(timer_id)

    def cancel_timer(self, timer_id: int) -> dict[str, Any] | None:
        """Cancel only a pending timer and return its previous row."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM timers WHERE id=? AND status='pending'
                   AND claimed_at IS NULL""",
                (timer_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """UPDATE timers SET status='cancelled'
                   WHERE id=? AND status='pending' AND claimed_at IS NULL""",
                (timer_id,),
            )
            result: dict[str, Any] = dict(row)
            result["status"] = "cancelled"
            return result

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived SQLite connection with foreign keys enabled."""
        connection: sqlite3.Connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _assert_no_schedule_conflict(
        connection: sqlite3.Connection,
        device_id: str,
        schedule_time: str,
        days: str,
        action: str,
        *,
        exclude_id: int | None = None,
    ) -> None:
        """Reject opposite actions at a matching time on any shared weekday."""
        rows = connection.execute(
            """SELECT id, days, action FROM schedules
               WHERE device_id=? AND time=? AND enabled=1 AND action<>?
                 AND (? IS NULL OR id<>?)""",
            (device_id, schedule_time, action, exclude_id, exclude_id),
        ).fetchall()
        selected_days: set[str] = _selected_days(days)
        for row in rows:
            if selected_days.intersection(_selected_days(row["days"])):
                raise ValueError(
                    f"Conflicts with schedule #{row['id']} at {schedule_time}: "
                    "opposite actions are set for an overlapping day."
                )


def _now() -> str:
    """Return the current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).isoformat()


def _selected_days(value: str) -> set[str]:
    """Expand the all-days sentinel for overlap checks."""
    return (
        {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        if value == "all"
        else set(value.split(","))
    )


def _schedule_row(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a schedule database row into a JSON-ready dictionary."""
    result: dict[str, Any] = dict(row)
    result["enabled"] = bool(result["enabled"])
    return result


def _timer_row(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a timer database row into a JSON-ready dictionary."""
    return dict(row)
