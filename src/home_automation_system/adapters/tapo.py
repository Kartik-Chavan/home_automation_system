import asyncio
from datetime import date, datetime
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from tapo import ApiClient
from tapo.requests import EnergyDataInterval, PowerDataInterval

from home_automation_system.domain.devices import DeviceState


ResponseT = TypeVar("ResponseT")


class TapoP110Switch:
    """Local-network adapter for a Tapo P110 plug."""

    def __init__(self, username: str, password: str, device_ip: str):
        self._client: ApiClient = ApiClient(username, password)
        self._device_ip: str = device_ip
        self._device: Any | None = None
        self._device_lock: asyncio.Lock = asyncio.Lock()
        self._session_refresh_lock: asyncio.Lock = asyncio.Lock()

    async def _get_device(self) -> Any:
        """Create the device handle once and reuse it for this session."""
        if self._device is None:
            async with self._device_lock:
                if self._device is None:
                    self._device = await self._client.p110(self._device_ip)
        return self._device

    async def get_state(self) -> DeviceState:
        """Read the current power state from the P110."""
        info: Any = await self._call_device(lambda device: device.get_device_info())
        return DeviceState(is_on=info.device_on)

    async def turn_on(self) -> None:
        """Turn the P110 on."""
        await self._call_device(lambda device: device.on())

    async def turn_off(self) -> None:
        """Turn the P110 off."""
        await self._call_device(lambda device: device.off())

    async def get_device_info(self) -> object:
        """Return the P110 device information response."""
        return await self._call_device(lambda device: device.get_device_info())

    async def get_device_usage(self) -> object:
        """Return the P110 aggregate usage response."""
        return await self._call_device(lambda device: device.get_device_usage())

    async def get_current_power(self) -> object:
        """Return the P110 current power response."""
        return await self._call_device(lambda device: device.get_current_power())

    async def get_energy_usage(self) -> object:
        """Return the P110 energy usage response."""
        return await self._call_device(lambda device: device.get_energy_usage())

    async def get_energy_data(self, start_date: date, end_date: date) -> object:
        """Return daily P110 energy data for the requested date range."""
        return await self._call_device(
            lambda device: device.get_energy_data(
                EnergyDataInterval.Daily, start_date, end_date
            )
        )

    async def get_power_data(
        self, start_datetime: datetime, end_datetime: datetime
    ) -> object:
        """Return hourly P110 power data for the requested time range."""
        return await self._call_device(
            lambda device: device.get_power_data(
                PowerDataInterval.Hourly, start_datetime, end_datetime
            )
        )

    async def _call_device(
        self, operation: Callable[[Any], Awaitable[ResponseT]]
    ) -> ResponseT:
        """Run a device operation and recover once from an expired session."""
        device: Any = await self._get_device()
        try:
            return await operation(device)
        except Exception as error:
            if not _is_session_timeout(error):
                raise
            async with self._session_refresh_lock:
                await device.refresh_session()
            return await operation(device)


def _is_session_timeout(error: Exception) -> bool:
    """Identify the Tapo authentication error that can be refreshed safely."""
    return "SESSION_TIMEOUT" in str(error)
