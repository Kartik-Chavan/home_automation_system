"""Shared, auditable home-automation tools for MCP, HTTP, and AI agents."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from home_automation_system.infrastructure.audit import AuditLogger
from home_automation_system.infrastructure.monitoring_logs import append_device_log
from home_automation_system.services.p110_service import P110Service


class HomeAutomationTools:
    """Expose one consistent operation contract to every frontend."""

    def __init__(
        self,
        service: P110Service,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._service: P110Service = service
        self._audit: AuditLogger = audit_logger or AuditLogger()

    async def get_plug_status(
        self, *, request_id: str | None = None, actor: str = "system", source: str = "unknown"
    ) -> dict[str, object]:
        """Read the current plug state."""
        return await self._run("get_plug_status", self._service.get_plug_status, request_id, actor, source)

    async def turn_plug_on(
        self, *, request_id: str | None = None, actor: str = "system", source: str = "unknown"
    ) -> dict[str, object]:
        """Turn the plug on and return its resulting state."""
        return await self._run("turn_plug_on", self._service.turn_on, request_id, actor, source)

    async def turn_plug_off(
        self, *, request_id: str | None = None, actor: str = "system", source: str = "unknown"
    ) -> dict[str, object]:
        """Turn the plug off and return its resulting state."""
        return await self._run("turn_plug_off", self._service.turn_off, request_id, actor, source)

    async def toggle_plug(
        self, *, request_id: str | None = None, actor: str = "system", source: str = "unknown"
    ) -> dict[str, object]:
        """Invert the plug state and return its resulting state."""
        return await self._run("toggle_plug", self._service.toggle, request_id, actor, source)

    async def get_device_info(
        self, *, request_id: str | None = None, actor: str = "system", source: str = "unknown"
    ) -> dict[str, object]:
        """Read device metadata."""
        return await self._run("get_device_info", self._service.get_device_info, request_id, actor, source)

    async def get_current_power(
        self, *, request_id: str | None = None, actor: str = "system", source: str = "unknown"
    ) -> dict[str, object]:
        """Read instantaneous power."""
        return await self._run("get_current_power", self._service.get_current_power, request_id, actor, source)

    async def get_device_usage(
        self, *, request_id: str | None = None, actor: str = "system", source: str = "unknown"
    ) -> dict[str, object]:
        """Read aggregate device usage."""
        return await self._run("get_device_usage", self._service.get_device_usage, request_id, actor, source)

    async def get_energy_usage(
        self, *, request_id: str | None = None, actor: str = "system", source: str = "unknown"
    ) -> dict[str, object]:
        """Read today's and this month's energy summary."""
        return await self._run("get_energy_usage", self._service.get_energy_usage, request_id, actor, source)

    async def get_energy_data(
        self,
        start_date: date,
        end_date: date,
        *,
        request_id: str | None = None,
        actor: str = "system",
        source: str = "unknown",
    ) -> dict[str, object]:
        """Read daily energy totals for an inclusive date range."""
        return await self._run(
            "get_energy_data",
            lambda: self._service.get_energy_data(start_date, end_date),
            request_id,
            actor,
            source,
            {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        )

    async def get_power_data(
        self,
        start_datetime: datetime,
        end_datetime: datetime,
        *,
        request_id: str | None = None,
        actor: str = "system",
        source: str = "unknown",
    ) -> dict[str, object]:
        """Read hourly power data for an explicit timezone-aware range."""
        return await self._run(
            "get_power_data",
            lambda: self._service.get_power_data(start_datetime, end_datetime),
            request_id,
            actor,
            source,
            {"start_datetime": start_datetime.isoformat(), "end_datetime": end_datetime.isoformat()},
        )

    async def execute_triggered_action(
        self,
        action: str,
        *,
        request_id: str,
        trigger_source: str,
        details: dict[str, Any],
    ) -> dict[str, object]:
        """Run a scheduler action through the shared service and audit path."""
        if action not in {"on", "off"}:
            raise ValueError("action must be 'on' or 'off'")
        service_action = self._service.turn_on if action == "on" else self._service.turn_off
        return await self._run(
            f"turn_plug_{action}",
            service_action,
            request_id,
            "scheduler",
            trigger_source,
            {"trigger_source": trigger_source, **details},
            success_outcome="success",
            failure_outcome="device_unreachable",
            log_started=False,
        )

    async def get_device_info_for_trigger(
        self,
        *,
        request_id: str,
        trigger_source: str,
        details: dict[str, Any],
    ) -> dict[str, object]:
        """Read device identity with complete scheduler correlation metadata."""
        return await self._run(
            "get_device_info",
            self._service.get_device_info,
            request_id,
            "scheduler",
            trigger_source,
            {"trigger_source": trigger_source, **details},
            success_outcome="success",
            failure_outcome="device_unreachable",
            log_started=False,
        )

    def record_audit_event(
        self,
        operation: str,
        *,
        request_id: str,
        actor: str,
        trigger_source: str,
        outcome: str,
        details: dict[str, Any],
        error: Exception | None = None,
    ) -> None:
        """Append scheduler lifecycle metadata through the shared audit writer."""
        self._audit.event(
            operation,
            request_id=request_id,
            actor=actor,
            source=trigger_source,
            outcome=outcome,
            details={"trigger_source": trigger_source, **details},
            error=error,
        )

    async def _run(
        self,
        operation: str,
        action: Any,
        request_id: str | None,
        actor: str,
        source: str,
        details: dict[str, Any] | None = None,
        success_outcome: str = "succeeded",
        failure_outcome: str = "failed",
        log_started: bool = True,
    ) -> dict[str, object]:
        """Execute and audit an operation without logging credentials or payloads."""
        operation_request_id: str = request_id or str(uuid4())
        trigger_source: str = (
            source if source.startswith(("schedule:", "timer:")) else "user"
        )
        audit_details: dict[str, Any] = dict(details or {})
        audit_details.setdefault("trigger_source", trigger_source)
        append_device_log(
            f"REQUEST operation={operation} source={source} "
            f"actor={actor} request_id={operation_request_id}"
        )
        if log_started:
            self._audit.event(
                operation,
                request_id=operation_request_id,
                actor=actor,
                source=source,
                outcome="started",
                details=audit_details,
            )
        try:
            result: object = await action()
            if not isinstance(result, dict):
                raise TypeError(f"{operation} returned a non-object result")
        except Exception as error:
            append_device_log(
                f"RESPONSE operation={operation} source={source} "
                f"request_id={operation_request_id} status=failed "
                f"error_type={type(error).__name__}",
                level=logging.ERROR,
            )
            self._audit.event(
                operation,
                request_id=operation_request_id,
                actor=actor,
                source=source,
                outcome=failure_outcome,
                details=audit_details,
                error=error,
            )
            raise
        self._audit.event(
            operation,
            request_id=operation_request_id,
            actor=actor,
            source=source,
            outcome=success_outcome,
            details=audit_details,
        )
        append_device_log(
            f"RESPONSE operation={operation} source={source} "
            f"request_id={operation_request_id} status=success "
            f"result={_safe_device_result(result)}"
        )
        return result


def _safe_device_result(result: dict[str, object]) -> str:
    """Keep terminal device logs useful without recording arbitrary payloads."""
    safe_keys: tuple[str, ...] = (
        "is_on", "current_power", "power", "model", "ip", "rssi",
        "signal_level", "reachable", "available",
    )
    safe_result: dict[str, object] = {
        key: result[key] for key in safe_keys if key in result
    }
    return json.dumps(safe_result, sort_keys=True, separators=(",", ":")) or "{}"
