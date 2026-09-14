# Tapo P110 Toggle

Small Python program that reads the current state of a local Tapo P110 plug and toggles it. It is intended for a plug controlling a laptop charger.

## Requirements

- Python 3.11 or newer
- Tapo P110 on the same local network as the computer
- Tapo account email and password
- The `tapo` Python package

## Setup on Windows PowerShell

```powershell
cd "C:\Drive F\Code\Project\home_automation_system"

py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Open `.env` and fill in your credentials:

```powershell
notepad .env
```

The `.env` variables are:

```text
TAPO_USERNAME=your-tapo-email@example.com
TAPO_PASSWORD=your-tapo-password
TAPO_DEVICE_IP=192.168.1.37
```

The current P110 address found during setup was `192.168.1.37`.

## Run

```powershell
.\.venv\Scripts\python.exe .\main.py
```

The program prints the initial state, changes it once, and prints the resulting state:

```text
Initial state: OFF
Turning plug on...
Current state: ON
```

Run it again to toggle the plug back off.

## Network behavior

This program uses direct local control. It does not require internet access after the Python package and credentials are configured, but the computer must be able to reach the P110 on the same home network or through a VPN into that network. It will not work from an unrelated Wi-Fi or mobile network using a private address such as `192.168.1.37`.

The P110 receives its local IP address from the Wi-Fi router's DHCP service. The address changed from `192.168.1.33` to `192.168.1.37` after reconnecting. If the script times out, check the Tapo app under **Device Info** and update `TAPO_DEVICE_IP`. For a stable address, create a DHCP reservation in the router for the P110 MAC address.

## Charger note

The script toggles power immediately. Use it only with a charger and device setup where removing power is safe. Frequent hard power cuts can interrupt operating-system updates or file writes. A smart plug timer or controlled shutdown may be safer for a laptop.

## Security

- `.env` is ignored by Git and must never be committed.
- Do not commit or share the Tapo password.
- Do not expose the P110's local control port directly to the internet.
