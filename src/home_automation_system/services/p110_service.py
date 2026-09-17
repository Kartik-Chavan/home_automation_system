import asyncio
import json
from datetime import date, datetime
from typing import Any

from home_automation_system.domain.devices import DeviceState, EnergyMonitoringPlug
from home_automation_system.domain.energy import add_power_reading_status


class P110Service:
    """Application use cases for the configured Tapo P110 plug."""

    def __init__(self, plug: EnergyMonitoringPlug):
        self._plug: EnergyMonitoringPlug = plug
        self._mutation_lock: asyncio.Lock = asyncio.Lock()

    async def get_plug_status(self) -> dict[str, object]:
        """Return the current on/off state."""
        state: DeviceState = await self._plug.get_state()
        return {"is_on": state.is_on}

    async def turn_on(self) -> dict[str, object]:
        """Turn the plug on and return its resulting state."""
        async with self._mutation_lock:
            await self._plug.turn_on()
            return await self.get_plug_status()

    async def turn_off(self) -> dict[str, object]:
        """Turn the plug off and return its resulting state."""
        async with self._mutation_lock:
            await self._plug.turn_off()
            return await self.get_plug_status()

    async def toggle(self) -> dict[str, object]:
        """Invert the plug state and return its resulting state."""
        async with self._mutation_lock:
            current: DeviceState = await self._plug.get_state()
            if current.is_on:
                await self._plug.turn_off()
            else:
                await self._plug.turn_on()
            return await self.get_plug_status()

    async def get_device_info(self) -> dict[str, object]:
        """Return serialized device metadata."""
        return _serialize(await self._plug.get_device_info())

    async def get_device_usage(self) -> dict[str, object]:
        """Return serialized aggregate device usage."""
        return _serialize(await self._plug.get_device_usage())

    async def get_current_power(self) -> dict[str, object]:
        """Return the serialized current power reading."""
        return _serialize(await self._plug.get_current_power())

    async def get_energy_usage(self) -> dict[str, object]:
        """Return the serialized energy usage summary."""
        return _serialize(await self._plug.get_energy_usage())

    async def get_energy_data(
        self, start_date: date, end_date: date
    ) -> dict[str, object]:
        """Return daily energy data for the requested date range."""
        if start_date > end_date:
            raise ValueError("start_date must not be later than end_date")
        return _serialize(await self._plug.get_energy_data(start_date, end_date))

    async def get_power_data(
        self, start_datetime: datetime, end_datetime: datetime
    ) -> dict[str, object]:
        """Return hourly power data with explicit missing-reading statuses."""
        if start_datetime > end_datetime:
            raise ValueError("start_datetime must not be later than end_datetime")
        data: dict[str, object] = _serialize(
            await self._plug.get_power_data(start_datetime, end_datetime)
        )
        entries: object = data.get("entries")
        if isinstance(entries, list) and all(isinstance(entry, dict) for entry in entries):
            typed_entries: list[dict[str, Any]] = [
                entry for entry in entries if isinstance(entry, dict)
            ]
            data["entries"] = add_power_reading_status(typed_entries)
        return data


def _serialize(value: object) -> dict[str, object]:
    """Convert a Tapo response into JSON-safe data."""
    response: object = value.to_dict() if hasattr(value, "to_dict") else value
    serialized: object = json.loads(json.dumps(response, default=str))
    if not isinstance(serialized, dict):
        raise TypeError("Expected the Tapo response to serialize to an object")
    return serialized