import asyncio

from home_automation_system.app import create_switch_service
from home_automation_system.domain.devices import DeviceState


async def run() -> None:
    """Execute one state read and one toggle operation."""
    service = create_switch_service()
    before: DeviceState = await service.get_state()
    print(f"Initial state: {'ON' if before.is_on else 'OFF'}")

    print(f"Turning plug {'off' if before.is_on else 'on'}...")
    after = await service.toggle()
    print(f"Current state: {'ON' if after.is_on else 'OFF'}")


def main() -> None:
    asyncio.run(run())
