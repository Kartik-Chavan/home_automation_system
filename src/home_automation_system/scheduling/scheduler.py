"""In-process scheduler reconciled from the authoritative SQLite rows."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from tzlocal import get_localzone_name

from home_automation_system.application.tools import HomeAutomationTools
from home_automation_system.scheduling.store import ScheduleStore

LOGGER: logging.Logger = logging.getLogger("home_automation_system.scheduler")
DAY_NAMES: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
READ_TIMEOUT_SECONDS: float = 8.0


class SchedulerService:
    """Manage recurring schedules and one-shot timers for the configured plug."""

    def __init__(
        self,
        store: ScheduleStore,
        tools: HomeAutomationTools,
        timezone_name: str | None = None,
    ) -> None:
        self.store: ScheduleStore = store
        self.tools: HomeAutomationTools = tools
        self.timezone: ZoneInfo = ZoneInfo(
            timezone_name or os.getenv("HOME_AUTOMATION_TIMEZONE", _local_timezone_name())
        )
        self.scheduler: AsyncIOScheduler = AsyncIOScheduler(timezone=self.timezone)
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the job runner and reconcile all rows before serving requests."""
        async with self._lifecycle_lock:
            if self.scheduler.running:
                return
            self.scheduler.start()
            for conflict in self.store.disable_conflicting_schedules():
                schedule_id: int = int(conflict["id"])
                conflicting_ids: list[int] = conflict["conflicts_with"]
                reason: str = (
                    f"This schedule was paused because it conflicts with schedule(s) "
                    f"{', '.join(f'#{value}' for value in conflicting_ids)} at "
                    f"{conflict['time']}; keep only one action for overlapping days."
                )
                checked_at: str = datetime.now(timezone.utc).isoformat()
                self.store.record_execution(
                    device_id=conflict["device_id"],
                    event_type="schedule",
                    event_id=schedule_id,
                    action=conflict["action"],
                    repeat_days=conflict["days"],
                    scheduled_for="",
                    triggered_at=checked_at,
                    outcome="failed",
                    reason=reason,
                )
                self._audit_lifecycle(
                    "schedule_disabled_conflict",
                    f"schedule:{schedule_id}",
                    {
                        "schedule_id": schedule_id,
                        "conflicts_with": conflicting_ids,
                        "rule": conflict,
                        "reason": reason,
                    },
                    actor="scheduler",
                )
            for row in self.store.list_enabled_schedules():
                self._register_schedule(row)
            self.store.reset_pending_timer_claims()
            now: datetime = datetime.now(timezone.utc)
            for row in self.store.list_pending_timers():
                fire_at: datetime = _parse_utc(row["fire_at"])
                if fire_at <= now:
                    await self._fire_timer(
                        int(row["id"]), row["fire_at"], startup_reconciliation=True
                    )
                else:
                    self._register_timer(row)

    def stop(self) -> None:
        """Stop the in-process runner while preserving SQLite rows."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def create_schedule(
        self,
        device_id: str,
        schedule_time: str,
        days: str,
        action: str,
        *,
        request_id: str | None = None,
        actor: str = "scheduler-api",
    ) -> dict[str, Any]:
        """Persist, then register a recurring job, and audit its creation."""
        normalized_time: str = normalize_time(schedule_time)
        normalized_days: str = normalize_days(days)
        _validate_action(action)
        self._validate_device_id(device_id)
        row: dict[str, Any] = self.store.create_schedule(
            device_id, normalized_time, normalized_days, action
        )
        self._audit_lifecycle(
            "schedule_created", f"schedule:{row['id']}",
            {"schedule_id": row["id"], "device_id": device_id, "rule": row},
            request_id=request_id,
            actor=actor,
        )
        self._register_schedule(row)
        return row

    async def update_schedule(
        self,
        schedule_id: int,
        *,
        enabled: bool | None = None,
        schedule_time: str | None = None,
        days: str | None = None,
        action: str | None = None,
        request_id: str | None = None,
        actor: str = "scheduler-api",
    ) -> dict[str, Any] | None:
        """Persist schedule edits before changing or removing its job."""
        old: dict[str, Any] | None = self.store.get_schedule(schedule_id)
        if old is None:
            return None
        normalized_time: str | None = normalize_time(schedule_time) if schedule_time else None
        normalized_days: str | None = normalize_days(days) if days else None
        if action is not None:
            _validate_action(action)
        row: dict[str, Any] | None = self.store.update_schedule(
            schedule_id, enabled=enabled, time=normalized_time, days=normalized_days,
            action=action,
        )
        if row is None:
            return None
        source: str = f"schedule:{schedule_id}"
        if enabled is not None and bool(old["enabled"]) != bool(row["enabled"]):
            self._audit_lifecycle(
                "schedule_enabled" if row["enabled"] else "schedule_disabled",
                source,
                {"schedule_id": schedule_id, "device_id": row["device_id"], "rule": row},
                request_id=request_id,
                actor=actor,
            )
        if normalized_time is not None or normalized_days is not None or action is not None:
            self._audit_lifecycle(
                "schedule_updated", source,
                {"schedule_id": schedule_id, "device_id": row["device_id"], "before": old, "after": row},
                request_id=request_id,
                actor=actor,
            )
        if row["enabled"]:
            self._register_schedule(row)
        else:
            self._remove_job(source)
        return row

    def delete_schedule(
        self, schedule_id: int, *, request_id: str | None = None, actor: str = "scheduler-api"
    ) -> dict[str, Any] | None:
        """Delete from SQLite first, then remove its cached job."""
        row: dict[str, Any] | None = self.store.delete_schedule(schedule_id)
        if row is None:
            return None
        self._remove_job(f"schedule:{schedule_id}")
        self._audit_lifecycle(
            "schedule_deleted", f"schedule:{schedule_id}",
            {"schedule_id": schedule_id, "device_id": row["device_id"], "rule": row},
            request_id=request_id,
            actor=actor,
        )
        return row

    async def create_timer(
        self,
        device_id: str,
        action: str,
        minutes: int,
        *,
        request_id: str | None = None,
        actor: str = "scheduler-api",
    ) -> dict[str, Any]:
        """Persist a timer before registering its date-triggered job."""
        if minutes < 1:
            raise ValueError("minutes must be at least 1")
        if minutes > 525600:
            raise ValueError("minutes must not exceed one year")
        _validate_action(action)
        self._validate_device_id(device_id)
        fire_at: datetime = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        row: dict[str, Any] = self.store.create_timer(device_id, action, fire_at.isoformat())
        self._audit_lifecycle(
            "timer_created", f"timer:{row['id']}",
            {"timer_id": row["id"], "device_id": device_id, "rule": row},
            request_id=request_id,
            actor=actor,
        )
        self._register_timer(row)
        return self.timer_response(row)

    def list_timers(self, device_id: str) -> list[dict[str, Any]]:
        """Return pending timers with current remaining time."""
        return [self.timer_response(row) for row in self.store.list_timers(device_id)]

    def cancel_timer(
        self, timer_id: int, *, request_id: str | None = None, actor: str = "scheduler-api"
    ) -> dict[str, Any] | None:
        """Persist cancellation before removing the timer job."""
        row: dict[str, Any] | None = self.store.cancel_timer(timer_id)
        if row is None:
            return None
        self._remove_job(f"timer:{timer_id}")
        self._audit_lifecycle(
            "timer_cancelled", f"timer:{timer_id}",
            {"timer_id": timer_id, "device_id": row["device_id"], "rule": row},
            request_id=request_id,
            actor=actor,
        )
        return row

    @staticmethod
    def timer_response(row: dict[str, Any]) -> dict[str, Any]:
        """Add remaining-time values for the timer UI."""
        response: dict[str, Any] = dict(row)
        seconds: float = max(
            0.0, (_parse_utc(row["fire_at"]) - datetime.now(timezone.utc)).total_seconds()
        )
        response["remaining_seconds"] = int(seconds)
        response["remaining_minutes"] = int((seconds + 59) // 60)
        return response

    async def _fire_schedule(self, schedule_id: int) -> None:
        """Execute a recurring rule only if SQLite still says it is enabled."""
        row: dict[str, Any] | None = self.store.get_schedule(schedule_id)
        source: str = f"schedule:{schedule_id}"
        request_id: str = str(uuid4())
        fired_at: datetime = datetime.now(timezone.utc)
        if row is None or not row["enabled"]:
            if row is not None:
                scheduled_for = _previous_schedule_fire(row, fired_at, self.timezone)
                self._record_execution(
                    schedule_id=schedule_id,
                    timer_id=None,
                    device_id=row["device_id"],
                    action=row["action"],
                    source=source,
                    request_id=request_id,
                    scheduled_for=scheduled_for,
                    fired_at=fired_at,
                    repeat_days=row["days"],
                    outcome="failed",
                    reason="This schedule was disabled before execution; no device action was sent.",
                )
            else:
                self._audit_firing(
                    "schedule_fired", request_id, source, schedule_id, None,
                    "unknown", "unknown", fired_at, fired_at, "skipped_disabled",
                    "Schedule was removed before execution.",
                )
            return
        scheduled_for: datetime = _previous_schedule_fire(row, fired_at, self.timezone)
        await self._execute_action(
            schedule_id=schedule_id, timer_id=None, device_id=row["device_id"],
            action=row["action"], source=source, request_id=request_id,
            scheduled_for=scheduled_for, fired_at=fired_at,
            repeat_days=row["days"],
        )

    async def _fire_timer(
        self,
        timer_id: int,
        scheduled_for_raw: str,
        *,
        startup_reconciliation: bool = False,
    ) -> None:
        """Fire a pending timer, including timers overdue after server downtime."""
        row: dict[str, Any] | None = self.store.claim_timer(timer_id)
        source: str = f"timer:{timer_id}"
        request_id: str = str(uuid4())
        if row is None:
            return
        scheduled_for: datetime = _parse_utc(row["fire_at"])
        fired_at: datetime = datetime.now(timezone.utc)
        late_by_seconds: float = max(0.0, (fired_at - scheduled_for).total_seconds())
        if late_by_seconds > 1:
            self._audit_lifecycle(
                "timer_missed_during_startup" if startup_reconciliation else "timer_overdue_fired", source,
                {
                    "timer_id": timer_id,
                    "device_id": row["device_id"],
                    "action": row["action"],
                    "scheduled_for": scheduled_for.isoformat(),
                    "late_by_seconds": round(late_by_seconds, 3),
                    "note": "Timer's due time passed; action will execute immediately and status will become fired.",
                },
                request_id=request_id,
            )
        try:
            await self._execute_action(
                schedule_id=None, timer_id=timer_id, device_id=row["device_id"],
                action=row["action"], source=source, request_id=request_id,
                scheduled_for=scheduled_for, fired_at=fired_at,
            )
        finally:
            self.store.set_timer_status(timer_id, "fired")
            self._remove_job(source)

    async def _execute_action(
        self,
        *,
        schedule_id: int | None,
        timer_id: int | None,
        device_id: str,
        action: str,
        source: str,
        request_id: str,
        scheduled_for: datetime,
        fired_at: datetime,
        repeat_days: str = "",
    ) -> None:
        """Resolve the target and call the shared control method with trace metadata."""
        details: dict[str, Any] = {
            "schedule_id": schedule_id,
            "timer_id": timer_id,
            "device_id": device_id,
            "action": action,
            "scheduled_for": scheduled_for.isoformat(),
            "fired_at": fired_at.isoformat(),
            "drift_seconds": round((fired_at - scheduled_for).total_seconds(), 3),
        }
        try:
            info: dict[str, object] = await asyncio.wait_for(
                self.tools.get_device_info_for_trigger(
                    request_id=request_id,
                    trigger_source=source,
                    details=details,
                ),
                timeout=READ_TIMEOUT_SECONDS,
            )
            if not self._matches_configured_device(device_id, info):
                self._record_execution(
                    schedule_id=schedule_id, timer_id=timer_id, device_id=device_id,
                    action=action, source=source, request_id=request_id,
                    scheduled_for=scheduled_for, fired_at=fired_at,
                    repeat_days=repeat_days,
                    outcome="failed",
                    reason="The saved device no longer matches the configured plug; no command was sent.",
                )
                return
        except Exception as error:
            LOGGER.warning(
                "Scheduled device resolution failed request_id=%s source=%s error=%s",
                request_id,
                source,
                error,
            )
            self._record_execution(
                schedule_id=schedule_id, timer_id=timer_id, device_id=device_id,
                action=action, source=source, request_id=request_id,
                scheduled_for=scheduled_for, fired_at=fired_at,
                repeat_days=repeat_days,
                outcome="failed", reason=_safe_failure_reason(error, "device check"),
            )
            return
        try:
            result: dict[str, object] = await asyncio.wait_for(
                self.tools.execute_triggered_action(
                    action,
                    request_id=request_id,
                    trigger_source=source,
                    details=details,
                ),
                timeout=READ_TIMEOUT_SECONDS,
            )
        except Exception as error:
            LOGGER.warning(
                "Scheduled action failed request_id=%s source=%s error=%s",
                request_id,
                source,
                error,
            )
            self._record_execution(
                schedule_id=schedule_id, timer_id=timer_id, device_id=device_id,
                action=action, source=source, request_id=request_id,
                scheduled_for=scheduled_for, fired_at=fired_at,
                repeat_days=repeat_days,
                outcome="failed", reason=_safe_failure_reason(error, "device action"),
            )
        else:
            expected_state: bool = action == "on"
            actual_state: object = result.get("is_on")
            if isinstance(actual_state, bool) and actual_state is expected_state:
                outcome: str = "succeeded"
                reason: str = "The plug confirmed the requested state."
            elif isinstance(actual_state, bool):
                outcome = "failed"
                reason = "The plug responded, but its reported state did not match the requested action."
            else:
                outcome = "failed"
                reason = "The command completed without a confirmed plug state."
            self._record_execution(
                schedule_id=schedule_id, timer_id=timer_id, device_id=device_id,
                action=action, source=source, request_id=request_id,
                scheduled_for=scheduled_for, fired_at=fired_at,
                repeat_days=repeat_days,
                outcome=outcome, reason=reason,
            )
            LOGGER.info(
                "Scheduled action outcome=%s request_id=%s source=%s",
                outcome, request_id, source,
            )

    def _record_execution(
        self,
        *,
        schedule_id: int | None,
        timer_id: int | None,
        device_id: str,
        action: str,
        source: str,
        request_id: str,
        scheduled_for: datetime,
        fired_at: datetime,
        repeat_days: str = "",
        outcome: str,
        reason: str,
    ) -> None:
        """Store a user-visible execution receipt and its audit counterpart."""
        event_type: str = "schedule" if schedule_id is not None else "timer"
        event_id: int = schedule_id if schedule_id is not None else timer_id or 0
        self.store.record_execution(
            device_id=device_id,
            event_type=event_type,
            event_id=event_id,
            action=action,
            repeat_days=repeat_days,
            scheduled_for=scheduled_for.isoformat(),
            triggered_at=fired_at.isoformat(),
            outcome=outcome,
            reason=reason,
        )
        self._audit_firing(
            "scheduled_device_action", request_id, source, schedule_id, timer_id,
            device_id, action, scheduled_for, fired_at, outcome, reason,
        )

    def _validate_device_id(self, device_id: str) -> None:
        """Reject blank identifiers; firing validates against live device identity."""
        if not device_id.strip():
            raise ValueError("device_id must not be empty")

    @staticmethod
    def _matches_configured_device(device_id: str, info: dict[str, object]) -> bool:
        """Protect the single-device backend from targeting an unknown plug."""
        identities: set[str] = {
            str(value).casefold()
            for value in (
                info.get("device_id"), info.get("ip"), info.get("nickname"),
                os.getenv("TAPO_DEVICE_NAME"), os.getenv("TAPO_DEVICE_IP"),
            )
            if value
        }
        return device_id.casefold() in identities

    def _register_schedule(self, row: dict[str, Any]) -> None:
        """Derive an in-memory cron job from an enabled SQLite row."""
        if not self.scheduler.running:
            return
        hour, minute = (int(part) for part in row["time"].split(":"))
        trigger: CronTrigger = CronTrigger(
            day_of_week=_cron_days(row["days"]), hour=hour, minute=minute,
            second=0, timezone=self.timezone,
        )
        self.scheduler.add_job(
            self._fire_schedule, trigger=trigger, id=f"schedule:{row['id']}",
            args=[int(row["id"])], replace_existing=True, coalesce=True,
            misfire_grace_time=None,
        )

    def _register_timer(self, row: dict[str, Any]) -> None:
        """Derive an in-memory date job from a pending SQLite row."""
        if not self.scheduler.running:
            return
        timer_id: int = int(row["id"])
        self.scheduler.add_job(
            self._fire_timer,
            trigger=DateTrigger(run_date=_parse_utc(row["fire_at"]), timezone=timezone.utc),
            id=f"timer:{timer_id}", args=[timer_id, row["fire_at"]],
            replace_existing=True, misfire_grace_time=None,
        )

    def _remove_job(self, job_id: str) -> None:
        """Remove one cached job if present."""
        if self.scheduler.running and self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    def _audit_lifecycle(
        self,
        operation: str,
        source: str,
        details: dict[str, Any],
        *,
        request_id: str | None = None,
        actor: str = "scheduler-api",
    ) -> None:
        """Log lifecycle metadata through the same shared audit writer."""
        self.tools.record_audit_event(
            operation, request_id=request_id or str(uuid4()), actor=actor,
            trigger_source=source, outcome="success", details=details,
        )

    def _audit_firing(
        self, operation: str, request_id: str, source: str,
        schedule_id: int | None, timer_id: int | None, device_id: str,
        action: str, scheduled_for: datetime, fired_at: datetime,
        outcome: str, error_detail: str | None,
    ) -> None:
        """Audit an action skipped because SQLite state made it ineligible."""
        details: dict[str, Any] = {
            "schedule_id": schedule_id, "timer_id": timer_id,
            "device_id": device_id, "action": action,
            "scheduled_for": scheduled_for.isoformat(),
            "fired_at": fired_at.isoformat(),
            "drift_seconds": round((fired_at - scheduled_for).total_seconds(), 3),
            "outcome": outcome,
        }
        if error_detail:
            details["error_detail"] = error_detail
        self.tools.record_audit_event(
            operation, request_id=request_id, actor="scheduler",
            trigger_source=source, outcome=outcome, details=details,
        )


def normalize_time(value: str) -> str:
    """Validate and normalize local wall-clock time to HH:MM."""
    parsed: time = time.fromisoformat(value)
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise ValueError("time must use HH:MM local time")
    return parsed.strftime("%H:%M")


def normalize_days(value: str) -> str:
    """Validate a weekday list or preserve the all-days sentinel."""
    normalized: str = value.strip().casefold()
    if normalized == "all":
        return "all"
    selected: set[str] = {day.strip() for day in normalized.split(",") if day.strip()}
    if not selected or not selected.issubset(set(DAY_NAMES)):
        raise ValueError("days must be 'all' or comma-separated weekday abbreviations")
    return ",".join(day for day in DAY_NAMES if day in selected)


def _cron_days(days: str) -> str:
    """Convert the all-days sentinel to APScheduler syntax."""
    return "mon-sun" if days == "all" else days


def _parse_utc(value: str) -> datetime:
    """Parse an aware ISO timestamp and normalize it to UTC."""
    parsed: datetime = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timer fire_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _safe_failure_reason(error: Exception, stage: str) -> str:
    """Describe common scheduled-action failures without exposing raw exceptions."""
    if isinstance(error, TimeoutError):
        return f"The {stage} timed out before the plug confirmed the action."
    if isinstance(error, (ConnectionError, OSError)):
        return "The plug could not be reached on the local network."
    return f"The {stage} failed ({type(error).__name__})."


def _previous_schedule_fire(row: dict[str, Any], now: datetime, zone: ZoneInfo) -> datetime:
    """Find the most recent cron occurrence at or before the actual firing time."""
    hour, minute = (int(part) for part in row["time"].split(":"))
    trigger: CronTrigger = CronTrigger(
        day_of_week=_cron_days(row["days"]), hour=hour, minute=minute,
        second=0, timezone=zone,
    )
    local_now: datetime = now.astimezone(zone)
    next_fire: datetime | None = trigger.get_next_fire_time(None, local_now - timedelta(days=8))
    latest: datetime | None = None
    while next_fire is not None and next_fire <= local_now:
        latest = next_fire
        next_fire = trigger.get_next_fire_time(next_fire, local_now)
    return (latest or local_now).astimezone(timezone.utc)


def _local_timezone_name() -> str:
    """Return the system's IANA timezone name, falling back to UTC."""
    try:
        return get_localzone_name()
    except (OSError, ValueError):
        return "UTC"


def _validate_action(action: str) -> None:
    """Accept only state-changing operations the shared tools implement."""
    if action not in {"on", "off"}:
        raise ValueError("action must be 'on' or 'off'")
