# MCP Host Deployment Plan

This document separates the current Windows deployment from the later Android-phone deployment.

## Decision

Implement and verify the MCP server on Windows first. Only after the Windows path is reliable should the same service be moved to a spare Android phone.

The phone is the preferred long-term bridge because it can use mobile data when the home ISP is down. A Raspberry Pi or other always-on device is not required for this plan.

## Phase 1: Windows Validation

The Windows host must provide:

- The Tapo P110 reachable over the home Wi-Fi LAN.
- The `.venv` containing the installed application and `fastmcp` dependency.
- The MCP server listening on `127.0.0.1:8765`.
- Tailscale connected and Funnel exposing only port `8765`.
- GitHub OAuth variables and Tapo credentials loaded from `.env`.

### Interactive test

From the project directory with the virtual environment activated:

```powershell
Set-Location "C:\Drive F\Code\Project\home_automation_system"
.\.venv\Scripts\Activate.ps1
home-automation-mcp
```

In a second PowerShell window, keep only the MCP port exposed:

```powershell
& "C:\Program Files\Tailscale\tailscale.exe" funnel --bg 8765
& "C:\Program Files\Tailscale\tailscale.exe" funnel status
```

The remote MCP URL is:

```text
https://<tailnet-hostname>/mcp
```

### Windows acceptance checklist

1. Connect the AI client to the remote `/mcp` URL.
2. Complete GitHub OAuth with the allow-listed account.
3. Call `get_plug_status`.
4. Call `get_energy_usage`.
5. Call `get_power_data` with explicit UTC timestamps.
6. Turn the plug on and off only during a deliberate control test.
7. Stop and restart the MCP process, then reconnect the AI client and verify a new session.
8. Leave the laptop idle long enough to exercise Tapo session refresh.
9. Confirm that only port `8765` is exposed through Tailscale.

### Windows unattended operation

The interactive terminal process stops when its terminal closes or Windows shuts down. For unattended operation, install the MCP executable as a Windows service using NSSM and configure:

```text
Application:
C:\Drive F\Code\Project\home_automation_system\.venv\Scripts\home-automation-mcp.exe

Application directory:
C:\Drive F\Code\Project\home_automation_system

Startup:
Automatic

Exit action:
Restart the application
```

Start it with:

```powershell
nssm start home-automation-mcp
```

Tailscale runs as its own Windows service. The laptop must remain powered on, connected to the internet, and able to reach the plug over the local LAN.

## Phase 2: Android Phone Bridge

After Windows is verified, run the MCP server on a spare Android phone using Termux. The phone must have:

- Termux installed from a trusted source.
- Termux:Boot installed for boot-time startup.
- Tailscale installed and signed into the same tailnet.
- Python, the project, and its dependencies installed in Termux.
- Mobile data enabled as a fallback path.

### Keep the Android process alive

Android does not have a Windows service. Use the following protections:

1. Install Termux:Boot and start the MCP process from a boot script.
2. Use a watchdog loop or `termux-services` to relaunch the process if it exits.
3. Set Termux battery usage to **Unrestricted**.
4. Run `termux-wake-lock` while the bridge is active.
5. Allow Termux unrestricted background data.
6. Keep Wi-Fi enabled during sleep where the Android vendor provides that setting.
7. Exempt both Termux and Tailscale from the vendor's battery and background-process restrictions.
8. Allow Tailscale to run as an always-on VPN.

The exact Android settings vary by vendor. Xiaomi, Oppo, Vivo, and similar OEMs may require additional autostart and battery-whitelist settings.

### Android boot script outline

Create a Termux:Boot script that starts the MCP process through a watchdog. The final paths and commands must be verified on the target phone before production use.

```sh
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd "$HOME/home_automation_system" || exit 1

while true; do
  "$HOME/home_automation_system/.venv/bin/home-automation-mcp"
  sleep 5
done
```

Do not copy Windows paths or PowerShell commands into Termux. The Android deployment must use Linux paths and a Termux-compatible Python environment.

## Internet and LAN failure boundary

The phone provides a useful fallback when the home ISP or internet connection is down:

```text
AI client -> mobile data -> Tailscale -> phone -> home Wi-Fi LAN -> Tapo P110
```

This works only if the phone can still reach the home LAN. Mobile data does **not** solve a failed home router or failed home Wi-Fi network:

- ISP/internet outage: the phone can use mobile data, while the phone-to-plug LAN path remains available.
- Home router or Wi-Fi outage: the phone cannot reach the plug's local IP, even if mobile data and Tailscale are working.
- Phone battery dead or Android kills Termux: the MCP bridge is unavailable.

For the local-LAN failure case, the practical remedies are restoring the router, using a UPS, or providing backup networking. This is outside the MCP software layer.

## Troubleshooting

A `POST /mcp 400 Bad Request` followed by `Terminating session` usually indicates that the AI client reused a Streamable HTTP session that the server no longer has. Reconnect or remove and re-add the MCP connector so it creates a fresh session.

A Tapo `SESSION_TIMEOUT` is different. The application refreshes the Tapo session and retries the operation once.

Do not add a manual bearer token to the MCP client. GitHub OAuth and the server-side numeric GitHub user ID allow-list provide MCP authorization.
