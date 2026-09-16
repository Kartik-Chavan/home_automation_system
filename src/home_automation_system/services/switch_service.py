from home_automation_system.domain.devices import DeviceState, Switch


class SwitchService:
    """Application use cases for a controllable switch."""

    def __init__(self, switch: Switch):
        self._switch: Switch = switch

    async def get_state(self) -> DeviceState:
        """Return the switch's current state."""
        return await self._switch.get_state()

    async def toggle(self) -> DeviceState:
        """Invert the switch state and return the resulting state."""
        current = await self._switch.get_state()
        if current.is_on:
            await self._switch.turn_off()
        else:
            await self._switch.turn_on()
        return await self._switch.get_state()
