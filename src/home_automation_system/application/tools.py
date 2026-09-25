"""Shared, auditable home-automation tools for MCP, HTTP, and AI agents."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from home_automation_system.infrastructure.audit import AuditLogger
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

    async def _run(
        self,
        operation: str,
        action: Any,
        request_id: str | None,
        actor: str,
        source: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        """Execute and audit an operation without logging credentials or payloads."""
        operation_request_id: str = request_id or str(uuid4())
        self._audit.event(
            operation,
            request_id=operation_request_id,
            actor=actor,
            source=source,
            outcome="started",
            details=details,
        )
        try:
            result: object = await action()
            if not isinstance(result, dict):
                raise TypeError(f"{operation} returned a non-object result")
        except Exception as error:
            self._audit.event(
                operation,
                request_id=operation_request_id,
                actor=actor,
                source=source,
                outcome="failed",
                details=details,
                error=error,
            )
            raise
        self._audit.event(
            operation,
            request_id=operation_request_id,
            actor=actor,
            source=source,
            outcome="succeeded",
            details=details,
        )
        return result
