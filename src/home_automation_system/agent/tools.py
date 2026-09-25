"""Typed JSON-schema tools that delegate to the shared audited layer."""

from __future__ import annotations

import inspect
import os
import typing
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel, create_model

from home_automation_system.application.tools import HomeAutomationTools

ToolFunction = Callable[..., Awaitable[dict[str, object]]]
ToolT = TypeVar("ToolT", bound=ToolFunction)


class RegisteredTool:
    """A callable plus its Cohere function schema and validation model."""

    def __init__(self, function: ToolFunction) -> None:
        self.function: ToolFunction = function
        hints: dict[str, Any] = typing.get_type_hints(function)
        fields: dict[str, tuple[Any, object]] = {}
        for name, parameter in inspect.signature(function).parameters.items():
            annotation: Any = hints.get(name, Any)
            default: object = (
                ...
                if parameter.default is inspect.Parameter.empty
                else parameter.default
            )
            fields[name] = (annotation, default)
        self.model: type[BaseModel] = create_model(
            f"{function.__name__}Arguments", **fields
        )
        self.spec: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": function.__name__,
                "description": inspect.getdoc(function) or "Home automation operation.",
                "parameters": self.model.model_json_schema(),
            },
        }

    async def invoke(self, arguments: dict[str, Any]) -> dict[str, object]:
        """Validate arguments and execute the wrapped async function."""
        validated: BaseModel = self.model.model_validate(arguments)
        result: object = await self.function(**validated.model_dump())
        if not isinstance(result, dict):
            raise TypeError(f"Tool {self.function.__name__} returned a non-object result")
        return result


def tool(function: ToolT) -> ToolT:
    """Mark a function for registry construction while preserving its signature."""
    setattr(function, "_tool_function", True)
    return function


def build_tool_registry(shared: HomeAutomationTools) -> dict[str, RegisteredTool]:
    """Build the agent's device-aware registry over shared audited operations."""
    async def get_plug_status(device_name: str | None = None) -> dict[str, object]:
        """Read the confirmed on/off state of one named device."""
        _check_device(device_name)
        return await shared.get_plug_status(
            request_id=_request_id(), actor="agent", source="cohere"
        )

    async def get_device_info(device_name: str | None = None) -> dict[str, object]:
        """Read metadata for one named device."""
        _check_device(device_name)
        return await shared.get_device_info(
            request_id=_request_id(), actor="agent", source="cohere"
        )

    async def get_current_power(device_name: str | None = None) -> dict[str, object]:
        """Read instantaneous power for one named device."""
        _check_device(device_name)
        return await shared.get_current_power(
            request_id=_request_id(), actor="agent", source="cohere"
        )

    async def get_energy_usage(device_name: str | None = None) -> dict[str, object]:
        """Read today's and this month's energy usage for one named device."""
        _check_device(device_name)
        return await shared.get_energy_usage(
            request_id=_request_id(), actor="agent", source="cohere"
        )

    async def turn_plug_on(device_name: str | None = None) -> dict[str, object]:
        """Turn one named device on and return its resulting state."""
        _check_device(device_name)
        return await shared.turn_plug_on(
            request_id=_request_id(), actor="agent", source="cohere"
        )

    async def turn_plug_off(device_name: str | None = None) -> dict[str, object]:
        """Turn one named device off and return its resulting state."""
        _check_device(device_name)
        return await shared.turn_plug_off(
            request_id=_request_id(), actor="agent", source="cohere"
        )

    async def toggle_plug(device_name: str | None = None) -> dict[str, object]:
        """Toggle one named device and return its resulting state."""
        _check_device(device_name)
        return await shared.toggle_plug(
            request_id=_request_id(), actor="agent", source="cohere"
        )

    async def set_plug_and_verify(
        state: bool, device_name: str | None = None
    ) -> dict[str, object]:
        """Set one device to an exact state, then verify state and power immediately."""
        _check_device(device_name)
        if state:
            await shared.turn_plug_on(
                request_id=_request_id(), actor="agent", source="cohere"
            )
        else:
            await shared.turn_plug_off(
                request_id=_request_id(), actor="agent", source="cohere"
            )
        status: dict[str, object] = await shared.get_plug_status(
            request_id=_request_id(), actor="agent", source="cohere"
        )
        power: dict[str, object] = await shared.get_current_power(
            request_id=_request_id(), actor="agent", source="cohere"
        )
        return {"requested_state": state, "status": status, "power": power}

    functions: list[ToolFunction] = [
        tool(get_plug_status),
        tool(get_device_info),
        tool(get_current_power),
        tool(get_energy_usage),
        tool(turn_plug_on),
        tool(turn_plug_off),
        tool(toggle_plug),
        tool(set_plug_and_verify),
    ]
    registry: dict[str, RegisteredTool] = {
        function.__name__: RegisteredTool(function) for function in functions
    }

    async def list_available_tools() -> dict[str, object]:
        """List the available home-automation tools and explain each one."""
        return {
            "tools": [
                {
                    "name": name,
                    "description": tool_spec.spec["function"]["description"],
                    "parameters": tool_spec.spec["function"]["parameters"],
                }
                for name, tool_spec in registry.items()
            ]
        }

    registry["list_available_tools"] = RegisteredTool(tool(list_available_tools))
    return registry


def _check_device(device_name: str | None) -> None:
    """Reject unknown targets until a multi-device adapter registry exists."""
    configured: str = _configured_device_name()
    if device_name and device_name.casefold() != configured.casefold():
        raise ValueError(
            f"Device '{device_name}' is not configured. Available device: '{configured}'."
        )


def _configured_device_name() -> str:
    """Return the configured device display name."""
    return os.getenv("TAPO_DEVICE_NAME", "configured P110")


def _request_id() -> str | None:
    """Read the current request correlation ID without coupling to the loop."""
    from home_automation_system.agent.loop import current_request_id

    return current_request_id()
