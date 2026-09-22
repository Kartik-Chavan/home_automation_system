from datetime import datetime, timedelta, timezone

import pytest

from home_automation_system.services.p110_service import P110Service


class FakeEnergyPlug:
    """Minimal energy plug double for timestamp contract tests."""

    def __init__(self) -> None:
        self.received_range: tuple[datetime, datetime] | None = None

    async def get_state(self) -> object:
        """Return an unused state placeholder."""
        raise NotImplementedError

    async def turn_on(self) -> None:
        """Return an unused control placeholder."""
        raise NotImplementedError

    async def turn_off(self) -> None:
        """Return an unused control placeholder."""
        raise NotImplementedError

    async def get_power_data(
        self, start_datetime: datetime, end_datetime: datetime
    ) -> dict[str, list[dict[str, int]]]:
        """Capture the normalized timestamp range."""
        self.received_range = (start_datetime, end_datetime)
        return {"entries": [{"power": 0}]}


@pytest.mark.asyncio
async def test_power_data_normalizes_offset_aware_timestamps_to_utc() -> None:
    """Tapo receives exact timezone.utc values regardless of input offset."""
    plug: FakeEnergyPlug = FakeEnergyPlug()
    service: P110Service = P110Service(plug)  # type: ignore[arg-type]
    input_timezone = timezone(timedelta(hours=5, minutes=30))

    await service.get_power_data(
        datetime(2026, 9, 18, 10, 0, tzinfo=input_timezone),
        datetime(2026, 9, 18, 11, 0, tzinfo=input_timezone),
    )

    assert plug.received_range is not None
    start_datetime, end_datetime = plug.received_range
    assert start_datetime.tzinfo is timezone.utc
    assert end_datetime.tzinfo is timezone.utc
    assert start_datetime.hour == 4
    assert end_datetime.hour == 5


@pytest.mark.asyncio
async def test_power_data_rejects_naive_timestamps() -> None:
    """Reject timestamps whose timezone cannot be determined safely."""
    plug: FakeEnergyPlug = FakeEnergyPlug()
    service: P110Service = P110Service(plug)  # type: ignore[arg-type]
    timestamp: datetime = datetime(2026, 9, 18, 10, 0)

    with pytest.raises(ValueError, match="must include a timezone"):
        await service.get_power_data(timestamp, timestamp)