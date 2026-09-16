import pytest

from home_automation_system.domain.devices import DeviceState
from home_automation_system.services.switch_service import SwitchService


class FakeSwitch:
    def __init__(self, is_on: bool):
        self.is_on: bool = is_on

    async def get_state(self) -> DeviceState:
        return DeviceState(is_on=self.is_on)

    async def turn_on(self) -> None:
        self.is_on = True

    async def turn_off(self) -> None:
        self.is_on = False


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_state", [False, True])
async def test_toggle_inverts_switch_state(initial_state: bool) -> None:
    switch = FakeSwitch(initial_state)
    service = SwitchService(switch)

    result: DeviceState = await service.toggle()

    assert result.is_on is (not initial_state)
    assert switch.is_on is (not initial_state)
