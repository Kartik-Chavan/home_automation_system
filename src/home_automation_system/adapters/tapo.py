import asyncio
from datetime import date, datetime
from typing import Any

from tapo import ApiClient
from tapo.requests import EnergyDataInterval, PowerDataInterval

from home_automation_system.domain.devices import DeviceState


class TapoP110Switch:
    """Local-network adapter for a Tapo P110 plug."""

    def __init__(self, username: str, password: str, device_ip: str):
        self._client: ApiClient = ApiClient(username, password)
        self._device_ip: str = device_ip
        self._device: Any | None = None
        self._device_lock: asyncio.Lock = asyncio.Lock()

    async def _get_device(self) -> Any:
        """Create the device handle once and reuse it for this session."""
        if self._device is None:
            async with self._device_lock:
                if self._device is None:
                    self._device = await self._client.p110(self._device_ip)
        return self._device

    async def get_state(self) -> DeviceState:
        """Read the current power state from the P110."""
        device = await self._get_device()
        info: Any = await device.get_device_info()
        return DeviceState(is_on=info.device_on)

    async def turn_on(self) -> None:
        """Turn the P110 on."""
        await (await self._get_device()).on()

    async def turn_off(self) -> None:
        """Turn the P110 off."""
        await (await self._get_device()).off()

    async def get_device_info(self) -> object:
        """Return the P110 device information response."""
        return await (await self._get_device()).get_device_info()

    async def get_device_usage(self) -> object:
        """Return the P110 aggregate usage response."""
        return await (await self._get_device()).get_device_usage()

    async def get_current_power(self) -> object:
        """Return the P110 current power response."""
        return await (await self._get_device()).get_current_power()

    async def get_energy_usage(self) -> object:
        """Return the P110 energy usage response."""
        return await (await self._get_device()).get_energy_usage()

    async def get_energy_data(self, start_date: date, end_date: date) -> object:
        """Return daily P110 energy data for the requested date range."""
        return await (await self._get_device()).get_energy_data(
            EnergyDataInterval.Daily, start_date, end_date
        )

    async def get_power_data(
        self, start_datetime: datetime, end_datetime: datetime
    ) -> object:
        """Return hourly P110 power data for the requested time range."""
        return await (await self._get_device()).get_power_data(
            PowerDataInterval.Hourly, start_datetime, end_datetime
        )
