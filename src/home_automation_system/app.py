from home_automation_system.adapters.tapo import TapoP110Switch
from home_automation_system.config import Settings
from home_automation_system.services.switch_service import SwitchService
from home_automation_system.services.p110_service import P110Service


def create_switch_service(settings: Settings | None = None) -> SwitchService:
    """Compose the application service with the Tapo adapter."""
    settings = settings or Settings.from_environment()
    switch = TapoP110Switch(
        username=settings.tapo_username,
        password=settings.tapo_password,
        device_ip=settings.tapo_device_ip,
    )
    return SwitchService(switch)


def create_p110_service(settings: Settings | None = None) -> P110Service:
    """Compose the energy-monitoring service with the Tapo adapter."""
    settings = settings or Settings.from_environment()
    plug = TapoP110Switch(
        username=settings.tapo_username,
        password=settings.tapo_password,
        device_ip=settings.tapo_device_ip,
    )
    return P110Service(plug)
