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
