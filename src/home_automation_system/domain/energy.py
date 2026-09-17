from typing import Any, Literal


PowerReadingStatus = Literal["measured", "no_data"]


def add_power_reading_status(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Annotate power entries without changing their measured values."""
    annotated_entries: list[dict[str, Any]] = []
    for entry in entries:
        power: Any = entry.get("power")
        status: PowerReadingStatus = "no_data" if power is None else "measured"
        annotated_entry: dict[str, Any] = {**entry, "status": status}
        annotated_entries.append(annotated_entry)
    return annotated_entries
