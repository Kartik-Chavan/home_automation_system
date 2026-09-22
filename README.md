# Home Automation System

Layered home automation service for local Tapo devices. The current use case reads and toggles a Tapo P110 plug connected to a laptop charger.

## Requirements

- Python 3.11 or newer
- Tapo P110 on the same local network as the computer
- Tapo account email and password
- The `tapo` Python package

## Project structure

```text
src/home_automation_system/
	adapters/       External device integrations, currently Tapo
	api/            Future HTTP/backend integration boundary
	domain/         Device contracts and state models
	infrastructure/ Future persistence and scheduling integrations
	mcp/            Future MCP server integration boundary
	services/       Application use cases
	app.py          Dependency composition
	cli.py          Command-line entrypoint
tests/            Unit tests without physical-device access
```

## Setup on Windows PowerShell

```powershell
cd "C:\Drive F\Code\Project\home_automation_system"

py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

For development and tests, also install:

```powershell
python -m pip install -r requirements-dev.txt
```

Open `.env` and fill in your credentials:

```powershell
notepad .env
```

The `.env` variables are:

```text
TAPO_USERNAME=your-tapo-email@example.com
TAPO_PASSWORD=your-tapo-password
TAPO_DEVICE_IP=your-device-ip
```

Set `TAPO_DEVICE_IP` to the current local IP shown in the Tapo app.

## Run

```powershell
python -m home_automation_system
```

The compatibility command also remains available from the project root:

```powershell
python .\main.py
```

The program prints the initial state, changes it once, and prints the resulting state:

```text
Initial state: OFF
Turning plug on...
Current state: ON
```

Run it again to toggle the plug back off.

Run the unit tests without contacting the physical plug:

```powershell
python -m pytest
```

## Run MCP unattended on Windows

The interactive `home-automation-mcp` command stops when its terminal closes.
For unattended operation, run it as a Windows service with automatic restart.
Tailscale runs separately as a Windows service, and its Funnel configuration
persists across restarts.

Using NSSM, install the service from an elevated PowerShell window:

```powershell
nssm install home-automation-mcp `
	"C:\Drive F\Code\Project\home_automation_system\.venv\Scripts\home-automation-mcp.exe"
```

Configure the service with these values in NSSM:

```text
Application directory:
C:\Drive F\Code\Project\home_automation_system

Startup type:
Automatic

Restart action:
Restart the application
```

Start it with:

```powershell
nssm start home-automation-mcp
```

Keep the MCP server's `.env` file in the application directory. It must
contain the Tapo credentials, GitHub OAuth values, numeric GitHub allow-list
ID, public MCP URL, and signing key described in
`docs/remote_access_architecture.md`.

Expose only the MCP port through Tailscale:

```powershell
tailscale funnel --bg 8765
```

Do not expose the web application's port. If the MCP process restarts, an AI
client may need to reconnect because Streamable HTTP session state is held in
memory. Tapo session expiry is handled automatically by refreshing the Tapo
session and retrying the operation once. The host computer must remain powered
on, connected to the internet, and able to reach the P110 over the local LAN.

## Network behavior

This program uses direct local control. It does not require internet access after the Python package and credentials are configured, but the computer must be able to reach the P110 on the same home network or through a VPN into that network. It will not work from an unrelated Wi-Fi or mobile network using a private address.

The P110 receives its local IP address from the Wi-Fi router's DHCP service. Its address changed after reconnecting. If the script times out, check the Tapo app under **Device Info** and update `TAPO_DEVICE_IP`. For a stable address, create a DHCP reservation in the router for the P110 MAC address.

## Prevent IP Changes

An internet outage can restart the router and reset its DHCP lease table. The router may then assign the P110 a different local IP address. This does not mean the P110 or Tapo changed it.

On the DIGISOL router:

1. Open your router's admin address and sign in.
2. Open **DHCP Settings** or **LAN Setup**.
3. Choose **Address Reservation**, **Static Lease**, or **Edit Reserved IP Address**.
4. Reserve the desired local IP for the P110's MAC address shown in the Tapo app.
5. Save and reconnect the P110 if needed.

Use the MAC address format required by your router. A DHCP reservation is safe, affects only this device, and can be removed later without changing other network settings.

