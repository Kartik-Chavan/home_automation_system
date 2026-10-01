from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from home_automation_system.application.tools import HomeAutomationTools
from home_automation_system.infrastructure.audit import AuditLogger
from home_automation_system.scheduling.scheduler import (
    SchedulerService,
    normalize_days,
    normalize_time,
)
from home_automation_system.scheduling.store import ScheduleStore
from home_automation_system.services.p110_service import P110Service


class FakeTools:
    """Scheduler test double for shared reads, actions, and audit writes."""

    def __init__(self) -> None:
        self.audit: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []

    async def get_device_info(self, **_: Any) -> dict[str, object]:
        return {"device_id": "plug-1", "nickname": "Desk plug", "ip": "10.0.0.2"}

    async def get_device_info_for_trigger(self, **_: Any) -> dict[str, object]:
        return await self.get_device_info()

    async def execute_triggered_action(
        self,
        action: str,
        *,
        request_id: str,
        trigger_source: str,
        details: dict[str, Any],
    ) -> dict[str, object]:
        self.actions.append(
            {
                "action": action,
                "request_id": request_id,
                "trigger_source": trigger_source,
                "details": details,
            }
        )
        return {"is_on": action == "on"}

    def record_audit_event(self, *args: Any, **kwargs: Any) -> None:
        self.audit.append({"args": args, **kwargs})


def test_normalize_schedule_rule_values() -> None:
    assert normalize_time("05:30") == "05:30"
    assert normalize_days("fri,mon,fri") == "mon,fri"
    assert normalize_days("all") == "all"
    with pytest.raises(ValueError):
        normalize_days("monday")


def test_termux_local_timezone_alias_is_available() -> None:
    assert ZoneInfo("Asia/Calcutta").key == "Asia/Calcutta"


def test_schedule_and_timer_tables_share_existing_database(tmp_path: Path) -> None:
    database_path = tmp_path / "sessions.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
        connection.execute("INSERT INTO messages(body) VALUES ('retained')")
        connection.execute(
            """CREATE TABLE scheduler_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN ('schedule','timer')),
                event_id INTEGER NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('on','off')),
                scheduled_for TEXT NOT NULL,
                triggered_at TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('succeeded','failed')),
                reason TEXT NOT NULL
            )"""
        )
        connection.execute(
            """INSERT INTO scheduler_events(
                device_id,event_type,event_id,action,scheduled_for,triggered_at,outcome,reason
            ) VALUES ('plug-1','schedule',2,'on','planned','checked','succeeded','retained')"""
        )

    store = ScheduleStore(database_path)
    schedule = store.create_schedule("plug-1", "05:30", "all", "off")
    timer = store.create_timer(
        "plug-1", "on", (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    )

    with sqlite3.connect(database_path) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"messages", "schedules", "timers"}.issubset(names)
        assert connection.execute("SELECT body FROM messages").fetchone()[0] == "retained"
        event_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(scheduler_events)")
        }
        assert "repeat_days" in event_columns
        assert connection.execute(
            "SELECT reason, repeat_days FROM scheduler_events"
        ).fetchone() == ("retained", "")
    assert store.get_schedule(schedule["id"])["enabled"] is True
    assert store.get_timer(timer["id"])["status"] == "pending"


def test_schedule_store_rejects_only_contradictory_overlapping_rules(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "sessions.db")
    first = store.create_schedule("plug-1", "07:00", "mon,wed", "on")

    same_action = store.create_schedule("plug-1", "07:00", "mon,wed", "on")
    disjoint_action = store.create_schedule("plug-1", "07:00", "tue,fri", "off")

    assert same_action["action"] == "on"
    assert disjoint_action["action"] == "off"
    with pytest.raises(ValueError, match=f"schedule #{first['id']}"):
        store.create_schedule("plug-1", "07:00", "wed,fri", "off")


def test_enabling_or_editing_schedule_checks_for_action_conflicts(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "sessions.db")
    store.create_schedule("plug-1", "07:00", "mon", "on")
    disabled = store.create_schedule("plug-1", "07:00", "tue", "off")
    store.update_schedule(disabled["id"], enabled=False)

    with pytest.raises(ValueError, match="opposite actions"):
        store.update_schedule(disabled["id"], enabled=True, days="mon")
    assert store.get_schedule(disabled["id"])["enabled"] is False


