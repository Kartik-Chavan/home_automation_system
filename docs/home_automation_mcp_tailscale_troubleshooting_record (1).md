# Home Automation MCP — Android/Tailscale Funnel Troubleshooting Record (revised)

**Date:** 2026-09-23
**Status:** Troubleshooting intentionally paused. This file preserves the evidence so the next plan starts from facts instead of repeating failed steps.

> **What changed in this revision** (versus the first draft)
> - Corrected the device name to Xiaomi Mi Max **Prime** (as in the setup guide).
> - Fixed the run order: the `netlinkrib` error came from the **latest** run (socket at `/tmp/ts.sock`), not an earlier one.
> - Added the `/tmp/ts.sock` result, which the first draft omitted.
> - Separated **facts** from **hypotheses** and added an **Open questions** section, including a timing inconsistency the first draft did not notice (section 9).
> - Added missing observations (Claude connector screens, `logpolicy` path, second stale connector, mixed timestamps).
> - Added the **laptop fallback** already documented in the repo README, and a concrete **next-plan decision list**.

---

## 1. Architecture

Intended remote path:

```text
Claude / MCP client (Anthropic cloud, NOT on your tailnet)
        |
        | Public HTTPS
        v
Tailscale Funnel (public ingress relay)
        |
        v
Android phone (Termux, tailscaled in userspace-networking mode)
        |
        | 127.0.0.1:8765
        v
FastMCP server (home-automation-mcp)
        |
        | Home Wi-Fi LAN
        v
Tapo P110 plug
```

| Item | Value |
|---|---|
| Public hostname | `https://home-automation-bridge.taila5d7e1.ts.net` |
| MCP endpoint | `https://home-automation-bridge.taila5d7e1.ts.net/mcp` |
| MCP server bind | `127.0.0.1:8765` |
| Device | Xiaomi Mi Max Prime, Android 7.0, 4 GB RAM, 128 GB storage |
| Why Termux, not the Tailscale app | Android 7 is below the Tailscale app's minimum (8.0+) |
| Tailscale | `1.102.4` (Linux arm64 binaries, Go 1.26.6), no auto-update |
| Repo dir | `~/home_automation_system` |
| Binary dir | `~/home_automation_system/tailscale_1.102.4_arm64` |
| State dir / socket | `$HOME/.tailscale` / `$HOME/.tailscale/ts.sock` |

Note: inside `termux-chroot`, `$HOME` resolves to `/home`, so the daemon reports `--statedir=/home/.tailscale`. This is normal for proot.

---

## 2. Known-good startup (worked earlier the same day)

Inside `termux-chroot`:

```bash
export SSL_CERT_FILE=/data/data/com.termux/files/usr/etc/tls/cert.pem
cd ~/home_automation_system/tailscale_1.102.4_arm64
./tailscaled --tun=userspace-networking --statedir=$HOME/.tailscale --socket=$HOME/.tailscale/ts.sock &
./tailscale --socket=$HOME/.tailscale/ts.sock status
```

Hostname was set successfully (from auto-generated `localhost-0`):

```bash
./tailscale --socket=$HOME/.tailscale/ts.sock set --hostname=home-automation-bridge
```

## 3. Known-good Funnel configuration

```bash
./tailscale --socket=$HOME/.tailscale/ts.sock funnel --bg 8765
./tailscale --socket=$HOME/.tailscale/ts.sock funnel status
```

Output (also seen again during troubleshooting, before the daemon died):

```text
https://home-automation-bridge.taila5d7e1.ts.net (Funnel on)
|-- / proxy http://127.0.0.1:8765
```

**Caveat:** `funnel status` shows the *configuration*. It does not prove the live public relay path is healthy.

## 4. Known-good MCP server

```bash
cd ~/home_automation_system
source .venv/bin/activate
home-automation-mcp
```

Expected: `Uvicorn running on http://127.0.0.1:8765`. The MCP application worked end to end earlier.

## 5. Evidence that OAuth + MCP worked

After fixing the hostname and the GitHub OAuth Redirect URI, server logs showed:

```text
GET  /auth/callback ... 302 Found
POST /token ... 200 OK
GET  https://api.github.com/user ... 200 OK
POST /mcp ... 200 OK
```

So GitHub OAuth, token exchange, MCP auth and real MCP calls all worked. Redirect URI used: `https://home-automation-bridge.taila5d7e1.ts.net/auth/callback`. Secrets were rotated after being exposed; values are deliberately not recorded.

---

## 6. Observations, in the order they happened

### 6.1 Screen-off observation
The phone sat untouched with the screen off for roughly 30–40 minutes. An external request failed with a connection error during that time. After waking the phone, the same URL returned `401 Unauthorized`.
*Status:* suggests Android/MIUI suspension of Termux/Tailscale, but it is a **hypothesis**, not proven.

### 6.2 Network-path isolation test (strongest evidence)

