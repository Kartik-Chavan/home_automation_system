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
COHERE_API_KEY=your-cohere-api-key
COHERE_MODEL=command-a-03-2025
TAPO_DEVICE_NAME=your-device-name
TAILSCALE_AUTH_ENABLED=true
TAILSCALE_ALLOWED_USERS=owner-tailscale-login@example.com,family-tailscale-login@example.com
```

Set `TAPO_DEVICE_IP` to the current local IP shown in the Tapo app.
`TAPO_DEVICE_NAME` is an optional friendly label. The emails in
`TAILSCALE_ALLOWED_USERS` must be the members' Tailscale account login emails,
not their Tapo or GitHub emails.

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

## Add Family Members to Tailscale

1. In the Tailscale admin console, invite each family member to your tailnet.
2. Each person accepts the invitation and signs in to Tailscale on their own
	phone or computer. Do not share one Tailscale login.
3. Copy each person's Tailscale account login email from the admin console.
4. Add those emails to `.env`, separated by commas:

	```env
	TAILSCALE_AUTH_ENABLED=true
	TAILSCALE_ALLOWED_USERS=owner@example.com,family-member@example.com
	```

5. Restart `home-automation-api` after changing `.env`.
6. Each invited member opens the private HTTPS URL shown by `tailscale serve status`.
	They can use the browser's **Add to Home Screen** feature.

The email is supplied by the member's Tailscale account. The member does not
enter it into the Home Automation UI. Tailscale Serve forwards the verified
identity to FastAPI; the API checks it on every request. The API listens on
`127.0.0.1:8000` by default, so the UI and API are reachable through Serve but
the API port is not directly exposed to the LAN or tailnet. Do not use Funnel
for this family UI.

## Run on the Windows Laptop

Keep Tailscale installed and signed in on the laptop. From PowerShell:

```powershell
cd "C:\Drive F\Code\Project\home_automation_system"
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e .
```

Create or update `.env` with the required Tapo and Cohere values, then set:

```env
TAILSCALE_AUTH_ENABLED=true
TAILSCALE_ALLOWED_USERS=owner@example.com,family-member@example.com
```

Start the application and leave this terminal open:

```powershell
home-automation-api
```

In another PowerShell window, enable private Serve and read its URL:

```powershell
tailscale serve --bg 8000
tailscale serve status
```

Open the HTTPS `.ts.net` URL reported by `serve status`. The UI is served by
FastAPI at `/`; it is not a separate process. Local development without the
Tailscale identity check is available by setting `TAILSCALE_AUTH_ENABLED=false`
in `.env` and restarting the API. Do not use that setting for remote access.

## Run on the Android 7 Phone (Termux)

The phone uses the existing Termux/proot Tailscale binaries because Android 7
cannot run the current Tailscale Android app. Keep the phone plugged in, set
Termux battery usage to **Unrestricted**, and run `termux-wake-lock` before
entering the chroot. Use separate Termux sessions for the daemon, API, and
Serve proxy.

### Session A: start Tailscale

In Termux:

```sh
termux-wake-lock
termux-chroot
```

Inside the chroot:

```sh
export SSL_CERT_FILE=/data/data/com.termux/files/usr/etc/tls/cert.pem
cd ~/home_automation_system/tailscale_1.102.4_arm64
./tailscaled --tun=userspace-networking --statedir=$HOME/.tailscale --socket=$HOME/.tailscale/ts.sock &
./tailscale --socket=$HOME/.tailscale/ts.sock status
```

If `status` says the node is logged out, run
`./tailscale --socket=$HOME/.tailscale/ts.sock up` and complete its login
flow. Do not start a second `tailscaled` if one is already running.

### Session B: start the app and API

For a new phone installation only, install the project into its existing
Termux virtual environment. The `tapo` package must already be installed in a
form compatible with this Android/Termux environment; do not recreate a
working phone environment just to update the project:

```sh
termux-chroot
cd ~/home_automation_system
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

For normal startup, open another Termux session, enter the chroot, then run:

```sh
termux-chroot
cd ~/home_automation_system
source .venv/bin/activate
home-automation-api
```

Keep this process running. It reads Tapo/Cohere credentials and the family
allow-list from the project's `.env` file.

### Session C: start private Serve

Open a third Termux session, enter the chroot, then run:

```sh
termux-chroot
cd ~/home_automation_system/tailscale_1.102.4_arm64
./tailscale --socket=$HOME/.tailscale/ts.sock serve --bg 8000
./tailscale --socket=$HOME/.tailscale/ts.sock serve status
```

Open the HTTPS `.ts.net` URL shown in the output from a family device signed
into the same tailnet. This is `tailscale serve`, not `funnel`; the family UI
must remain tailnet-only.

To stop the API, press `Ctrl+C` in Session B. To release the Android wake lock
when shutting down, run `termux-wake-unlock` outside the chroot. After changing
`.env` or application code, restart Session B. `tailscale serve` configuration
persists, but the phone, Termux, Tailscale daemon, and API process must remain
awake and running for the page to be available.

## Watchdog and Logs

In a separate terminal/session, run:

```powershell
python .\scripts\health_monitor.py
```

The monitor checks process health, device info, and device status and writes
JSONL records to `logs/health/checks.jsonl`. Application operation audits are
in `logs/audit/events.jsonl`; persistent agent conversations are stored under
`logs/agent/`. On Android, the same command can be run using the active venv
Python (`python scripts/health_monitor.py`).

The current backend connects one configured P110. `TAPO_DEVICE_NAMES` does
not itself configure multiple plugs; multi-device support requires adding
device entries to the backend.

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

