from pathlib import Path
from typing import Any

import pytest

from home_automation_system.agent.loop import AgentLoop
from home_automation_system.agent.sessions import SessionStore
from home_automation_system.agent.tools import build_tool_registry
from home_automation_system.infrastructure.audit import AuditLogger


class FakeSharedTools:
    """Shared-tool double for registry and composite behavior tests."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_plug_status(self, **_: Any) -> dict[str, object]:
        self.calls.append("status")
        return {"is_on": True}

    async def get_device_info(self, **_: Any) -> dict[str, object]:
        self.calls.append("info")
        return {"model": "P110"}

    async def get_current_power(self, **_: Any) -> dict[str, object]:
        self.calls.append("power")
        return {"current_power": 12}

    async def get_energy_usage(self, **_: Any) -> dict[str, object]:
        self.calls.append("energy")
        return {"today": 1}

    async def turn_plug_on(self, **_: Any) -> dict[str, object]:
        self.calls.append("on")
        return {"is_on": True}

    async def turn_plug_off(self, **_: Any) -> dict[str, object]:
        self.calls.append("off")
        return {"is_on": False}

    async def toggle_plug(self, **_: Any) -> dict[str, object]:
        self.calls.append("toggle")
        return {"is_on": True}


class FakeClient:
    """Deterministic Cohere client double."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append([dict(message) for message in messages])
        return self.responses.pop(0)


def prompt_file(tmp_path: Path) -> Path:
    path = tmp_path / "prompt.j2"
    path.write_text("Now {{ current_time }}{% if device_name %}: {{ device_name }}{% endif %}", encoding="utf-8")
    return path


def test_registry_generates_typed_schema() -> None:
    """Registry exposes optional device targeting and JSON schema."""
    registry = build_tool_registry(FakeSharedTools())  # type: ignore[arg-type]

    spec = registry["set_plug_and_verify"].spec

    assert spec["function"]["name"] == "set_plug_and_verify"
    assert "state" in spec["function"]["parameters"]["properties"]
    assert "device_name" in spec["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_composite_sets_then_verifies(tmp_path: Path) -> None:
    """Composite control performs the mutation and both verification reads."""
    shared = FakeSharedTools()
    registry = build_tool_registry(shared)  # type: ignore[arg-type]

    result = await registry["set_plug_and_verify"].invoke({"state": True})

    assert result == {
        "requested_state": True,
        "status": {"is_on": True},
        "power": {"current_power": 12},
    }
    assert shared.calls == ["on", "status", "power"]


@pytest.mark.asyncio
async def test_loop_persists_tool_plan_and_tool_result(tmp_path: Path) -> None:
    """Tool-call exchanges are replayable from SQLite with tool plan intact."""
    shared = FakeSharedTools()
    registry = build_tool_registry(shared)  # type: ignore[arg-type]
    client = FakeClient([
        {
            "message": {
                "role": "assistant",
                "tool_plan": "Check the current state.",
                "tool_calls": [{"id": "call-1", "function": {"name": "get_plug_status", "arguments": {}}}],
                "content": [],
            }
        },
        {"message": {"role": "assistant", "content": "It is on."}},
    ])
    loop = AgentLoop(client, SessionStore(tmp_path / "sessions.db"), registry, prompt_file(tmp_path), AuditLogger(tmp_path), device_names=["Desk plug"])

    result = await loop.run("user", "session", "Is it on?", "request-1")
    messages = SessionStore(tmp_path / "sessions.db").load_messages("user", "session")

    assert result["response"] == "It is on."
    assert messages[1]["tool_plan"] == "Check the current state."
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "call-1"
    assert any(message["role"] == "tool" for message in client.calls[1])


@pytest.mark.asyncio
async def test_loop_caps_repeated_tool_calls(tmp_path: Path) -> None:
    """A model that never finishes cannot keep the phone busy forever."""
    repeated_call = {
        "message": {
            "role": "assistant",
            "tool_plan": "Check again.",
            "tool_calls": [{"id": "call-loop", "function": {"name": "get_plug_status", "arguments": {}}}],
            "content": "",
        }
    }
    client = FakeClient([repeated_call.copy() for _ in range(8)])
    loop = AgentLoop(client, SessionStore(tmp_path / "sessions.db"), build_tool_registry(FakeSharedTools()), prompt_file(tmp_path), AuditLogger(tmp_path), max_iterations=8)

    result = await loop.run("user", "session", "loop", "request-cap")

    assert "stopped after 8 tool steps" in str(result["response"])
    assert len(client.calls) == 8
