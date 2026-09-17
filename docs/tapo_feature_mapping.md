# Tapo P110 — App Features vs. `tapo` Python Library

Analysis of which features from the official Tapo app are available through the
community `tapo` Python library (local KLAP control), and which we'll need to
build ourselves.

## Feature mapping

| App feature | `tapo` library status | Verdict | Priority 
|---|---|---|---|
| ON/OFF toggle | `device.on()` / `device.off()` — confirmed, already used in `main.py` | ✅ Have it | High
| Device status/info | `device.get_device_info()` — confirmed in official example | ✅ Have it | High
| Current power (W) | `device.get_current_power()` — confirmed | ✅ Have it | High
| Today/monthly runtime & usage | `device.get_device_usage()` — confirmed | ✅ Have it | Medium 
| Energy usage (kWh totals) | `device.get_energy_usage()` — confirmed | ✅ Have it | Medium
| Historical energy data (hourly/daily/monthly graphs) | `device.get_energy_data(EnergyDataInterval, date)` — confirmed | ✅ Have it | Medium
| Historical power data (5-min/hourly graphs) | `device.get_power_data(PowerDataInterval, start, end)` — confirmed | ✅ Have it | Medium
| Auto-discover devices on LAN | `client.discover_devices()` — confirmed (added v0.8.5) | ✅ Have it, useful if IP changes | High
| **Schedule** (time / sunrise-sunset rules) | No method for this appears anywhere in the README, CHANGELOG, or example files | ❌ Not exposed — build ourselves (cron / Task Scheduler calling `device.on()`/`off()` at set times) | High
| **Timer** (countdown auto-off) | Same — no library method found | ❌ Build ourselves (simple sleep-then-toggle, or scheduled job) | Medium
| **Device Sharing** (multi-user) | Not applicable to a local-control library at all — that's a cloud/account feature | ❌ Out of scope; irrelevant to our remote-control goal anyway | Medium


## Summary

The library gives us **ON/OFF + full energy monitoring + discovery for free**.

Everything time-based (Schedule, Timer) has to be built in our own
Flask app using plain Python timing logic. (tech stack for app need to decided) The plug can execute those rules
locally on its own firmware once configured through the official app, but
nobody has reverse-engineered the local protocol call that *pushes* that
configuration — so the community library doesn't expose a way to set them
programmatically.

## Source

Confirmed against the library's own repo (README, CHANGELOG, and the official
`tapo_p110.py` example): https://github.com/mihai-dinculescu/tapo

---
*Rounds of confirmation scripts (offline package introspection, then live
device tests) to be appended here once run.*
---
 
## Round 1 — offline package introspection (confirmed)
 
Ran against the actual installed package, `tapo` **v0.9.0**, in the project venv.
 
Directory listing of `.venv\Lib\site-packages\tapo\` shows the full set of `.pyi`
type-stub files shipped with the package — this is the real API surface, not
just what's in the README:
 
```
api_client.pyi
camera_ptz_handler.pyi
color_light_handler.pyi
debug_ext.pyi
device_discovery.pyi
device_discovery_raw.pyi
device_management_ext.pyi
device_type.pyi
discovery_raw_result.pyi
discovery_result.pyi
hub_handler.pyi
ke100_handler.pyi
light_handler.pyi
on_off_ext.pyi
plug_energy_monitoring_handler.pyi
plug_handler.pyi
power_strip_energy_monitoring_handler.pyi
power_strip_handler.pyi
power_strip_plug_energy_monitoring_handler.pyi
power_strip_plug_handler.pyi
refresh_session_ext.pyi
rgbic_light_strip_handler.pyi
rgb_light_strip_handler.pyi
s200_handler.pyi
s210_handler.pyi
t100_handler.pyi
t110_handler.pyi
t300_handler.pyi
t31x_handler.pyi
to_dict_ext.pyi
__init__.pyi
```
 
**Finding:** confirms the table above directly from the installed package —
there is no `schedule`, `timer`, `away_mode`, or `sharing` handler/stub file
anywhere in the package. Schedule/Timer/Away Mode/Device Sharing are not a
documentation gap; they are simply not implemented in this library at the
package level, for any supported device type (not just the P110).
 
### Confirmed exact P110 API (`PlugEnergyMonitoringHandler`, from `plug_energy_monitoring_handler.pyi`)
 
The P110 uses `PlugEnergyMonitoringHandler`, not `PlugHandler` (that one's for
the P100/P105, which have no energy monitoring). Full method list, straight
from the installed v0.9.0 stub file:
 
```python
class PlugEnergyMonitoringHandler(OnOffExt, DeviceManagementExt, RefreshSessionExt, DebugExt):
 
    async def on(self) -> None
    async def off(self) -> None
 
    async def get_device_info(self) -> DeviceInfoPlugEnergyMonitoringResult
    async def get_device_usage(self) -> DeviceUsageEnergyMonitoringResult
    async def get_current_power(self) -> CurrentPowerResult
    async def get_energy_usage(self) -> EnergyUsageResult
 
    async def get_energy_data(
        self,
        interval: EnergyDataInterval,
        start_date: date,
        end_date: Optional[date] = None,
    ) -> EnergyDataResult
 
    async def get_power_data(
        self,
        interval: PowerDataInterval,
        start_date_time: datetime,
        end_date_time: datetime,
    ) -> PowerDataResult
