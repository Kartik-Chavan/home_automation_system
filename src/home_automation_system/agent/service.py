"""Plain-Python Cohere agent service with persistent conversation history."""

from __future__ import annotations

import os
from pathlib import Path

from home_automation_system.agent.client import CohereClient
from home_automation_system.agent.loop import AgentLoop
from home_automation_system.agent.sessions import SessionStore
from home_automation_system.agent.tools import build_tool_registry
from home_automation_system.app import create_p110_service
from home_automation_system.application.tools import HomeAutomationTools
from home_automation_system.infrastructure.audit import AuditLogger


class CohereAgentService:
    """Expose persistent Cohere conversations to the HTTP API."""

    def __init__(self, tools: HomeAutomationTools | None = None) -> None:
        """Build the agent from environment configuration and shared tools."""
        audit_logger: AuditLogger = AuditLogger()
        shared_tools: HomeAutomationTools = tools or HomeAutomationTools(
            create_p110_service(), audit_logger
        )
        database_path: Path = Path(
            os.getenv("AGENT_DATABASE_PATH", "logs/agent/sessions.db")
        )
        self._loop: AgentLoop = AgentLoop(
            client=CohereClient(),
            sessions=SessionStore(database_path),
            tools=build_tool_registry(shared_tools),
            prompt_path=Path(__file__).resolve().parents[3]
            / "prompts"
            / "home_automation_agent.j2",
            audit_logger=audit_logger,
            max_iterations=int(os.getenv("AGENT_MAX_ITERATIONS", "8")),
            device_names=_configured_device_names(),
        )

    async def ask(
        self, user_id: str, session_id: str, query: str, request_id: str
    ) -> dict[str, object]:
        """Process one user turn and return the established API response shape."""
        return await self._loop.run(user_id, session_id, query, request_id)


def _configured_device_names() -> list[str]:
    """Read optional display names without exposing credentials in prompts."""
    raw_names: str = os.getenv("TAPO_DEVICE_NAMES", "")
    names: list[str] = [name.strip() for name in raw_names.split(",") if name.strip()]
    return names or [os.getenv("TAPO_DEVICE_NAME", "configured P110")]

