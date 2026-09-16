from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Validated runtime configuration for the home automation app."""

    tapo_username: str
    tapo_password: str
    tapo_device_ip: str

    @classmethod
    def from_environment(cls) -> "Settings":
        """Build settings from the process environment and `.env` file."""
        load_dotenv()
        variable_names: dict[str, str] = {
            "tapo_username": "TAPO_USERNAME",
            "tapo_password": "TAPO_PASSWORD",
            "tapo_device_ip": "TAPO_DEVICE_IP",
        }
        values: dict[str, str] = {}
        missing: list[str] = []
        for field_name, variable_name in variable_names.items():
            value: str | None = os.getenv(variable_name)
            if value:
                values[field_name] = value
            else:
                missing.append(variable_name)
        if missing:
            variables: str = ", ".join(missing)
            raise RuntimeError(f"Missing required environment variables: {variables}")
        return cls(**values)
