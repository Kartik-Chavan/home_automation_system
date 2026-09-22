import pytest

from home_automation_system.adapters.tapo import TapoP110Switch


class FakeDevice:
    """Device double that expires once before succeeding after refresh."""

    def __init__(self) -> None:
        self.refresh_count: int = 0
        self.info_calls: int = 0

    async def get_device_info(self) -> object:
        """Fail once with the Tapo session timeout marker."""
        self.info_calls += 1
        if self.info_calls == 1:
            raise RuntimeError("Tapo(Unauthorized { kind: SESSION_TIMEOUT })")
        return type("DeviceInfo", (), {"device_on": True})()

    async def refresh_session(self) -> None:
        """Record session refreshes."""
        self.refresh_count += 1


@pytest.mark.asyncio
async def test_adapter_refreshes_expired_session_and_retries() -> None:
    """Recover a Tapo session without restarting the MCP process."""
    adapter: TapoP110Switch = TapoP110Switch("user", "password", "192.0.2.1")
    device: FakeDevice = FakeDevice()
    adapter._device = device

    state = await adapter.get_state()

    assert state.is_on is True
    assert device.info_calls == 2
    assert device.refresh_count == 1