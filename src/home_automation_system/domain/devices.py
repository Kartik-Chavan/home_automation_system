from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True)
class DeviceState:
    """Snapshot of a switch's power state."""

    is_on: bool


class Switch(Protocol):
    """Port implemented by controllable switch adapters."""

    async def get_state(self) -> DeviceState:
        """Return the current switch state."""

    async def turn_on(self) -> None:
        """Turn the switch on."""

    async def turn_off(self) -> None:
        """Turn the switch off."""


class EnergyMonitoringPlug(Switch, Protocol):
    """Port for a controllable plug with energy monitoring."""

    async def get_device_info(self) -> object:
        """Return device metadata."""

    async def get_device_usage(self) -> object:
        """Return aggregate device usage."""

    async def get_current_power(self) -> object:
        """Return the current power reading."""

    async def get_energy_usage(self) -> object:
        """Return the current energy usage summary."""

    async def get_energy_data(self, start_date: date, end_date: date) -> object:
        """Return daily energy data for the inclusive date range."""

    async def get_power_data(
        self, start_datetime: datetime, end_datetime: datetime
    ) -> object:
        """Return hourly power data for the requested time range."""