| Test device | Tailscale | Result |
|---|---|---|
| Laptop / phone on the tailnet | ON | `401`, request appeared in MCP server log |
| Device on public internet / mobile data | OFF | Did **not** reach server, nothing in MCP log |

Conclusion: `tailnet -> phone -> MCP` worked, while `public internet -> Funnel -> phone -> MCP` did not.
Claude's cloud backend is not in the tailnet, so it depends on the public Funnel path.

*Gap:* the exact failure mode of the Tailscale-OFF test (timeout vs DNS failure vs TLS error vs HTTP error) was **not recorded**. That detail would tell us whether DNS/Funnel ingress or the node itself was at fault.

### 6.3 Claude connector symptoms
- The "Add custom connector" screen for the URL showed **"Connecting to the server: Couldn't reach this address"** (later steps skipped).
- An earlier authorization attempt showed a failure toast with a support reference, while the server log showed the browser-side OAuth legs completing (`/authorize` 302, `/consent` 200/302, GitHub `access_token` 200, `/auth/callback` 302). Those requests came from a `100.x` address (tailnet), i.e. from the user's own device, so they did not exercise the public path.
- Later, the registered connector `Home_Automation_phone` was visible with 10 tools, but tool calls returned **"The connector's server isn't responding."** No matching requests appeared in the MCP log.
- Claude also reported a second connector, `home_automation`, pointing at a localhost address that did not load. A localhost URL can never be reached from Claude's cloud, so it should be removed. *(Reported by Claude in chat, not independently verified.)*

Interpretation: failure happens **before** the MCP application layer.

### 6.4 tailscaled became unhealthy
`ps` showed a `tailscaled` process (PID 19917, started 17:06), but:

```text
./tailscale --socket=$HOME/.tailscale/ts.sock status
failed to connect to local tailscaled ... connect: connection refused
```

The process existed while its local API socket refused connections.

### 6.5 Kill and restart attempts (chronological)

| # | Action | Result |
|---|---|---|
| 1 | `kill -9 19917`, `rm -f ts.sock`, start with `--socket=$HOME/.tailscale/ts.sock` | **SIGSEGV** in `safesocket.listen` |
| 2 | Removed socket again, restarted the same way | **SIGSEGV**, same stack |
| 3 | `pkill -9 tailscaled`, cleared `ts*.sock`, start with `--socket=/tmp/ts.sock` | **No panic.** Exit 1 with `netmon.New: route ip+net: netlinkrib: invalid argument` |

`ls` after attempt 1 showed the socket file **was created** (`srwx------ ... ts.sock`, fresh timestamp). So `bind()` succeeded and the crash happens right after, inside Go's `net.(*sysListener).listenUnix` (fault address `0x20`, i.e. a nil pointer dereference).

Not run: the `ts2.sock` suggestion from the helper agent, and the two follow-ups from the previous message (reboot; `PROOT_NO_SECCOMP=1`).

### 6.6 Other lines seen in daemon output
- `TPM: error opening: stat /dev/tpmrm0` — harmless, **not** the failure.
- `logpolicy: using system state directory "/var/lib/tailscale"` — the daemon logs to a **system** path even though `--statedir` points elsewhere. This directory may not exist or be writable inside proot. Possibly relevant, not proven.
- `dns: using "direct" mode` and `dns: inotify: context canceled` in the first crash.
- A Python venv (`.venv`) was active in the shell used for later attempts. Probably irrelevant, but it is part of the environment.

---

## 7. Facts vs hypotheses

**Established by evidence**
- OAuth, token exchange and MCP calls worked earlier.
- Funnel configuration is correct (name and proxy target).
- Tailscale-mesh access returned 401; public access did not reach the server.
- `tailscaled` at one point ran without answering on its local socket.
- Restarting `tailscaled` now fails: two SIGSEGVs in socket listen, then a `netlinkrib` exit when the socket is `/tmp/ts.sock`.
- Deleting the socket file did not fix the crash, so a stale socket is not the cause.

**Hypotheses only**
- Android/MIUI suspended or froze Termux and started the chain of failures.
- The `termux-chroot` (proot) layer or its syscall handling (seccomp/netlink) is at fault.
- A Termux/proot package update today changed behavior.
- The Tailscale 1.102.4 build (Go 1.26.6) has a compatibility problem with this old Android 7 kernel/environment.
- The SIGSEGV mechanism is a failed or unusable socket-address lookup after bind.

## 8. Open questions (things the evidence does not answer)

