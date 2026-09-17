"""
Round 2 — live device tests against the confirmed tapo v0.9.0 API.

Drop this into the project root (next to main.py and .env) and run with the
venv's python. It exercises every method confirmed in Round 1, prints the
result of each, and writes everything to round2_results.json so we have a
saved record even if you're not copy-pasting terminal output back.

Usage (PowerShell, from the project root, venv already active):
    .venv\\Scripts\\python.exe .\\round3_test.py
"""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
import os

from tapo import ApiClient
from tapo.requests import EnergyDataInterval, PowerDataInterval

from home_automation_system.domain.energy import add_power_reading_status


def to_serializable(obj):
    """Best-effort conversion of .to_dict() output (and any stray non-JSON
    types like datetimes) into something json.dump can handle."""
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    return json.loads(json.dumps(obj, default=str))


def annotate_power_data(data):
    """Preserve raw power values and label missing readings explicitly."""
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return data
    data["entries"] = add_power_reading_status(data["entries"])
    return data


async def safe_call(label, coro):
    """Run one API call, print + capture the result, and don't let a single
    failure kill the rest of the script."""
    print(f"\n--- {label} ---")
    try:
        result = await coro
        data = to_serializable(result)
        if label == "get_power_data (Hourly, last 24h)":
            data = annotate_power_data(data)
        print(json.dumps(data, indent=2))
        return {"ok": True, "data": data}
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def main():
    load_dotenv()
    username = os.getenv("TAPO_USERNAME")
    password = os.getenv("TAPO_PASSWORD")
    device_ip = os.getenv("TAPO_DEVICE_IP")

    if not all([username, password, device_ip]):
        raise SystemExit("Missing TAPO_USERNAME / TAPO_PASSWORD / TAPO_DEVICE_IP in .env")

    client = ApiClient(username, password)
    device = await client.p110(device_ip)

    results = {}

    # --- Enum inspection first, so we know real values instead of guessing ---
    print("EnergyDataInterval members:", [m for m in dir(EnergyDataInterval) if not m.startswith("_")])
    print("PowerDataInterval members:", [m for m in dir(PowerDataInterval) if not m.startswith("_")])

    # --- Read-only calls (safe, no state change) ---
    results["device_info"] = await safe_call("get_device_info", device.get_device_info())
    results["device_usage"] = await safe_call("get_device_usage", device.get_device_usage())
    results["current_power"] = await safe_call("get_current_power", device.get_current_power())
    results["energy_usage"] = await safe_call("get_energy_usage", device.get_energy_usage())

    # --- Energy data over the last 7 days ---
    today = date.today()
    week_ago = today - timedelta(days=7)
    results["energy_data_daily"] = await safe_call(
        "get_energy_data (Daily, last 7 days)",
        device.get_energy_data(EnergyDataInterval.Daily, week_ago, today),
    )

    # --- Power data over the last 24 hours ---
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    results["power_data_hourly"] = await safe_call(
        "get_power_data (Hourly, last 24h)",
        device.get_power_data(PowerDataInterval.Hourly, day_ago, now),
    )

    # --- State-changing calls, done last and deliberately ---
    print("\n--- toggling device on() then off() to confirm control still works ---")
    results["on"] = await safe_call("on", device.on())
    await asyncio.sleep(2)
    results["off"] = await safe_call("off", device.off())

    out_path = Path(__file__).parent / "round3_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nSaved full results to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())