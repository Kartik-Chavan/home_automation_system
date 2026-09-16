from home_automation_system.adapters.tapo import TapoP110Switch
from home_automation_system.config import Settings
from home_automation_system.services.switch_service import SwitchService


def create_switch_service(settings: Settings | None = None) -> SwitchService:
    """Compose the application service with the Tapo adapter."""
    settings = settings or Settings.from_environment()
    switch = TapoP110Switch(
        username=settings.tapo_username,
        password=settings.tapo_password,
        device_ip=settings.tapo_device_ip,
    )
    return SwitchService(switch)
