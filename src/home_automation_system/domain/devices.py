from dataclasses import dataclass
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
