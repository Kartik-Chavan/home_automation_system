from typing import Any

import httpx
import pytest

from home_automation_system.api.server import create_app


class FakeTools:
    """Transport-level fake proving routes call the shared operation names."""

    async def get_plug_status(self, **_: Any) -> dict[str, object]:
        return {"is_on": False}

    async def get_device_info(self, **_: Any) -> dict[str, object]:
        return {"model": "P110"}

    async def get_current_power(self, **_: Any) -> dict[str, object]:
        return {"current_power": 217}

    async def get_energy_usage(self, **_: Any) -> dict[str, object]:
        return {
            "today_energy": 296,
            "month_energy": 6842,
            "today_runtime": 60,
            "month_runtime": 410,
        }

    async def turn_plug_on(self, **_: Any) -> dict[str, object]:
        return {"is_on": True}

    async def turn_plug_off(self, **_: Any) -> dict[str, object]:
        return {"is_on": False}

    async def toggle_plug(self, **_: Any) -> dict[str, object]:
        return {"is_on": True}


@pytest.mark.asyncio
async def test_health_and_device_routes_are_available() -> None:
    """Private API exposes process health and non-mutating device reads."""
    app = create_app(tools=FakeTools())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/health")
        status = await client.get("/api/device/status")
        info = await client.get("/api/device/info")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "home-automation"}
    assert status.json() == {"is_on": False}
    assert info.json() == {"model": "P110"}
    assert health.headers["x-request-id"]


@pytest.mark.asyncio
async def test_mutation_route_returns_shared_tool_result() -> None:
    """HTTP mutations return the actual device-tool result."""
    app = create_app(tools=FakeTools())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/device/on", json={"actor": "test-user"})

    assert response.status_code == 200
    assert response.json() == {"is_on": True}


@pytest.mark.asyncio
async def test_dashboard_returns_live_tool_data_in_ui_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dashboard snapshot is composed from real device info/status/power/energy tools."""
    monkeypatch.setenv("TAPO_DEVICE_NAME", "Air_Cooler")
    app = create_app(tools=FakeTools())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/device/dashboard")

    assert response.status_code == 200
    device = response.json()["devices"][0]
    assert device["is_on"] is False
    assert device["name"] == "Air_Cooler"
    assert device["power_w"] == 217
    assert device["today_energy_kwh"] == pytest.approx(0.296)


@pytest.mark.asyncio
async def test_tailscale_allow_list_uses_loopback_proxy_and_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only an allow-listed identity from a local Tailscale proxy is accepted."""
    monkeypatch.setenv("TAILSCALE_AUTH_ENABLED", "true")
    monkeypatch.setenv("TAILSCALE_ALLOWED_USERS", "parent@example.com, sibling@example.com")
    app = create_app(tools=FakeTools())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        allowed = await client.get("/api/health", headers={"Tailscale-User-Login": "Parent@Example.com"})
        denied = await client.get("/api/health", headers={"Tailscale-User-Login": "other@example.com"})

    assert allowed.status_code == 200
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_tailscale_auth_rejects_non_loopback_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spoofed Tailscale identity is rejected from a direct network peer."""
    monkeypatch.setenv("TAILSCALE_AUTH_ENABLED", "true")
    monkeypatch.setenv("TAILSCALE_ALLOWED_USERS", "family@example.com")
    app = create_app(tools=FakeTools())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app, client=("100.90.80.70", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/device/status",
            headers={"Tailscale-User-Login": "family@example.com"},
        )

    assert response.status_code == 403
