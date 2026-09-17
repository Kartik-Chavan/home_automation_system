from datetime import date, datetime, timezone

import pytest
from fastmcp.server.auth import AccessToken

from home_automation_system.mcp.server import (
    McpSettings,
    create_server,
    is_authorized_user,
)


class FakeService:
    """Test double that records whether a tool reached the service layer."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_plug_status(self) -> dict[str, object]:
        """Return a fake status."""
        self.calls.append("get_plug_status")
        return {"is_on": False}

    async def turn_on(self) -> dict[str, object]:
        """Record a fake turn-on operation."""
        self.calls.append("turn_on")
        return {"is_on": True}

    async def turn_off(self) -> dict[str, object]:
        """Record a fake turn-off operation."""
        self.calls.append("turn_off")
        return {"is_on": False}

    async def toggle(self) -> dict[str, object]:
        """Record a fake toggle operation."""
        self.calls.append("toggle")
        return {"is_on": True}

    async def get_device_info(self) -> dict[str, object]:
        """Record a fake device-info read."""
        self.calls.append("get_device_info")
        return {}

    async def get_device_usage(self) -> dict[str, object]:
        """Record a fake usage read."""
        self.calls.append("get_device_usage")
        return {}

    async def get_current_power(self) -> dict[str, object]:
        """Record a fake current-power read."""
        self.calls.append("get_current_power")
        return {}

    async def get_energy_usage(self) -> dict[str, object]:
        """Record a fake energy-usage read."""
        self.calls.append("get_energy_usage")
        return {}

    async def get_energy_data(
        self, start_date: date, end_date: date
    ) -> dict[str, object]:
        """Record a fake energy-data read."""
        self.calls.append("get_energy_data")
        return {"start_date": str(start_date), "end_date": str(end_date)}

    async def get_power_data(
        self, start_datetime: datetime, end_datetime: datetime
    ) -> dict[str, object]:
        """Record a fake power-data read."""
        self.calls.append("get_power_data")
        return {"start_datetime": str(start_datetime), "end_datetime": str(end_datetime)}


def _token(user_id: str) -> AccessToken:
    """Create a verified-token-shaped test value."""
    return AccessToken(
        token="test-token",
        client_id="test-client",
        scopes=["user"],
        subject=user_id,
        claims={"sub": user_id},
    )


def _settings() -> McpSettings:
    """Create valid non-secret test configuration."""
    return McpSettings(
        github_client_id="client-id",
        github_client_secret="client-secret",
        allowed_github_user_id=12345,
        public_base_url="https://mcp.example.com",
        jwt_signing_key="a-secure-test-signing-key",
    )


@pytest.mark.parametrize(
    ("presented_id", "expected"),
    [(12345, True), ("12345", True), (12346, False), (None, False), ("", False)],
)
def test_is_authorized_user_uses_numeric_id(
    presented_id: int | str | None, expected: bool
) -> None:
    """Allow only the configured immutable GitHub numeric ID."""
    assert is_authorized_user(presented_id, 12345) is expected


@pytest.mark.asyncio
async def test_disallowed_user_cannot_reach_any_tool_service_call() -> None:
    """A valid OAuth token alone must never authorize a tool call."""
    fake_service: FakeService = FakeService()
    server = create_server(_settings(), service_factory=lambda: fake_service)  # type: ignore[arg-type]
    denied_token: AccessToken = _token("99999")

    tool_names: list[str] = [tool.name for tool in await server.list_tools()]
    for tool_name in tool_names:
        tool = await server.get_tool(tool_name)
        assert tool is not None
        with pytest.raises(PermissionError):
            if tool_name == "get_energy_data":
                await tool.fn(date(2026, 1, 1), date(2026, 1, 2), denied_token)
            elif tool_name == "get_power_data":
                await tool.fn(
                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                    datetime(2026, 1, 2, tzinfo=timezone.utc),
                    denied_token,
                )
            else:
                await tool.fn(denied_token)

    assert fake_service.calls == []