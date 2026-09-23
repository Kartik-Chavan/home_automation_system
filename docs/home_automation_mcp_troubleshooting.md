# Home Automation MCP — Setup, Startup & Troubleshooting Guide

This doc combines the day-to-day startup procedure with a record of the real
issues hit while moving this server from a laptop to the Xiaomi Mi Max Prime
(Termux) bridge, and their fixes. Keep this in the repo so any future
migration (new phone, new Tailscale hostname, rotated secrets) can be done
by following the checklist instead of re-debugging from scratch.

---

## 1. Architecture

```
Session A: tailscaled (userspace networking, no root/TUN)
        |
Session B: tailscale funnel  →  https://<hostname>.taila5d7e1.ts.net
        |  proxies to
        v
Session C: home-automation-mcp  →  http://127.0.0.1:8765/mcp
        |
Session D: curl test  →  confirms 401 Unauthorized (expected pre-auth)
```

Auth path: Claude → GitHub OAuth (via `GitHubProvider`) → allow-listed GitHub
user ID → JWT issued by the MCP server → Claude calls `/mcp` with the token.

---

## 2. Why Termux + userspace networking (not the Tailscale Android app)

The phone runs **Android 7.0**. Current Tailscale Android app releases
require **Android 8.0+**, so the Play Store app is not an option here.

**Fix used:** run the `tailscale`/`tailscaled` Linux binaries directly
inside Termux with `--tun=userspace-networking`. This needs no root and no
TUN device, so it sidesteps the Android version requirement entirely (same
trick used to run Tailscale on old Kindles/Kobos). `tailscale serve`/`funnel`
work fine in this mode since they're implemented as an HTTP layer inside
`tailscaled`, not through the kernel TUN interface.

Binaries: `pkgs.tailscale.com/stable/tailscale_latest_arm64.tgz` — no Play
Store auto-update, so re-download periodically.

---

## 3. Startup procedure (4 sessions)

### Step 0 — Safety check (avoid duplicate processes)
```sh
ps -ef | grep -E '[t]ailscaled|[h]ome-automation-mcp'
# kill <PID>   (or kill -9 <PID> if it won't stop)
```

### Session A — Tailscale daemon
```sh
termux-chroot
export SSL_CERT_FILE=/data/data/com.termux/files/usr/etc/tls/cert.pem
cd ~/home_automation_system/tailscale_1.102.4_arm64
./tailscaled --tun=userspace-networking --statedir=$HOME/.tailscale --socket=$HOME/.tailscale/ts.sock &
./tailscale --socket=$HOME/.tailscale/ts.sock status
```
Leave running. Also run `termux-wake-lock` and set Termux's battery mode to
**Unrestricted** (Android Settings → Apps → Termux → Battery) — otherwise
MIUI suspends the process on screen lock, causing periodic
`time jump detected... probably wake from sleep` reconnects in the log.

### Session B — Funnel
```sh
cd ~/home_automation_system/tailscale_1.102.4_arm64
./tailscale --socket=$HOME/.tailscale/ts.sock funnel --bg 8765
./tailscale --socket=$HOME/.tailscale/ts.sock funnel status
```
> CLI syntax changed in Tailscale 1.52+: it's `funnel --bg <port>`, **not**
> `funnel <port> on`. The old `on`/`off` suffix form errors out.

Expected: `https://<hostname>.taila5d7e1.ts.net (Funnel on)`

### Session C — MCP server
```sh
cd ~/home_automation_system
source .venv/bin/activate
home-automation-mcp
```
Confirm: `Uvicorn running on http://127.0.0.1:8765`

### Session D — Test
```sh
curl -sI https://<hostname>.taila5d7e1.ts.net/mcp
```
Expected: `HTTP/2 401` — this means reachable + auth required. **401 here is
correct and good**, not a failure.

---

## 4. Known issue: changing the phone's hostname breaks OAuth login

This was the big one — cost the most time. Record of cause and fix:

### Symptom
- Claude connector flow fails with **"Invalid Redirect URI" (from github.com)**
  or **"Invalid or expired consent token"** (from our own server).
- Server log shows the `/authorize` → `/consent` → `302 Found` sequence
  completing, but the browser lands on an error page anyway.

