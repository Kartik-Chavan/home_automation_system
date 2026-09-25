"""Bounded Cohere tool-calling loop with durable message persistence."""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from home_automation_system.agent.sessions import SessionStore
from home_automation_system.agent.tools import RegisteredTool
from home_automation_system.infrastructure.audit import AuditLogger

_request_id: ContextVar[str] = ContextVar("cohere_request_id", default="")


class ChatClient(Protocol):
    """Protocol for the Cohere client, allowing deterministic loop tests."""

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Request the next assistant message."""


class AgentLoop:
    """Run a bounded tool-calling conversation and persist every message."""

    def __init__(
        self,
        client: ChatClient,
        sessions: SessionStore,
        tools: dict[str, RegisteredTool],
        prompt_path: Path,
        audit_logger: AuditLogger,
        max_iterations: int = 8,
        device_names: list[str] | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        self._client: ChatClient = client
        self._sessions: SessionStore = sessions
        self._tools: dict[str, RegisteredTool] = tools
        self._prompt_template: str = prompt_path.read_text(encoding="utf-8")
        self._audit: AuditLogger = audit_logger
        self._max_iterations: int = max_iterations
        self._device_names: list[str] = device_names or []

    async def run(
        self, user_id: str, session_id: str, query: str, request_id: str
    ) -> dict[str, object]:
        """Process one user turn, stopping after a bounded number of model calls."""
        token = _request_id.set(request_id)
        self._audit.event(
            "agent_chat", request_id=request_id, actor=user_id, source="cohere",
            outcome="started", details={"session_id": session_id},
        )
        try:
            messages: list[dict[str, Any]] = self._sessions.load_messages(user_id, session_id)
            conversation: list[dict[str, Any]] = [
                {"role": "system", "content": self._render_prompt()},
                *messages,
            ]
            user_message: dict[str, Any] = {"role": "user", "content": query}
            conversation.append(user_message)
            self._sessions.append_message(user_id, session_id, user_message, request_id)
            for _ in range(self._max_iterations):
                response: dict[str, Any] = await self._client.chat(
                    conversation, [tool.spec for tool in self._tools.values()]
                )
                assistant: dict[str, Any] = _assistant_message(response)
                conversation.append(assistant)
                self._sessions.append_message(user_id, session_id, assistant, request_id)
                calls: list[dict[str, Any]] = assistant.get("tool_calls", [])
                if not calls:
                    result: dict[str, object] = {
                        "request_id": request_id,
                        "user_id": user_id,
                        "session_id": session_id,
                        "response": _message_text(assistant),
                    }
                    self._audit.event(
                        "agent_chat", request_id=request_id, actor=user_id, source="cohere",
                        outcome="succeeded", details={"session_id": session_id},
                    )
                    return result
                for call in calls:
                    tool_message: dict[str, Any] = await self._execute_call(call)
                    conversation.append(tool_message)
                    self._sessions.append_message(user_id, session_id, tool_message, request_id)
            cap_message: str = (
                f"I stopped after {self._max_iterations} tool steps to avoid an endless loop. "
                "Please retry the request."
            )
            self._audit.event(
                "agent_chat", request_id=request_id, actor=user_id, source="cohere",
                outcome="capped", details={"session_id": session_id},
            )
            return {
                "request_id": request_id,
                "user_id": user_id,
                "session_id": session_id,
                "response": cap_message,
            }
        except Exception as error:
            self._audit.event(
                "agent_chat", request_id=request_id, actor=user_id, source="cohere",
                outcome="failed", details={"session_id": session_id}, error=error,
            )
            raise
        finally:
            _request_id.reset(token)

    async def _execute_call(self, call: dict[str, Any]) -> dict[str, Any]:
        """Validate and execute one model tool call while returning errors to Cohere."""
        function: dict[str, Any] = call.get("function", {})
        name: str = str(function.get("name", ""))
        call_id: str = str(call.get("id", ""))
        raw_arguments: object = function.get("arguments", {})
        try:
            arguments: dict[str, Any] = _arguments(raw_arguments)
            registered: RegisteredTool = self._tools[name]
            result: dict[str, object] = await registered.invoke(arguments)
            content: str = json.dumps({"ok": True, "result": result}, default=str)
        except Exception as error:
            content = json.dumps(
                {"ok": False, "error_type": type(error).__name__, "error": str(error)},
                default=str,
            )
        return {"role": "tool", "tool_call_id": call_id, "content": content}

    def _render_prompt(self) -> str:
        """Render the small Jinja-compatible prompt without a template dependency."""
        device_name: str = ", ".join(self._device_names)
        rendered: str = self._prompt_template.replace(
            "{{ current_time }}", datetime.now(timezone.utc).isoformat()
        )
        rendered = _conditional(rendered, "device_name", device_name)
        rendered = rendered.replace("{{ device_name }}", device_name)
        rendered = rendered.replace("{{ device_location }}", "")
        return rendered


def current_request_id() -> str | None:
    """Return the active request ID for shared-tool audit correlation."""
    return _request_id.get() or None


def _assistant_message(response: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Cohere v2 response into a replayable assistant message."""
    raw_message: object = response.get("message", response)
    if not isinstance(raw_message, dict):
        raise RuntimeError("Cohere response did not contain an assistant message")
    message: dict[str, Any] = {
        "role": "assistant",
        "content": raw_message.get("content", ""),
    }
    if raw_message.get("tool_plan") is not None:
        message["tool_plan"] = raw_message["tool_plan"]
    if raw_message.get("tool_calls"):
        message["tool_calls"] = raw_message["tool_calls"]
    return message


def _message_text(message: dict[str, Any]) -> str:
    """Extract plain text from Cohere text blocks or string content."""
    content: object = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
    return str(content)


def _arguments(raw_arguments: object) -> dict[str, Any]:
    """Accept Cohere's object form and compatible JSON-string form."""
    parsed: object = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    if not isinstance(parsed, dict):
        raise ValueError("tool arguments must be a JSON object")
    return parsed


def _conditional(template: str, name: str, value: str) -> str:
    """Render the simple optional variable blocks used by the prompt."""
    pattern = re.compile(r"\{% if " + re.escape(name) + r" %\}(.*?)\{% endif %\}", re.DOTALL)
    return pattern.sub(lambda match: match.group(1) if value else "", template)
