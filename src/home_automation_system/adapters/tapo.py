from typing import Any

from tapo import ApiClient

from home_automation_system.domain.devices import DeviceState


class TapoP110Switch:
    """Local-network adapter for a Tapo P110 plug."""

    def __init__(self, username: str, password: str, device_ip: str):
        self._client: ApiClient = ApiClient(username, password)
        self._device_ip: str = device_ip
        self._device: Any | None = None

    async def _get_device(self) -> Any:
        """Create the device handle once and reuse it for this session."""
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
