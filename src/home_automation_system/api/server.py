"""Private FastAPI application for the web UI and direct device API."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from home_automation_system.app import create_p110_service
from home_automation_system.application.tools import HomeAutomationTools
from home_automation_system.config import Settings
from home_automation_system.infrastructure.audit import AuditLogger


LOGGER: logging.Logger = logging.getLogger("home_automation_system.api")
UI_PATH: Path = Path(__file__).resolve().parents[3] / "docs" / "Home Automation.html"
TAILSCALE_LOGIN_HEADER: str = "Tailscale-User-Login"
LOCAL_HEALTH_PATHS: frozenset[str] = frozenset(
    {"/api/health", "/api/health/device", "/api/device/status"}
)


class ChatService(Protocol):
    """Protocol for the optional Cohere-backed chat service."""

    async def ask(self, user_id: str, session_id: str, query: str, request_id: str) -> dict[str, object]:
        """Process one user message and return a traceable response."""


class ChatRequest(BaseModel):
    """Validated chatbot request."""

    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=4000)


class ActionRequest(BaseModel):
    """Optional identity metadata for direct API mutations."""

    actor: str = Field(default="api-user", min_length=1, max_length=128)


def create_app(
    settings: Settings | None = None,
    tools: HomeAutomationTools | None = None,
    chat_service: ChatService | None = None,
    service_factory: Callable[[], object] | None = None,
) -> FastAPI:
    """Build the private application with injectable dependencies for tests."""
    del settings
    audit: AuditLogger = AuditLogger()
    if tools is None:
        service = (service_factory or create_p110_service)()
        tools = HomeAutomationTools(service)  # type: ignore[arg-type]
    app: FastAPI = FastAPI(title="Home Automation", version="0.1.0")

    auth_enabled: bool = _env_bool("TAILSCALE_AUTH_ENABLED", False)
    allowed_users: set[str] = _env_list("TAILSCALE_ALLOWED_USERS")

    @app.middleware("http")
    async def tailscale_auth(request: Request, call_next: object) -> object:
        """Require an allow-listed Tailscale identity when production auth is enabled."""
        request.state.actor = "local-development"
        request.state.request_id = request.headers.get("X-Request-ID", str(uuid4()))
        if auth_enabled:
            client_host: str = request.client.host if request.client else ""
            if not _is_loopback(client_host):
                audit.event(
                    "http_request",
                    request_id=request.state.request_id,
                    actor="unknown",
                    source="api",
                    outcome="denied",
                    details={"method": request.method, "path": request.url.path, "reason": "non_loopback_peer", "peer_host": client_host},
                )
                return JSONResponse(status_code=403, content={"detail": "Tailscale proxy is required"})
            login: str = request.headers.get(TAILSCALE_LOGIN_HEADER, "").strip().casefold()
            if not login and request.url.path in LOCAL_HEALTH_PATHS:
                request.state.actor = "local-health-monitor"
                return await call_next(request)  # type: ignore[operator]
            if not login or login not in allowed_users:
                audit.event(
                    "http_request",
                    request_id=request.state.request_id,
                    actor=login or "unknown",
                    source="api",
                    outcome="denied",
                    details={"method": request.method, "path": request.url.path, "reason": "user_not_allowed"},
                )
                return JSONResponse(status_code=403, content={"detail": "Tailscale user is not authorized"})
            request.state.actor = login
        return await call_next(request)  # type: ignore[operator]

    @app.middleware("http")
    async def request_audit(request: Request, call_next: object) -> object:
        """Attach a request ID and persist HTTP request outcome metadata."""
        request_id: str = getattr(
            request.state,
            "request_id",
            request.headers.get("X-Request-ID", str(uuid4())),
        )
        request.state.request_id = request_id
        try:
            response = await call_next(request)  # type: ignore[operator]
        except Exception as error:
            audit.event(
                "http_request",
                request_id=request_id,
                actor=request.state.actor,
                source="api",
                outcome="failed",
                details={"method": request.method, "path": request.url.path},
                error=error,
            )
            raise
        audit.event(
            "http_request",
            request_id=request_id,
            actor=request.state.actor,
            source="api",
            outcome="succeeded",
            details={"method": request.method, "path": request.url.path, "status": response.status_code},
        )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Return process health without contacting the physical plug."""
        return {"status": "ok", "service": "home-automation"}

    @app.get("/api/health/device")
    async def device_health(request: Request) -> dict[str, object]:
        """Perform a real device read for watchdog and uptime checks."""
        return await tools.get_device_info(
            request_id=request.state.request_id,
            actor="health-check",
            source="api",
        )

    @app.get("/api/device/status")
    async def device_status(request: Request) -> dict[str, object]:
        """Return the current plug state."""
        return await tools.get_plug_status(
            request_id=request.state.request_id,
            actor=request.state.actor,
            source="api",
        )

    @app.get("/api/device/info")
    async def device_info(request: Request) -> dict[str, object]:
        """Return device metadata."""
        return await tools.get_device_info(
            request_id=request.state.request_id,
            actor=request.state.actor,
            source="api",
        )

    @app.get("/api/device/power")
    async def current_power(request: Request) -> dict[str, object]:
        """Return instantaneous power."""
        return await tools.get_current_power(
            request_id=request.state.request_id,
            actor=request.state.actor,
            source="api",
        )

    @app.get("/api/device/dashboard")
    async def device_dashboard(request: Request) -> dict[str, object]:
        """Return a live P110 snapshot, including explicit device reachability."""
        request_id: str = request.state.request_id
        actor: str = request.state.actor
        checked_at: str = datetime.now(timezone.utc).isoformat()
        read_timeout: float = float(os.getenv("API_DEVICE_READ_TIMEOUT_SECONDS", "8"))
        info: dict[str, object] = {}
        status: dict[str, object] = {}
        power: dict[str, object] = {}
        energy: dict[str, object] = {}
        device_error: str | None = None
        power_error: bool = False
        energy_error: bool = False
        try:
            info = await asyncio.wait_for(
                tools.get_device_info(
                    request_id=request_id, actor=actor, source="api"
                ),
                timeout=read_timeout,
            )
            status_result, power_result, energy_result = await asyncio.gather(
                asyncio.wait_for(
                    tools.get_plug_status(
                        request_id=request_id, actor=actor, source="api"
                    ),
                    timeout=read_timeout,
                ),
                asyncio.wait_for(
                    tools.get_current_power(
                        request_id=request_id, actor=actor, source="api"
                    ),
                    timeout=read_timeout,
                ),
                asyncio.wait_for(
                    tools.get_energy_usage(
                        request_id=request_id, actor=actor, source="api"
                    ),
                    timeout=read_timeout,
                ),
                return_exceptions=True,
            )
            if isinstance(status_result, BaseException):
                raise status_result
            status = status_result
            power_error = isinstance(power_result, BaseException)
            energy_error = isinstance(energy_result, BaseException)
            power = {} if power_error else power_result
            energy = {} if energy_error else energy_result
            if power_error:
                LOGGER.warning("Dashboard power read failed request_id=%s", request_id)
            if energy_error:
                LOGGER.warning("Dashboard energy read failed request_id=%s", request_id)
        except Exception as error:
            LOGGER.warning(
                "Dashboard device check failed request_id=%s error=%s: %s",
                request_id,
                type(error).__name__,
                error,
            )
            device_error = "Plug did not respond. Check its power and Wi-Fi connection."
        model: str = str(info.get("model") or "Tapo plug")
        device: dict[str, object] = {
            "id": str(info.get("device_id") or info.get("ip") or "configured-device"),
            "name": str(os.getenv("TAPO_DEVICE_NAME") or info.get("nickname") or model),
            "model": model,
            "ip": str(info.get("ip") or os.getenv("TAPO_DEVICE_IP") or ""),
            "firmware": str(info.get("fw_ver") or "Unknown"),
            "rssi": info.get("rssi"),
            "signal_level": info.get("signal_level"),
            "reachable": device_error is None,
            "error": device_error,
            "last_checked": checked_at,
            "is_on": bool(status.get("is_on", info.get("device_on", False))) if device_error is None else None,
            "power_w": _as_number(power.get("current_power", power.get("power", 0))) if device_error is None and not power_error else None,
            "today_energy_kwh": _as_number(energy.get("today_energy", 0)) / 1000 if device_error is None and not energy_error else None,
            "month_energy_kwh": _as_number(energy.get("month_energy", 0)) / 1000 if device_error is None and not energy_error else None,
            "today_runtime_hours": _as_number(energy.get("today_runtime", 0)) / 60 if device_error is None and not energy_error else None,
            "month_runtime_hours": _as_number(energy.get("month_runtime", 0)) / 60 if device_error is None and not energy_error else None,
            "power_available": device_error is None and not power_error,
            "energy_available": device_error is None and not energy_error,
        }
        return {"devices": [device]}

    @app.post("/api/device/on")
    async def turn_on(request: Request, payload: ActionRequest | None = None) -> dict[str, object]:
        """Turn the plug on."""
        actor: str = request.state.actor
        return await tools.turn_plug_on(
            request_id=request.state.request_id, actor=actor, source="api"
        )

    @app.post("/api/device/off")
    async def turn_off(request: Request, payload: ActionRequest | None = None) -> dict[str, object]:
        """Turn the plug off."""
        actor: str = request.state.actor
        return await tools.turn_plug_off(
            request_id=request.state.request_id, actor=actor, source="api"
        )

    @app.post("/api/device/toggle")
    async def toggle(request: Request, payload: ActionRequest | None = None) -> dict[str, object]:
        """Toggle the plug state."""
        actor: str = request.state.actor
        return await tools.toggle_plug(
            request_id=request.state.request_id, actor=actor, source="api"
        )

    @app.post("/api/agent/chat")
    async def chat(request: Request, payload: ChatRequest) -> dict[str, object]:
        """Send one persistent conversation turn to the AI agent."""
        if chat_service is None:
            raise HTTPException(status_code=503, detail="AI agent is not configured")
        return await chat_service.ask(
            request.state.actor,
            payload.session_id,
            payload.query,
            request.state.request_id,
        )

    @app.get("/")
    async def ui() -> FileResponse:
        """Serve the browser UI from the repository documentation directory."""
        if not UI_PATH.is_file():
            raise HTTPException(status_code=404, detail="UI file is not installed")
        return FileResponse(UI_PATH, media_type="text/html")

    return app


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment setting without accepting ambiguous values."""
    value: str | None = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> set[str]:
    """Parse a comma-separated case-insensitive allow-list."""
    return {
        value.strip().casefold()
        for value in os.getenv(name, "").split(",")
        if value.strip()
    }


def _is_loopback(host: str) -> bool:
    """Return whether a proxy peer is a loopback address."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _as_number(value: object) -> float:
    """Convert a device reading to a finite displayable number."""
    try:
        number: float = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def main() -> None:
    """Start the private FastAPI server."""
    import uvicorn

    from home_automation_system.agent.service import CohereAgentService
    from home_automation_system.application.tools import HomeAutomationTools
    from home_automation_system.infrastructure.audit import AuditLogger

    shared_tools: HomeAutomationTools = HomeAutomationTools(
        create_p110_service(), AuditLogger()
    )
    app: FastAPI = create_app(
        tools=shared_tools,
        chat_service=CohereAgentService(shared_tools),
    )
    host: str = os.getenv("API_HOST", "127.0.0.1")
    port: int = int(os.getenv("API_PORT", "8000"))
    asyncio.run(
        uvicorn.Server(
            uvicorn.Config(app, host=host, port=port, proxy_headers=False)
        ).serve()
    )