```


## Round 2 — live device tests (confirmed)
 
Ran against the actual P110 via `round2_test.py`.
 
| Method | Result | Notes |
|---|---|---|
| `get_device_info()` | ✅ | Full device metadata, firmware/hardware information, WiFi signal, region, nickname, and on/off state |
| `get_device_usage()` | ✅ | `power_usage`/`saved_power`/`time_usage`, each split `today`/`past7`/`past30`. `past7` == `past30` currently — plug likely has under 30 days of history so far |
| `get_current_power()` | ✅ | `0` — correctly matches `device_on: false` at the time of the run |
| `get_energy_usage()` | ✅ | Today/month totals + `electricity_charge` (all `0` currently) + local time |
| `get_energy_data(Daily, ...)` | ✅ | Returns per-day entries, `interval_length: 1440` (minutes/day), UTC timestamps. **Anomaly:** returned entries out to `2026-12-08`, ~90 days past the requested window, and two *future* dates (Sept 21–22) show nonzero energy while everything else is `0`. Looks like `start_date`/`end_date` may not constrain the returned range the way the signature implies — needs a follow-up test with a tighter window to confirm |
| `get_power_data(Hourly, ...)` | ✅ after fix | Requires **timezone-aware** `datetime` objects. `datetime.now(timezone.utc)` works; a naive `datetime.now()` raises a `TypeError`. |
| `on()` | ✅ | Returns `None` as the stub signature indicated |
| `off()` | ✅ | Returns `None` as the stub signature indicated |
 
**Net result:** all tested methods work with the correct argument types and
return usable data. Historical date-window behavior still needs client-side
filtering if an exact dashboard range is required.
 
## Round 3 — retested power history and verified edge cases

The live test uses timezone-aware datetimes and succeeds. Device-identifying
values are intentionally omitted from this document.

| Method | Result | Notes |
|---|---|---|
| `get_power_data(Hourly, ...)` | ✅ | The timezone-aware datetime fix resolves the original `TypeError`. |
| `get_energy_data(Daily, ...)` | ✅ | The API returns energy entries, but the returned window may be broader than the requested dates. |

### Power reading null handling

The API can return both of these values:

```json
{"power": 0, "status": "measured"}
{"power": null, "status": "no_data"}
```

These meanings remain separate. `0` is a measured zero-watt reading; `null`
means no reading was recorded for that interval. The
`add_power_reading_status` helper preserves the raw `power` value and adds the
explicit status for dashboards or API responses.

The date-window behavior is reproducible: the API may return a cached window
that does not exactly match the requested bounds. Future dashboard code should
filter returned entries client-side when exact date ranges are required.

---

## Decision — multi-platform access & scheduling architecture
 
### Multi-platform access
 
FastAPI has no concept of "platform support" — it's just an HTTP backend.
Any device on the Tailscale tailnet (laptop, phone, anyone else's phone) can
reach the same URL, regardless of OS.
 
- **Chosen:** serve a responsive webpage (HTML + light JS) from FastAPI,
  reached over Tailscale. Works on any device's browser, nothing to install
  beyond Tailscale itself, no app store involved.
- **Rejected:** a true native/PWA app calling FastAPI as a JSON API — real
  extra frontend effort, not justified for a personal home project. The
  future phone's role is to be the **server** (running the script), not the
  client, so a native client app isn't needed anyway.
### Scheduling (Schedule / Timer features)
 
Three options considered:
 
| Option | Verdict | Why |
|---|---|---|
| Third-party cloud scheduler (AWS Lambda + EventBridge, etc.) | ❌ Rejected | Runs outside Tailscale entirely — would require exposing something publicly (the exact thing ruled out when Cloudflare Tunnel was dropped) or bolting Tailscale into the cloud function. Reintroduces an internet dependency for something that currently works offline |
| OS-level scheduler (Windows Task Scheduler now, `cron` on Termux later) | ❌ Rejected | Platform-specific config — different mechanism per OS, meaning the laptop→phone migration would require re-engineering the scheduling integration from scratch |
| **App-owned events: SQLite storage + `APScheduler` running inside the FastAPI process** | ✅ **Chosen** | The always-on device already has to run a continuous Python process for the web UI to work — costs nothing extra to hold a scheduler in memory too. SQLite is a single portable file (just copy it when migrating laptop → phone). Same Python code runs identically on Windows now and Termux later — no per-platform scheduler config, no cloud dependency, fully internet-independent |
 
**Net decision:** SQLite for storing schedule/timer entries, `APScheduler`
for executing them, both living inside the same FastAPI process that serves
the web UI. Matches the local-control, no-internet-dependency principle
already established by the Tailscale-over-tunnel decision.