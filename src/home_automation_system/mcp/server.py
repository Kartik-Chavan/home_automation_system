import asyncio
import logging
import os
from datetime import date, datetime
from typing import Callable, Final

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import CurrentAccessToken
from dotenv import load_dotenv

from home_automation_system.app import create_p110_service
from home_automation_system.config import Settings
from home_automation_system.services.p110_service import P110Service


LOGGER: Final[logging.Logger] = logging.getLogger("home_automation_system.mcp")


class McpSettings:
    """Validated configuration required to start the protected MCP server."""

    def __init__(
        self,
        github_client_id: str,
        github_client_secret: str,
        allowed_github_user_id: int,
        public_base_url: str,
        jwt_signing_key: str,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self.github_client_id: str = github_client_id
        self.github_client_secret: str = github_client_secret
        self.allowed_github_user_id: int = allowed_github_user_id
        self.public_base_url: str = public_base_url.rstrip("/")
        self.jwt_signing_key: str = jwt_signing_key
        self.host: str = host
        self.port: int = port

    @classmethod
    def from_environment(cls) -> "McpSettings":
        """Load and validate MCP configuration from environment variables."""
        load_dotenv()
        required_names: tuple[str, ...] = (
            "GITHUB_CLIENT_ID",
            "GITHUB_CLIENT_SECRET",
            "ALLOWED_GITHUB_USER_ID",
            "MCP_PUBLIC_BASE_URL",
            "MCP_JWT_SIGNING_KEY",
        )
        values: dict[str, str] = {}
        missing: list[str] = []
        for name in required_names:
            value: str | None = os.getenv(name)
            if value:
                values[name] = value
            else:
                missing.append(name)
        if missing:
            raise RuntimeError(
                f"Missing required MCP environment variables: {', '.join(missing)}"
            )
        try:
            allowed_user_id: int = int(values["ALLOWED_GITHUB_USER_ID"])
            port: int = int(os.getenv("MCP_PORT", "8765"))
        except ValueError as error:
            raise RuntimeError("MCP numeric environment variables are invalid") from error
        if allowed_user_id <= 0 or not 1 <= port <= 65535:
            raise RuntimeError("MCP user ID and port must be positive valid values")
        return cls(
            github_client_id=values["GITHUB_CLIENT_ID"],
            github_client_secret=values["GITHUB_CLIENT_SECRET"],
            allowed_github_user_id=allowed_user_id,
            public_base_url=values["MCP_PUBLIC_BASE_URL"],
            jwt_signing_key=values["MCP_JWT_SIGNING_KEY"],
            host=os.getenv("MCP_HOST", "127.0.0.1"),
            port=port,
        )


def is_authorized_user(
    github_user_id: int | str | None, allowed_github_user_id: int
) -> bool:
    """Return whether a GitHub numeric user ID matches the configured allow-list."""
    if github_user_id is None:
        return False
    try:
        presented_id: int = int(github_user_id)
    except (TypeError, ValueError):
        return False
    return presented_id == allowed_github_user_id


def _github_user_id(access_token: AccessToken) -> int | None:
    """Extract GitHub's immutable numeric ID from the verified token claims."""
    subject: str | None = access_token.subject
    if subject is None:
        claim: object = access_token.claims.get("sub")
        subject = claim if isinstance(claim, str) else None
    try:
        return int(subject) if subject is not None else None
    except ValueError:
        return None


def _authorize(
    tool_name: str, access_token: AccessToken, allowed_user_id: int
) -> None:
    """Log and enforce the numeric GitHub-ID allow-list for one tool call."""
    github_user_id: int | None = _github_user_id(access_token)
    authorized: bool = is_authorized_user(github_user_id, allowed_user_id)
    LOGGER.info(
        "MCP authorization attempt github_user_id=%s tool=%s result=%s",
        github_user_id,
        tool_name,
        "allow" if authorized else "deny",
    )
    if not authorized:
        raise PermissionError("GitHub user is not authorized for this MCP server")


def create_server(
    settings: McpSettings | None = None,
    service_factory: Callable[[], P110Service] | None = None,
) -> FastMCP:
    """Create the authenticated MCP server with the configured tool set."""
    server_settings: McpSettings = settings or McpSettings.from_environment()
    provider: GitHubProvider = GitHubProvider(
        client_id=server_settings.github_client_id,
        client_secret=server_settings.github_client_secret,
        base_url=server_settings.public_base_url,
        jwt_signing_key=server_settings.jwt_signing_key,
        required_scopes=["user"],
    )
    service_builder: Callable[[], P110Service] = service_factory or _create_service
    service: P110Service = service_builder()
    mcp: FastMCP = FastMCP("Home Automation MCP", auth=provider)

    @mcp.tool
    async def get_plug_status(
        access_token: AccessToken = CurrentAccessToken(),
    ) -> dict[str, object]:
        """Get the P110 on/off state."""
        _authorize("get_plug_status", access_token, server_settings.allowed_github_user_id)
        return await service.get_plug_status()

    @mcp.tool
    async def turn_plug_on(
        access_token: AccessToken = CurrentAccessToken(),
    ) -> dict[str, object]:
        """Turn the P110 on."""
        _authorize("turn_plug_on", access_token, server_settings.allowed_github_user_id)
        return await service.turn_on()

    @mcp.tool
    async def turn_plug_off(
        access_token: AccessToken = CurrentAccessToken(),
    ) -> dict[str, object]:
        """Turn the P110 off."""
        _authorize("turn_plug_off", access_token, server_settings.allowed_github_user_id)
        return await service.turn_off()

    @mcp.tool
    async def toggle_plug(
        access_token: AccessToken = CurrentAccessToken(),
    ) -> dict[str, object]:
        """Invert the P110 state."""
        _authorize("toggle_plug", access_token, server_settings.allowed_github_user_id)
        return await service.toggle()

    @mcp.tool
    async def get_device_info(
        access_token: AccessToken = CurrentAccessToken(),
    ) -> dict[str, object]:
        """Get P110 device metadata."""
        _authorize("get_device_info", access_token, server_settings.allowed_github_user_id)
        return await service.get_device_info()

    @mcp.tool
    async def get_device_usage(
        access_token: AccessToken = CurrentAccessToken(),
    ) -> dict[str, object]:
        """Get aggregate P110 usage totals and runtime counters.

        This is a summary of today and the current month. It is not a custom
        time-range query and does not answer questions about a specific night.
        """
        _authorize("get_device_usage", access_token, server_settings.allowed_github_user_id)
        return await service.get_device_usage()

    @mcp.tool
    async def get_current_power(
        access_token: AccessToken = CurrentAccessToken(),
    ) -> dict[str, object]:
        """Get the P110 instantaneous power reading in watts.

        This is the current load only, not energy consumed over a time range.
        """
        _authorize("get_current_power", access_token, server_settings.allowed_github_user_id)
        return await service.get_current_power()

    @mcp.tool
    async def get_energy_usage(
        access_token: AccessToken = CurrentAccessToken(),
    ) -> dict[str, object]:
        """Get the P110 energy summary for today and the current month.

        Use this for app-style summary values such as today's energy, monthly
        energy, runtime, and estimated charge. It has no date parameters and
        does not return a custom 'last night' interval.
        """
        _authorize("get_energy_usage", access_token, server_settings.allowed_github_user_id)
        return await service.get_energy_usage()

    @mcp.tool
    async def get_energy_data(
        start_date: date,
        end_date: date,
        access_token: AccessToken = CurrentAccessToken(),
    ) -> dict[str, object]:
        """Get daily P110 energy totals for a date range.

        Dates are calendar dates in the plug's local timezone. Use this for
        daily energy consumption, not instantaneous power. For an overnight
        interval crossing midnight, use the two affected calendar dates and
        explain that the result is daily rather than an exact night-only total.
        """
        _authorize("get_energy_data", access_token, server_settings.allowed_github_user_id)
        return await service.get_energy_data(start_date, end_date)

    @mcp.tool
    async def get_power_data(
        start_datetime: datetime,
        end_datetime: datetime,
        access_token: AccessToken = CurrentAccessToken(),
    ) -> dict[str, object]:
        """Get hourly P110 power readings for a time range.

        This returns watts per hourly bucket and does not directly return kWh.
        The timestamps must include an explicit timezone and are interpreted in
        UTC. Convert a user's local-time request such as 'last night' to UTC
        before calling this tool. For an energy-consumption question, prefer
        get_energy_usage or get_energy_data; do not report zero power readings
        as zero energy unless the requested UTC interval is correct.
        """
        _authorize("get_power_data", access_token, server_settings.allowed_github_user_id)
        return await service.get_power_data(start_datetime, end_datetime)

    return mcp



def _create_service() -> P110Service:
    """Build the shared application service from Tapo environment settings."""
    return create_p110_service(Settings.from_environment())


def main() -> None:
    """Start the MCP server on its private local bind address."""
    logging.basicConfig(level=logging.INFO)
    server_settings: McpSettings = McpSettings.from_environment()
    mcp: FastMCP = create_server(server_settings)
    asyncio.run(
        mcp.run_async(
            transport="streamable-http",
            host=server_settings.host,
            port=server_settings.port,
        )
    )