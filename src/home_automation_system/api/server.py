"""Private FastAPI application for the web UI and direct device API."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import time
from contextlib import asynccontextmanager
from collections.abc import Callable
from pathlib import Path
from typing import AsyncIterator, Literal, Protocol
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from home_automation_system.app import create_p110_service
from home_automation_system.api.device_snapshot import DeviceSnapshotPoller
from home_automation_system.application.tools import HomeAutomationTools
from home_automation_system.config import Settings
from home_automation_system.infrastructure.audit import AuditLogger
from home_automation_system.infrastructure.monitoring_logs import (
    configure_monitor_logging,
    read_monitor_lines,
)
from home_automation_system.scheduling.scheduler import SchedulerService
from home_automation_system.scheduling.store import ScheduleStore, default_database_path


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


class ScheduleCreateRequest(BaseModel):
    """Recurring local-time device rule."""

    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: str = Field(min_length=1, max_length=31)
    action: Literal["on", "off"]


class ScheduleUpdateRequest(BaseModel):
    """Fields that can be changed on an existing schedule."""

    enabled: bool | None = None
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: str | None = Field(default=None, min_length=1, max_length=31)
    action: Literal["on", "off"] | None = None


class TimerCreateRequest(BaseModel):
    """One-shot control action scheduled after a number of minutes."""

    action: Literal["on", "off"]
    minutes: int = Field(ge=1, le=525600)


def create_app(
    settings: Settings | None = None,
    tools: HomeAutomationTools | None = None,
    chat_service: ChatService | None = None,
    service_factory: Callable[[], object] | None = None,
    schedule_store: ScheduleStore | None = None,
    scheduler_service: SchedulerService | None = None,
) -> FastAPI:
    """Build the private application with injectable dependencies for tests."""
    del settings
    configure_monitor_logging()
    audit: AuditLogger = AuditLogger()
    if tools is None:
        service = (service_factory or create_p110_service)()
        tools = HomeAutomationTools(service)  # type: ignore[arg-type]
    device_poller: DeviceSnapshotPoller = DeviceSnapshotPoller(tools)
    rules: ScheduleStore = schedule_store or ScheduleStore(default_database_path())
    job_service: SchedulerService = scheduler_service or SchedulerService(rules, tools)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Reconcile persistent rules before the application accepts requests."""
        await job_service.start()
        device_poller.start()
        try:
            yield
        finally:
            await device_poller.stop()
            job_service.stop()

    app: FastAPI = FastAPI(title="Home Automation", version="0.1.0", lifespan=lifespan)
    app.state.schedule_store = rules
    app.state.scheduler_service = job_service
    app.state.device_poller = device_poller

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        """Return a retryable service response without leaking a traceback."""
        request_id: str = getattr(request.state, "request_id", str(uuid4()))
        LOGGER.error(
            "Unhandled API exception request_id=%s path=%s error_type=%s",
            request_id,
            request.url.path,
            type(error).__name__,
            exc_info=(type(error), error, error.__traceback__),
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Service temporarily unavailable. Please retry shortly.",
                "request_id": request_id,
            },
            headers={"Retry-After": "5", "X-Request-ID": request_id},
        )

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
        started_at: float = time.monotonic()
        monitor_poll: bool = request.url.path == "/api/monitor/logs"
        if not monitor_poll:
            LOGGER.info(
                "HTTP REQUEST method=%s path=%s actor=%s request_id=%s",
                request.method,
                request.url.path,
                getattr(request.state, "actor", "unknown"),
                request_id,
            )
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
            if not monitor_poll:
                LOGGER.error(
                    "HTTP RESPONSE method=%s path=%s status=exception request_id=%s error_type=%s elapsed_ms=%.1f",
                    request.method,
                    request.url.path,
                    request_id,
                    type(error).__name__,
                    (time.monotonic() - started_at) * 1000,
                )
            raise
        elapsed_ms: float = (time.monotonic() - started_at) * 1000
        response_level: int = (
            logging.ERROR if response.status_code >= 500
            else logging.WARNING if response.status_code >= 400
            else logging.INFO
        )
        if not monitor_poll:
            LOGGER.log(
                response_level,
                "HTTP RESPONSE method=%s path=%s status=%s request_id=%s elapsed_ms=%.1f",
                request.method,
                request.url.path,
                response.status_code,
                request_id,
                elapsed_ms,
            )
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

    @app.get("/api/monitor/logs")
    async def monitor_logs(
        source: Literal["server", "device"] = Query(default="server"),
        limit: int = Query(default=500, ge=1, le=500),
    ) -> dict[str, object]:
        """Return the newest terminal-style lines from an allowed log source."""
        return {"source": source, "lines": read_monitor_lines(source, limit)}

    @app.get("/api/monitor/logs")
    async def monitor_logs(
        source: Literal["server", "device"] = Query(default="server"),
        limit: int = Query(default=500, ge=1, le=500),
    ) -> dict[str, object]:
        """Return the newest bounded terminal-style log lines for the admin UI."""
        return {"source": source, "lines": read_monitor_lines(source, limit)}

    @app.get("/api/health/device")
    async def device_health(request: Request) -> dict[str, object]:
        """Return cached device information for watchdog and uptime checks."""
        return device_poller.device_info()

    @app.get("/api/device/status")
    async def device_status(request: Request) -> dict[str, object]:
        """Return the latest cached plug state."""
        return device_poller.device_status()

    @app.get("/api/device/info")
    async def device_info(request: Request) -> dict[str, object]:
        """Return cached device metadata."""
        return device_poller.device_info()

    @app.get("/api/device/power")
    async def current_power(request: Request) -> dict[str, object]:
        """Return the latest cached instantaneous power reading."""
        return device_poller.current_power()

    @app.get("/api/device/dashboard")
    async def device_dashboard(request: Request) -> dict[str, object]:
        """Return the background poller's cached P110 snapshot immediately."""
        return device_poller.dashboard_snapshot()

    @app.post("/api/device/{device_id}/schedule", status_code=201)
    async def create_schedule(
        device_id: str, payload: ScheduleCreateRequest, request: Request
    ) -> dict[str, object]:
        """Create and schedule a recurring local-time action."""
        try:
            return await job_service.create_schedule(
                device_id,
                payload.time,
                payload.days,
                payload.action,
                request_id=request.state.request_id,
                actor=request.state.actor,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/device/{device_id}/schedule")
    async def list_schedules(device_id: str) -> list[dict[str, object]]:
        """List saved recurring rules for one device."""
        return rules.list_schedules(device_id)

    @app.patch("/api/schedule/{schedule_id}")
    async def update_schedule(
        schedule_id: int, payload: ScheduleUpdateRequest, request: Request
    ) -> dict[str, object]:
        """Enable/disable or edit a recurring rule."""
        if not payload.model_fields_set:
            raise HTTPException(status_code=422, detail="Provide at least one field to update")
        try:
            row = await job_service.update_schedule(
                schedule_id,
                enabled=payload.enabled,
                schedule_time=payload.time,
                days=payload.days,
                action=payload.action,
                request_id=request.state.request_id,
                actor=request.state.actor,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if row is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return row

    @app.delete("/api/schedule/{schedule_id}")
    async def delete_schedule(schedule_id: int, request: Request) -> dict[str, object]:
        """Delete a recurring rule and remove its cached job."""
        row = job_service.delete_schedule(
            schedule_id, request_id=request.state.request_id, actor=request.state.actor
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return {"deleted": True, "schedule_id": schedule_id}

    @app.post("/api/device/{device_id}/timer", status_code=201)
    async def create_timer(
        device_id: str, payload: TimerCreateRequest, request: Request
    ) -> dict[str, object]:
        """Create a one-shot action relative to the current UTC time."""
        try:
            return await job_service.create_timer(
                device_id,
                payload.action,
                payload.minutes,
                request_id=request.state.request_id,
                actor=request.state.actor,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/device/{device_id}/timer")
    async def list_timers(device_id: str) -> list[dict[str, object]]:
        """List pending timers with remaining time for the UI."""
        return job_service.list_timers(device_id)

    @app.get("/api/device/{device_id}/events")
    async def list_device_events(device_id: str) -> list[dict[str, object]]:
        """Return recent execution acknowledgements for schedules and timers."""
        return rules.list_executions(device_id)

    @app.delete("/api/device/{device_id}/events/{event_row_id}")
    async def dismiss_device_event(device_id: str, event_row_id: int) -> dict[str, object]:
        """Permanently dismiss one history receipt without changing its schedule."""
        if not rules.delete_execution(device_id, event_row_id):
            raise HTTPException(status_code=404, detail="History item not found")
        return {"deleted": True, "event_id": event_row_id}

    @app.delete("/api/timer/{timer_id}")
    async def cancel_timer(timer_id: int, request: Request) -> dict[str, object]:
        """Cancel a pending one-shot timer."""
        row = job_service.cancel_timer(
            timer_id, request_id=request.state.request_id, actor=request.state.actor
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Pending timer not found")
        return {"cancelled": True, "timer_id": timer_id}

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
            uvicorn.Config(
                app, host=host, port=port, proxy_headers=False, log_config=None
            )
        ).serve()
    )