def test_claimed_timer_cannot_be_cancelled_and_is_recoverable(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "sessions.db")
    timer = store.create_timer(
        "plug-1", "off", (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    )

    assert store.claim_timer(timer["id"])["id"] == timer["id"]
    assert store.list_timers("plug-1") == []
    assert store.cancel_timer(timer["id"]) is None

    store.reset_pending_timer_claims()
    assert store.list_timers("plug-1")[0]["id"] == timer["id"]
    assert store.cancel_timer(timer["id"])["status"] == "cancelled"


@pytest.mark.asyncio
async def test_timer_fires_late_and_audits_correlated_details(tmp_path: Path) -> None:
    tools = FakeTools()
    store = ScheduleStore(tmp_path / "sessions.db")
    scheduler = SchedulerService(store, tools, timezone_name="UTC")  # type: ignore[arg-type]
    scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=3)
    timer = store.create_timer("plug-1", "off", scheduled_for.isoformat())

    await scheduler._fire_timer(timer["id"], timer["fire_at"])

    assert store.get_timer(timer["id"])["status"] == "fired"
    assert store.list_timers("plug-1") == []
    assert tools.actions[0]["action"] == "off"
    assert tools.actions[0]["request_id"]
    assert tools.actions[0]["trigger_source"] == f"timer:{timer['id']}"
    event_names = [entry["args"][0] for entry in tools.audit]
    assert "timer_overdue_fired" in event_names
    firing = next(entry for entry in tools.audit if entry["args"][0] == "timer_overdue_fired")
    assert firing["details"]["late_by_seconds"] >= 179


@pytest.mark.asyncio
async def test_schedule_lifecycle_uses_http_request_id_and_full_payload(tmp_path: Path) -> None:
    """Creation audit correlates to the request and preserves its complete rule."""
    tools = FakeTools()
    scheduler = SchedulerService(
        ScheduleStore(tmp_path / "sessions.db"), tools, timezone_name="UTC"
    )  # type: ignore[arg-type]

    row = await scheduler.create_schedule(
        "plug-1", "05:30", "mon,fri", "off", request_id="http-request-1", actor="member@example.com"
    )

    created = tools.audit[-1]
    assert created["args"][0] == "schedule_created"
    assert created["request_id"] == "http-request-1"
    assert created["actor"] == "member@example.com"
    assert created["trigger_source"] == f"schedule:{row['id']}"
    assert created["details"]["rule"]["time"] == "05:30"
    assert created["details"]["rule"]["days"] == "mon,fri"
    assert created["details"]["rule"]["action"] == "off"


@pytest.mark.asyncio
async def test_startup_reconciles_enabled_cron_schedule(tmp_path: Path) -> None:
    tools = FakeTools()
    store = ScheduleStore(tmp_path / "sessions.db")
    schedule = store.create_schedule("plug-1", "05:30", "mon,wed,fri", "on")
    scheduler = SchedulerService(store, tools, timezone_name="UTC")  # type: ignore[arg-type]

    await scheduler.start()
    try:
        job = scheduler.scheduler.get_job(f"schedule:{schedule['id']}")
        assert job is not None
        assert job.trigger.fields[4].name == "day_of_week"
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_startup_pauses_and_reports_legacy_conflicting_schedules(
    tmp_path: Path,
) -> None:
    tools = FakeTools()
    store = ScheduleStore(tmp_path / "sessions.db")
    first = store.create_schedule("plug-1", "07:00", "mon,wed", "on")
    with store._connect() as connection:
        connection.execute(
            """INSERT INTO schedules(device_id, time, days, action, enabled, created_at)
               VALUES ('plug-1', '07:00', 'wed,fri', 'off', 1, 'legacy')"""
        )
    scheduler = SchedulerService(store, tools, timezone_name="UTC")  # type: ignore[arg-type]

    await scheduler.start()
    try:
        rules = store.list_schedules("plug-1")
        receipts = store.list_executions("plug-1")
        assert all(rule["enabled"] is False for rule in rules)
        assert scheduler.scheduler.get_job(f"schedule:{first['id']}") is None
        assert len(receipts) == 2
        assert all(receipt["event_type"] == "schedule" for receipt in receipts)
        assert all(receipt["scheduled_for"] == "" for receipt in receipts)
        assert all("paused because it conflicts" in receipt["reason"] for receipt in receipts)
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_mutations_sync_jobs_after_persisting_database_state(tmp_path: Path) -> None:
    """Enable/disable/delete and timer cancel update SQLite before job cache state."""
    tools = FakeTools()
    store = ScheduleStore(tmp_path / "sessions.db")
    scheduler = SchedulerService(store, tools, timezone_name="UTC")  # type: ignore[arg-type]
    await scheduler.start()
    try:
        schedule = await scheduler.create_schedule("plug-1", "05:30", "all", "off")
        job_id = f"schedule:{schedule['id']}"
        assert store.get_schedule(schedule["id"])["enabled"] is True
        assert scheduler.scheduler.get_job(job_id) is not None

        disabled = await scheduler.update_schedule(schedule["id"], enabled=False)
        assert disabled["enabled"] is False
        assert scheduler.scheduler.get_job(job_id) is None

        enabled = await scheduler.update_schedule(schedule["id"], enabled=True)
        assert enabled["enabled"] is True
        assert scheduler.scheduler.get_job(job_id) is not None

        timer = await scheduler.create_timer("plug-1", "on", 20)
        timer_job_id = f"timer:{timer['id']}"
        assert store.get_timer(timer["id"])["status"] == "pending"
        assert scheduler.scheduler.get_job(timer_job_id) is not None
        cancelled = scheduler.cancel_timer(timer["id"])
        assert cancelled["status"] == "cancelled"
        assert scheduler.scheduler.get_job(timer_job_id) is None

        scheduler.delete_schedule(schedule["id"])
        assert store.get_schedule(schedule["id"]) is None
        assert scheduler.scheduler.get_job(job_id) is None
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_startup_fires_overdue_timer_and_marks_it_fired(tmp_path: Path) -> None:
    """A pending timer past its deadline fires immediately during reconciliation."""
    tools = FakeTools()
    store = ScheduleStore(tmp_path / "sessions.db")
    due = datetime.now(timezone.utc) - timedelta(minutes=2)
    timer = store.create_timer("plug-1", "on", due.isoformat())
    scheduler = SchedulerService(store, tools, timezone_name="UTC")  # type: ignore[arg-type]

    await scheduler.start()
    try:
        assert store.get_timer(timer["id"])["status"] == "fired"
        assert len(tools.actions) == 1
        assert tools.actions[0]["action"] == "on"
        missed_event = next(
            entry for entry in tools.audit
            if entry["args"][0] == "timer_missed_during_startup"
        )
        assert missed_event["details"]["late_by_seconds"] >= 119
    finally:
        scheduler.stop()


