import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from home_automation_system.api.server import create_app
from home_automation_system.infrastructure.monitoring_logs import append_device_log
from home_automation_system.scheduling.store import ScheduleStore


class FakeTools:
    """Transport-level fake proving routes call the shared operation names."""

    def __init__(self) -> None:
        self.read_calls: list[str] = []

    async def get_plug_status(self, **_: Any) -> dict[str, object]:
        self.read_calls.append("status")
        return {"is_on": False}

    async def get_device_info(self, **_: Any) -> dict[str, object]:
        self.read_calls.append("info")
        return {"model": "P110", "rssi": -47, "signal_level": 3}

    async def get_current_power(self, **_: Any) -> dict[str, object]:
        self.read_calls.append("power")
        return {"current_power": 217}

    async def get_energy_usage(self, **_: Any) -> dict[str, object]:
        self.read_calls.append("energy")
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

    def record_audit_event(self, *_: Any, **__: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_health_and_device_routes_are_available() -> None:
    """Private API exposes process health and non-mutating device reads."""
    app = create_app(tools=FakeTools())  # type: ignore[arg-type]
    await app.state.device_poller.poll_once()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/health")
        status = await client.get("/api/device/status")
        info = await client.get("/api/device/info")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "home-automation"}
    assert status.json() == {"is_on": False}
    assert info.json()["model"] == "P110"
    assert info.json()["rssi"] == -47
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
async def test_dashboard_returns_cached_tool_data_in_ui_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dashboard returns the latest background snapshot in the existing UI shape."""
    monkeypatch.setenv("TAPO_DEVICE_NAME", "Air_Cooler")
    monkeypatch.setenv("TAPO_DEVICE_IP", "192.168.1.33")
    app = create_app(tools=FakeTools())  # type: ignore[arg-type]
    await app.state.device_poller.poll_once()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/device/dashboard")

    assert response.status_code == 200
    device = response.json()["devices"][0]
    assert device["is_on"] is False
    assert device["name"] == "Air_Cooler"
    assert device["id"] == "192.168.1.33"
    assert device["power_w"] == 217
    assert device["today_energy_kwh"] == pytest.approx(0.296)
    assert device["reachable"] is True
    assert device["last_checked"]
    assert device["power_available"] is True
    assert device["energy_available"] is True
    assert device["last_ok_at"]
    assert device["last_error_kind"] is None
    assert device["rssi"] == -47
    assert device["signal_level"] == 3


@pytest.mark.asyncio
async def test_dashboard_returns_unreachable_device_snapshot_on_tapo_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plug network failure returns explicit offline state instead of losing its card."""
    monkeypatch.setenv("TAPO_DEVICE_IP", "10.0.0.9")
    class UnreachableTools(FakeTools):
        async def get_device_info(self, **_: Any) -> dict[str, object]:
            raise ConnectionError("Tapo device did not respond")

    app = create_app(tools=UnreachableTools())  # type: ignore[arg-type]
    await app.state.device_poller.poll_once()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/device/dashboard")

    assert response.status_code == 200
    device = response.json()["devices"][0]
    assert device["reachable"] is False
    assert device["id"] == "10.0.0.9"
    assert device["is_on"] is None
    assert device["power_w"] is None
    assert device["error"] == "Plug did not respond. Check its power and Wi-Fi connection."
    assert "Tapo device did not respond" not in device["error"]
    assert device["last_checked"]
    assert device["last_ok_at"] is None
    assert device["last_error_kind"] == "ConnectionError"


@pytest.mark.asyncio
async def test_dashboard_marks_only_failed_optional_reading_unavailable() -> None:
    """A power read failure does not disguise a successful status as offline."""
    class PowerReadErrorTools(FakeTools):
        async def get_current_power(self, **_: Any) -> dict[str, object]:
            raise ConnectionError("power endpoint failed")

    app = create_app(tools=PowerReadErrorTools())  # type: ignore[arg-type]
    await app.state.device_poller.poll_once()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/device/dashboard")

    device = response.json()["devices"][0]
    assert device["reachable"] is True
    assert device["is_on"] is False
    assert device["power_available"] is False
    assert device["power_w"] is None
    assert device["last_error_kind"] == "ConnectionError"


@pytest.mark.asyncio
async def test_poller_retains_last_good_snapshot_and_logs_wifi_quality(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="home_automation_system.device_snapshot")
    class IntermittentTools(FakeTools):
        fail_info = False

        async def get_device_info(self, **_: Any) -> dict[str, object]:
            if self.fail_info:
                raise ConnectionError("plug offline")
            return await super().get_device_info()

    tools = IntermittentTools()
    app = create_app(tools=tools)  # type: ignore[arg-type]
    poller = app.state.device_poller
    await poller.poll_once()
    last_ok_at = poller.dashboard_snapshot()["devices"][0]["last_ok_at"]
    tools.fail_info = True
    await poller.poll_once()
    device = poller.dashboard_snapshot()["devices"][0]

    assert device["reachable"] is False
    assert device["last_ok_at"] == last_ok_at
    assert device["last_error_kind"] == "ConnectionError"
    assert device["is_on"] is False
    assert "rssi=-47 signal_level=3" in caplog.text