1. **Timing inconsistency.** The Tailscale-ON test returned 401, yet the daemon's local socket was refusing connections at some point. Either the data path kept working while the local API listener was dead, or the two tests happened at different times. Nobody recorded timestamps, so the order is unknown. This matters: it decides whether the daemon was already sick when Funnel failed.
2. Did `pkg upgrade` (or any Termux update) run today? Not yet checked.
3. Is the binary the same one that worked this morning, or was it re-downloaded?
4. Exact failure of the Tailscale-OFF test (see 6.2).
5. Mixed clocks: server logs show local time (about 21:23 IST) while some Termux lines show different times (e.g. 17:06, 17:41, likely UTC). Compare carefully before drawing timing conclusions.
6. Does a reboot, by itself, restore a working `tailscaled`?

---

## 9. What should NOT be changed now

Do not delete `$HOME/.tailscale/tailscaled.state` or the whole `$HOME/.tailscale`, because that forces a new login and can change the hostname. Do not change any of these, since all previously worked:

- GitHub OAuth app, client ID, numeric allow-list
- MCP auth code, endpoint or port
- Tapo credentials
- The registered Claude connector (except removing the localhost duplicate)

---

## 10. Resume plan for the phone path (cheapest first)

1. **Reboot the phone.** Retry the known-good startup from section 2 with the normal socket path.
2. If it fails: outside the chroot, run `PROOT_NO_SECCOMP=1 termux-chroot`, then repeat startup.
3. Collect diagnostics (outside the chroot):
   ```bash
   proot --version
   uname -a
   grep -E "Start-Date|Upgrade" $PREFIX/var/log/apt/history.log | tail -20
   ```
4. Try an **older Tailscale tarball** (a build from a few months earlier) to test the version-compatibility hypothesis. Keep the current state directory (copy `tailscaled.state` somewhere first as a backup).
5. Check whether Termux offers a native package (`pkg search tailscale`) that can run **without proot**.
6. Only when `tailscale status` works: `funnel reset`, then `funnel --bg 8765`, wait 2–3 minutes.
7. Test from **outside** the tailnet (Tailscale OFF, mobile data): expect `401` and a matching MCP log line. Record the exact failure mode if it fails.
8. Only then retry Claude. No re-authorization should be needed.

## 11. Alternatives if the phone path stays unreliable

| Option | Effort | Notes |
|---|---|---|
| **Run the MCP server on the Windows laptop** | Low | Already documented in the repo README: NSSM service with auto-restart, Tailscale as a Windows service, `tailscale funnel --bg 8765`. The laptop is on the same LAN as the P110. Downside: it must stay powered on and online. Update `MCP_PUBLIC_BASE_URL`, the GitHub Redirect URI and re-add the connector if the hostname changes. |
| **Phone with Android 8+** (cheap used device) | Medium | Runs the official Tailscale app with Funnel support and proper background handling, and avoids proot. |
| **Raspberry Pi / small always-on Linux box** | Medium | Most reliable long term, low power, native Tailscale. |
| **Different tunnel (e.g. Cloudflare Tunnel)** | Medium | Removes Tailscale from the chain. Stable URLs generally need a domain you control. |

Suggested order: try steps 1–3 of section 10 (about 15 minutes). If that fails, use the laptop deployment as the working fallback and treat the phone as a later project.

## 12. Reliability settings to apply once it runs again

- `termux-wake-lock` in the tailscaled session
- Termux battery mode: Unrestricted (Settings → Apps → Termux → Battery), plus MIUI battery-management exception
- Lock Termux in the recent-apps screen
- Keep the phone plugged in
- Termux:Boot for auto-start after reboot
- Add a watchdog that checks `tailscale status` and restarts `tailscaled` and Funnel if the socket stops answering (this failure mode already occurred)
- P110: create a DHCP reservation in the router so `TAPO_DEVICE_IP` does not change

## 13. Timeline

```text
Android MCP configured
  -> Tailscale userspace networking worked
  -> Funnel worked
  -> Stable hostname created
  -> GitHub OAuth callback corrected
  -> Claude authenticated; MCP requests returned 200
  -> Phone screen off ~30-40 min; external request failed
  -> Tailnet devices still got 401; public (Tailscale OFF) could not reach /mcp
  -> Claude: "Couldn't reach this address" / "server isn't responding"
  -> tailscaled found running but local socket refused connections
  -> kill + restart: SIGSEGV in safesocket.listen (twice)
  -> socket at /tmp/ts.sock: netmon "netlinkrib: invalid argument", exit 1
  -> Troubleshooting paused
```

## 14. Handoff summary

The MCP server, GitHub OAuth and the Claude connector all worked earlier. The later failure is isolated to the **public Funnel path**, and the current concrete blocker is that `tailscaled 1.102.4` no longer starts inside Termux/proot on this Android 7 phone: it crashes in `safesocket.listen`, or exits on `netmon` when the socket path is changed. The cause is unproven; the leading suspects are the proot layer, a Termux update, or a Tailscale build/kernel incompatibility. The next step is either the short resume plan in section 10 or moving the server to the laptop (section 11), not any change to MCP, OAuth or Claude.

*This document records observations and hypotheses. It does not prove that Android sleep caused the Tailscale failure.*
