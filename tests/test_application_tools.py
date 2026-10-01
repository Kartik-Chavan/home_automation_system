import json
from pathlib import Path

import pytest

from home_automation_system.application.tools import HomeAutomationTools
from home_automation_system.infrastructure.audit import AuditLogger
from home_automation_system.infrastructure.monitoring_logs import read_monitor_lines


class FakeService:
    """Minimal service double for shared-tool behavior and audit tests."""

    async def get_plug_status(self) -> dict[str, object]:
        return {"is_on": False}


@pytest.mark.asyncio
async def test_tool_result_is_audited_with_shared_request_id(tmp_path: Path) -> None:
    """A successful operation writes start and success events to JSONL."""
    tools = HomeAutomationTools(FakeService(), AuditLogger(tmp_path))  # type: ignore[arg-type]

    result = await tools.get_plug_status(
        request_id="request-1", actor="tester", source="api"
    )

    assert result == {"is_on": False}
    lines = (tmp_path / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [event["outcome"] for event in events] == ["started", "succeeded"]
    assert all(event["request_id"] == "request-1" for event in events)
    assert all(event["operation"] == "get_plug_status" for event in events)
    assert all(event["details"]["trigger_source"] == "user" for event in events)


@pytest.mark.asyncio
async def test_device_tool_writes_request_and_response_terminal_lines() -> None:
    tools = HomeAutomationTools(FakeService())  # type: ignore[arg-type]

    await tools.get_plug_status(
        request_id="monitor-log-request",
        actor="admin@example.com",
        source="api",
    )

    lines = read_monitor_lines("device", 500)
    matching = [line for line in lines if "monitor-log-request" in line]
    assert len(matching) == 2
    assert "REQUEST operation=get_plug_status" in matching[0]
    assert "RESPONSE operation=get_plug_status" in matching[1]
    assert "result={\"is_on\":false}" in matching[1]
