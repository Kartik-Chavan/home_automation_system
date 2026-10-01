"""Background poller and cached snapshot for the configured Tapo device."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable
from datetime import datetime, timezone
from uuid import uuid4

from home_automation_system.application.tools import HomeAutomationTools

LOGGER: logging.Logger = logging.getLogger("home_automation_system.device_snapshot")


class DeviceSnapshotPoller:
    """Refresh one device snapshot off-request and serve cached reads instantly."""

    def __init__(
        self,
        tools: HomeAutomationTools,
        *,
        interval_seconds: float | None = None,
        read_timeout_seconds: float | None = None,
    ) -> None:
        self.tools: HomeAutomationTools = tools
        self.interval_seconds: float = (
            interval_seconds
            if interval_seconds is not None
            else float(os.getenv("API_DEVICE_POLL_INTERVAL_SECONDS", "15"))
        )
        self.read_timeout_seconds: float = (
            read_timeout_seconds
            if read_timeout_seconds is not None
            else float(os.getenv("API_DEVICE_READ_TIMEOUT_SECONDS", "8"))
        )
        self._stop_event: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._snapshot: dict[str, object] = self._initial_snapshot()

    def start(self) -> None:
        """Start the single background polling loop if it is not already active."""
        if self._task is None or self._task.done():
            self._stop_event = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="device-snapshot-poller")

    @property
    def running(self) -> bool:
        """Whether the background poll task is currently active."""
        return self._task is not None and not self._task.done()

    async def stop(self) -> None:
        """Stop the polling loop cleanly during application shutdown."""
        task: asyncio.Task[None] | None = self._task
        if task is None:
            return
        self._stop_event.set()
        await task
        self._task = None

    def dashboard_snapshot(self) -> dict[str, object]:
        """Return a detached dashboard response without contacting the plug."""
        return {"devices": [dict(self._snapshot)]}

    def device_info(self) -> dict[str, object]:
        """Return cached device metadata for the existing information endpoint."""
        device: dict[str, object] = self._snapshot
        return {
            "device_id": device["id"],
            "nickname": device["name"],
            "model": device["model"],
            "ip": device["ip"],
            "fw_ver": device["firmware"],
            "rssi": device["rssi"],
            "signal_level": device["signal_level"],
            "reachable": device["reachable"],
            "last_ok_at": device["last_ok_at"],
            "last_error_kind": device["last_error_kind"],
        }

    def device_status(self) -> dict[str, object]:
        """Return cached on/off state for the existing status endpoint."""
        return {"is_on": self._snapshot["is_on"]}

    def current_power(self) -> dict[str, object]:
        """Return cached instantaneous power for the existing power endpoint."""
        return {
            "current_power": self._snapshot["power_w"],
            "available": self._snapshot["power_available"],
        }

    async def poll_once(self) -> None:
        """Refresh the snapshot once, isolating optional reading failures."""
        request_id: str = str(uuid4())
        previous: dict[str, object] = self._snapshot
        info: dict[str, object] = {}
        try:
            info = await self._read(
                self.tools.get_device_info(
                    request_id=request_id, actor="device-poller", source="api-poller"
                )
            )
            LOGGER.info(
                "Tapo Wi-Fi quality rssi=%s signal_level=%s",
                info.get("rssi"),
                info.get("signal_level"),
            )
            status: dict[str, object] = await self._read(
                self.tools.get_plug_status(
                    request_id=request_id, actor="device-poller", source="api-poller"
                )
            )
        except Exception as error:
            checked_at: str = _now()
            error_kind: str = type(error).__name__
            LOGGER.warning(
                "Tapo snapshot core read failed request_id=%s error_kind=%s",
                request_id,
                error_kind,
            )
            self._snapshot = {
                **previous,
                **(self._metadata(info) if info else {}),
                "reachable": False,
                "error": "Plug did not respond. Check its power and Wi-Fi connection.",
                "last_checked": checked_at,
                "last_error_kind": error_kind,
            }
            return

        power: dict[str, object] | None = None
        energy: dict[str, object] | None = None
        optional_errors: list[Exception] = []
        try:
            power = await self._read(
                self.tools.get_current_power(
                    request_id=request_id, actor="device-poller", source="api-poller"
                )
            )
        except Exception as error:
            optional_errors.append(error)
            LOGGER.warning("Tapo power poll failed request_id=%s", request_id)
        try:
            energy = await self._read(
                self.tools.get_energy_usage(
                    request_id=request_id, actor="device-poller", source="api-poller"
                )
            )
        except Exception as error:
            optional_errors.append(error)
            LOGGER.warning("Tapo energy poll failed request_id=%s", request_id)

        checked_at = _now()
        optional_error: Exception | None = optional_errors[0] if optional_errors else None
        self._snapshot = {
            **previous,
            **self._metadata(info),
            "reachable": True,
            "error": None,
            "last_checked": checked_at,
            "last_ok_at": checked_at,
            "last_error_kind": type(optional_error).__name__ if optional_error else None,
            "is_on": status.get("is_on", info.get("device_on")),
            "power_w": (
                _as_number(power.get("current_power", power.get("power")))
                if power is not None else None
            ),
            "today_energy_kwh": (
                _as_number(energy.get("today_energy")) / 1000 if energy is not None else None
            ),
            "month_energy_kwh": (
                _as_number(energy.get("month_energy")) / 1000 if energy is not None else None
            ),
            "today_runtime_hours": (
                _as_number(energy.get("today_runtime")) / 60 if energy is not None else None
            ),
            "month_runtime_hours": (
                _as_number(energy.get("month_runtime")) / 60 if energy is not None else None
            ),
            "power_available": power is not None,
            "energy_available": energy is not None,
        }

    async def _run(self) -> None:
        """Poll immediately, then wait between serialized refresh attempts."""
        while not self._stop_event.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.interval_seconds
                )
            except TimeoutError:
                continue

    async def _read(
        self, operation: Awaitable[dict[str, object]]
    ) -> dict[str, object]:
        """Apply a bound to one device read and validate its JSON-object result."""
        result: object = await asyncio.wait_for(
            operation, timeout=self.read_timeout_seconds
        )
        if not isinstance(result, dict):
            raise TypeError("Device read returned a non-object result")
        return result

    def _metadata(self, info: dict[str, object]) -> dict[str, object]:
        """Map Tapo device metadata into the dashboard's established shape."""
        model: str = str(info.get("model") or self._snapshot.get("model") or "Tapo plug")
        return {
            "id": str(
                os.getenv("TAPO_DEVICE_ID")
                or os.getenv("TAPO_DEVICE_IP")
                or info.get("ip")
                or info.get("device_id")
                or os.getenv("TAPO_DEVICE_NAME")
                or "configured-device"
            ),
            "name": str(os.getenv("TAPO_DEVICE_NAME") or info.get("nickname") or model),
            "model": model,
            "ip": str(info.get("ip") or os.getenv("TAPO_DEVICE_IP") or ""),
            "firmware": str(info.get("fw_ver") or "Unknown"),
            "rssi": info.get("rssi"),
            "signal_level": info.get("signal_level"),
        }

    @staticmethod
    def _initial_snapshot() -> dict[str, object]:
        """Provide an immediate, explicitly unpolled device card during startup."""
        configured_id: str = (
            os.getenv("TAPO_DEVICE_ID")
            or os.getenv("TAPO_DEVICE_IP")
            or os.getenv("TAPO_DEVICE_NAME")
            or "configured-device"
        )
        configured_name: str = os.getenv("TAPO_DEVICE_NAME", "Tapo plug")
        return {
            "id": configured_id,
            "name": configured_name,
            "model": "Tapo plug",
            "ip": os.getenv("TAPO_DEVICE_IP", ""),
            "firmware": "Unknown",
            "rssi": None,
            "signal_level": None,
            "reachable": False,
            "error": "Waiting for the first background device check.",
            "last_checked": None,
            "last_ok_at": None,
            "last_error_kind": "not_polled",
            "is_on": None,
            "power_w": None,
            "today_energy_kwh": None,
            "month_energy_kwh": None,
            "today_runtime_hours": None,
            "month_runtime_hours": None,
            "power_available": False,
            "energy_available": False,
        }


def _now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _as_number(value: object) -> float:
    """Convert a device reading to a finite number for the dashboard."""
    try:
        number: float = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0