### Root cause (two separate issues, both hostname-related)
1. **Tailscale's auto-generated hostname is unstable.** Termux can't report
   a real device name to the coordination server, so it defaults to
   `localhost-0`, `localhost-1`, etc. Every time the node re-registers, this
   can change — silently breaking any URL that was hardcoded elsewhere.
2. **Funnel doesn't follow a hostname rename automatically.** Running
   `tailscale set --hostname=<new>` updates `tailscale status`, but Funnel
   keeps serving under the *old* name until it's explicitly reset.
3. **GitHub OAuth App's Redirect URI is a separate field from Homepage URL.**
   Homepage URL is just informational — changing it does nothing for auth.
   Only the **Redirect URIs** list is checked against what the server sends.
   It's easy to update Homepage URL, assume you're done, and leave the real
   Redirect URI pointing at a stale hostname (e.g. an old laptop's).

### Fix checklist (do all of these together, in order, whenever the
hostname changes — new phone, factory reset, re-registered node, etc.)

1. **Set a stable, explicit hostname** (don't rely on the auto-generated one):
   ```sh
   ./tailscale --socket=$HOME/.tailscale/ts.sock set --hostname=home-automation-bridge
   ```
2. **Reset and re-enable Funnel under the new name:**
   ```sh
   ./tailscale --socket=$HOME/.tailscale/ts.sock funnel reset
   ./tailscale --socket=$HOME/.tailscale/ts.sock funnel --bg 8765
   ./tailscale --socket=$HOME/.tailscale/ts.sock funnel status
   ```
3. **Update `.env`:**
   ```
   MCP_PUBLIC_BASE_URL=https://home-automation-bridge.taila5d7e1.ts.net
   ```
4. **Update the GitHub OAuth App's Redirect URI** (Settings → Developer
   settings → OAuth Apps → *this app*) — delete the stale one, add:
   ```
   https://home-automation-bridge.taila5d7e1.ts.net/auth/callback
   ```
   Click **Update application** to save. (Homepage URL can be updated too,
   but it has no functional effect — don't rely on it as the fix.)
5. **Restart the MCP server** so it picks up the new `.env`.
6. **Remove and re-add the connector in Claude**, rather than retrying an
   old browser tab/session — a stale tab can resubmit an already-used
   `consent` token, producing "Invalid or expired consent token" even after
   everything above is fixed.
7. **Verify:** `curl -sI https://home-automation-bridge.taila5d7e1.ts.net/mcp`
   → expect `401`, then retry the Claude connector flow.

### Note on duplicate/expired consent tokens
Each page load of `/authorize` mints a fresh one-time `txn_id`. Reloading
the consent page, going back, or double-tapping "Allow" submits an
already-invalidated token and produces `400 Bad Request` in the log plus
"Invalid or expired consent token" in the browser — even when an earlier
attempt in the same batch actually succeeded. If the log shows a `302 Found`
followed later by a successful `POST .../oauth/access_token` from GitHub,
the flow likely completed — check Claude's connector status directly rather
than trusting a stale browser tab's error page.

---

## 5. `.env` hygiene

Keep exactly one instance of each key — duplicate keys (e.g.
`MCP_PUBLIC_BASE_URL` or `MCP_JWT_SIGNING_KEY` set twice) are fragile since
most loaders silently take whichever is parsed last.

```env
TAPO_USERNAME=...
TAPO_PASSWORD=...
TAPO_DEVICE_IP=192.168.1.33

GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
ALLOWED_GITHUB_USER_ID=...
MCP_JWT_SIGNING_KEY=...
MCP_PUBLIC_BASE_URL=https://home-automation-bridge.taila5d7e1.ts.net
MCP_HOST=127.0.0.1
MCP_PORT=8765
```

**If any of these values were ever pasted into a screenshot, log, or chat,
rotate them:**
- GitHub OAuth App → Generate a new client secret
- New JWT signing key: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

---

## 6. Quick reference: current public MCP URL

```
https://home-automation-bridge.taila5d7e1.ts.net/mcp
```

Use this exact URL when adding/re-adding the connector in Claude. If the
phone is ever re-flashed or the hostname changes again, follow Section 4's
checklist before assuming anything else is broken.