@pytest.mark.asyncio
async def test_dashboard_times_out_slow_device_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung plug request becomes a bounded offline result instead of stalling the page."""
    class SlowDeviceTools(FakeTools):
        async def get_device_info(self, **_: Any) -> dict[str, object]:
            await asyncio.sleep(1)
            return {"model": "P110"}

    monkeypatch.setenv("API_DEVICE_READ_TIMEOUT_SECONDS", "0.01")
    app = create_app(tools=SlowDeviceTools())  # type: ignore[arg-type]
    await app.state.device_poller.poll_once()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/device/dashboard")

    assert response.status_code == 200
    device = response.json()["devices"][0]
    assert device["reachable"] is False
    assert device["error"] == "Plug did not respond. Check its power and Wi-Fi connection."
    assert device["last_error_kind"] == "TimeoutError"


@pytest.mark.asyncio
async def test_device_read_routes_do_not_query_plug_after_snapshot_is_cached() -> None:
    tools = FakeTools()
    app = create_app(tools=tools)  # type: ignore[arg-type]
    await app.state.device_poller.poll_once()
    poll_calls = list(tools.read_calls)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        dashboard = await client.get("/api/device/dashboard")
        status = await client.get("/api/device/status")
        info = await client.get("/api/device/info")
        power = await client.get("/api/device/power")
        health = await client.get("/api/health/device")

    assert dashboard.status_code == status.status_code == info.status_code == 200
    assert power.status_code == health.status_code == 200
    assert status.json() == {"is_on": False}
    assert info.json()["rssi"] == health.json()["rssi"] == -47
    assert power.json()["current_power"] == 217
    assert tools.read_calls == poll_calls


@pytest.mark.asyncio
async def test_unexpected_api_exception_returns_retryable_503() -> None:
    class BrokenChat:
        async def ask(self, *_: Any) -> dict[str, object]:
            raise RuntimeError("private diagnostic detail")

    app = create_app(tools=FakeTools(), chat_service=BrokenChat())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/agent/chat",
            json={"user_id": "browser", "session_id": "session", "query": "status"},
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert "private diagnostic detail" not in response.text
    assert response.json()["detail"] == "Service temporarily unavailable. Please retry shortly."


@pytest.mark.asyncio
async def test_monitor_logs_returns_timestamped_bounded_sources() -> None:
    tools = FakeTools()
    app = create_app(tools=tools)  # type: ignore[arg-type]
    await app.state.device_poller.poll_once()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/device/dashboard")
        server_response = await client.get("/api/monitor/logs?source=server&limit=500")
        for index in range(550):
            append_device_log(f"TEST sequence={index}")
        device_response = await client.get("/api/monitor/logs?source=device&limit=500")
        oversized = await client.get("/api/monitor/logs?source=device&limit=5000")

    server_lines = server_response.json()["lines"]
    device_lines = device_response.json()["lines"]
    assert server_response.status_code == 200
    assert server_response.json()["source"] == "server"
    assert len(server_lines) <= 500
    assert any("HTTP REQUEST" in line for line in server_lines)
    assert any("HTTP RESPONSE" in line for line in server_lines)
    assert all(len(line) >= 19 and line[4] == "-" for line in server_lines)
    assert device_response.status_code == 200
    assert len(device_lines) == 500
    assert "sequence=50" in device_lines[0]
    assert "sequence=549" in device_lines[-1]
    assert oversized.status_code == 422


@pytest.mark.asyncio
async def test_monitor_logs_require_tailscale_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAILSCALE_AUTH_ENABLED", "true")
    monkeypatch.setenv("TAILSCALE_ALLOWED_USERS", "admin@example.com")
    app = create_app(tools=FakeTools())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/api/monitor/logs")
        allowed = await client.get(
            "/api/monitor/logs",
            headers={"Tailscale-User-Login": "admin@example.com"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200


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


@pytest.mark.asyncio
async def test_schedule_and_timer_routes_persist_ui_ready_rows(tmp_path: Path) -> None:
    """Schedule/timer API contracts persist rules without altering device controls."""
    store = ScheduleStore(tmp_path / "sessions.db")
    app = create_app(tools=FakeTools(), schedule_store=store)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        schedule_response = await client.post(
            "/api/device/plug-1/schedule",
            json={"time": "05:30", "days": "mon,wed,fri", "action": "off"},
        )
        schedule = schedule_response.json()
        listed_schedules = await client.get("/api/device/plug-1/schedule")
        updated = await client.patch(
            f"/api/schedule/{schedule['id']}",
            json={"enabled": False, "time": "06:15"},
        )
        timer_response = await client.post(
            "/api/device/plug-1/timer", json={"action": "on", "minutes": 7}
        )
        timer = timer_response.json()
        listed_timers = await client.get("/api/device/plug-1/timer")
        cancelled = await client.delete(f"/api/timer/{timer['id']}")
        deleted = await client.delete(f"/api/schedule/{schedule['id']}")

    assert schedule_response.status_code == 201
    assert schedule["days"] == "mon,wed,fri"
    assert schedule["enabled"] is True
    assert listed_schedules.json()[0]["action"] == "off"
    assert updated.json()["enabled"] is False
    assert updated.json()["time"] == "06:15"
    assert timer_response.status_code == 201
    assert timer["status"] == "pending"
    assert timer["remaining_minutes"] in {7, 8}
    assert listed_timers.json()[0]["action"] == "on"
    assert cancelled.json()["cancelled"] is True
    assert deleted.json()["deleted"] is True


@pytest.mark.asyncio
async def test_schedule_api_rejects_opposite_actions_at_same_overlapping_time(
    tmp_path: Path,
) -> None:
    store = ScheduleStore(tmp_path / "sessions.db")
    app = create_app(tools=FakeTools(), schedule_store=store)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        original = await client.post(
            "/api/device/plug-1/schedule",
            json={"time": "07:00", "days": "mon,wed", "action": "on"},
        )
        duplicate_action = await client.post(
            "/api/device/plug-1/schedule",
            json={"time": "07:00", "days": "mon,wed", "action": "on"},
        )
        disjoint = await client.post(
            "/api/device/plug-1/schedule",
            json={"time": "07:00", "days": "tue", "action": "off"},
        )
        conflict = await client.post(
            "/api/device/plug-1/schedule",
            json={"time": "07:00", "days": "wed,fri", "action": "off"},
        )

    assert original.status_code == 201
    assert duplicate_action.status_code == 201
    assert disjoint.status_code == 201
    assert conflict.status_code == 422
    assert f"schedule #{original.json()['id']}" in conflict.json()["detail"]


@pytest.mark.asyncio
async def test_device_events_api_returns_execution_acknowledgements(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "sessions.db")
    receipt = store.record_execution(
        device_id="plug-1",
        event_type="timer",
        event_id=8,
        action="off",
        scheduled_for="2026-09-27T12:00:00+00:00",
        triggered_at="2026-09-27T12:00:01+00:00",
        outcome="failed",
        reason="The plug could not be reached on the local network.",
    )
    app = create_app(tools=FakeTools(), schedule_store=store)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/device/plug-1/events")

    assert response.status_code == 200
    assert response.json() == [receipt]


@pytest.mark.asyncio
async def test_schedule_action_can_be_updated_and_history_item_dismissed(
    tmp_path: Path,
) -> None:
    store = ScheduleStore(tmp_path / "sessions.db")
    schedule = store.create_schedule("plug-1", "07:00", "mon,fri", "on")
    receipt = store.record_execution(
        device_id="plug-1",
        event_type="schedule",
        event_id=schedule["id"],
        action="on",
        repeat_days="mon,fri",
        scheduled_for="2026-09-27T07:00:00+00:00",
        triggered_at="2026-09-27T07:00:01+00:00",
        outcome="succeeded",
        reason="The plug confirmed the requested state.",
    )
    app = create_app(tools=FakeTools(), schedule_store=store)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        updated = await client.patch(
            f"/api/schedule/{schedule['id']}",
            json={"time": "08:15", "days": "tue,thu", "action": "off"},
        )
        dismissed = await client.delete(
            f"/api/device/plug-1/events/{receipt['id']}"
        )
        history = await client.get("/api/device/plug-1/events")

    assert updated.status_code == 200
    assert updated.json()["time"] == "08:15"
    assert updated.json()["days"] == "tue,thu"
    assert updated.json()["action"] == "off"
    assert dismissed.status_code == 200
    assert history.json() == []
    assert store.get_schedule(schedule["id"]) is not None


def test_fastapi_lifespan_starts_and_stops_scheduler(tmp_path: Path) -> None:
    """APScheduler is reconciled on startup and shut down with the API process."""
    store = ScheduleStore(tmp_path / "sessions.db")
    app = create_app(tools=FakeTools(), schedule_store=store)  # type: ignore[arg-type]
    scheduler = app.state.scheduler_service

    with TestClient(app) as client:
        assert scheduler.scheduler.running is True
        assert app.state.device_poller.running is True
        assert client.get("/api/health").status_code == 200

    assert scheduler.scheduler.running is False
    assert app.state.device_poller.running is False