class FakePlug:
    """Small local-plug double for testing the existing shared audit path."""

    def __init__(self) -> None:
        self.is_on: bool = False

    async def get_device_info(self) -> dict[str, object]:
        return {"device_id": "plug-1", "nickname": "Desk plug", "ip": "10.0.0.2"}

    async def get_state(self) -> Any:
        return type("State", (), {"is_on": self.is_on})()

    async def turn_on(self) -> None:
        self.is_on = True

    async def turn_off(self) -> None:
        self.is_on = False


@pytest.mark.asyncio
async def test_scheduled_control_audit_has_trigger_and_drift_fields(tmp_path: Path) -> None:
    """Scheduled control emits the standard tool-operation record with trace fields."""
    tools = HomeAutomationTools(
        P110Service(FakePlug()),  # type: ignore[arg-type]
        AuditLogger(tmp_path),
    )
    store = ScheduleStore(tmp_path / "sessions.db")
    scheduler = SchedulerService(store, tools, timezone_name="UTC")
    scheduled_for: datetime = datetime.now(timezone.utc) - timedelta(seconds=3)
    fired_at: datetime = datetime.now(timezone.utc)

    await scheduler._execute_action(
        schedule_id=17,
        timer_id=None,
        device_id="plug-1",
        action="on",
        source="schedule:17",
        request_id="request-abc",
        scheduled_for=scheduled_for,
        fired_at=fired_at,
        repeat_days="mon,fri",
    )

    lines = (tmp_path / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    action_event = next(event for event in events if event["operation"] == "turn_plug_on")
    assert action_event["request_id"] == "request-abc"
    assert action_event["source"] == "schedule:17"
    assert action_event["outcome"] == "success"
    assert action_event["details"]["trigger_source"] == "schedule:17"
    assert action_event["details"]["schedule_id"] == 17
    assert action_event["details"]["device_id"] == "plug-1"
    assert action_event["details"]["action"] == "on"
    assert action_event["details"]["scheduled_for"] == scheduled_for.isoformat()
    assert action_event["details"]["fired_at"] == fired_at.isoformat()
    assert action_event["details"]["drift_seconds"] >= 3
    receipts = store.list_executions("plug-1")
    assert receipts[0]["outcome"] == "succeeded"
    assert receipts[0]["repeat_days"] == "mon,fri"
    assert receipts[0]["reason"] == "The plug confirmed the requested state."


@pytest.mark.asyncio
async def test_unreachable_scheduled_action_audits_failure_details(tmp_path: Path) -> None:
    """Device timeout is correlated and classified as device_unreachable."""
    class OfflinePlug(FakePlug):
        async def turn_on(self) -> None:
            raise TimeoutError("device did not answer")

    audit = AuditLogger(tmp_path)
    tools = HomeAutomationTools(P110Service(OfflinePlug()), audit)  # type: ignore[arg-type]
    scheduler = SchedulerService(ScheduleStore(tmp_path / "sessions.db"), tools)
    scheduled_for: datetime = datetime.now(timezone.utc) - timedelta(seconds=5)
    fired_at: datetime = datetime.now(timezone.utc)

    await scheduler._execute_action(
        schedule_id=None,
        timer_id=42,
        device_id="plug-1",
        action="on",
        source="timer:42",
        request_id="timer-request-42",
        scheduled_for=scheduled_for,
        fired_at=fired_at,
    )

    events = [
        json.loads(line)
        for line in (tmp_path / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    action_event = next(event for event in events if event["operation"] == "turn_plug_on")
    assert action_event["outcome"] == "device_unreachable"
    assert action_event["request_id"] == "timer-request-42"
    assert action_event["source"] == "timer:42"
    assert action_event["details"]["timer_id"] == 42
    assert action_event["details"]["scheduled_for"] == scheduled_for.isoformat()
    assert action_event["error"] == "device did not answer"
    receipt = scheduler.store.list_executions("plug-1")[0]
    assert receipt["outcome"] == "failed"
    assert "timed out" in receipt["reason"]