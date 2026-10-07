# MonitorIT - Troubleshooting & Bug Log

This document serves as an operational record for tracking errors, unexpected behaviors, root causes, and verified fixes across the lifecycle of the **MonitorIT** project.

---

## Log Entries

### [2026-09-21] - Project Initial Scaffolding

* **Component:** System Scaffolding & Setup
* **Issue Description:** Potential privilege denial when executing raw socket ICMP operations on domain-managed Windows workstations (`OSError: [WinError 10013]`).
* **Root Cause:** Standard Windows security policy restricts raw socket creation (`IPPROTO_ICMP`) to elevated/Administrator accounts.
* **Resolution Strategy:** Implement `icmp_engine.py` using Windows `iphlpapi.dll` via Python's `ctypes` library. Calling `IcmpSendEcho` allows standard, unprivileged domain users to perform ICMP pings and TTL traceroutes without requiring elevation.
* **Verification Method:** Run ping and TTL traceroute unit functions under a standard (non-admin) user prompt.

### [2026-10-06] - Code Review Fixes (Operation & Reliability)

* **Component:** ICMP engine, ping/traceroute loops, host parser, dashboard, launcher
* **Issue Description:** Several defects found in a full code review:
  * Ping rounds stretched to 9-12 seconds and traceroute sweeps stalled when many hosts in a large profile (100+) were unreachable.
  * False "path shift" alerts when a router skipped a single TTL-exceeded reply; the Baseline column misaligned after any `*` hop.
  * Importing a large CIDR (e.g., `10.0.0.0/8`) froze the server; hostnames and IPv6 entries were silently dropped.
  * After a browser refresh, traceroutes kept running (and beeping) with no visible panel or working Stop button.
  * Loading a profile did not update the interval buttons, so **Save** overwrote the profile's intervals with 3s/1s.
  * Route alert beeps stopped working after a while (a new browser AudioContext per beep).
  * The launcher crashed when started from a shortcut or via "Run as administrator" (working directory was not the project folder).
  * Any website open in the browser could read the host list over the WebSocket or switch profiles.
* **Root Cause:** Probes shared asyncio's small default thread pool; path comparison removed `*` hops before comparing lists; no subnet size limit and parsing ran on the event loop; traceroute slots were tracked only in the browser; relative file paths; no Origin/Host validation.
* **Resolution Strategy:**
  * Dedicated ping (256) and traceroute (64) thread pools, and a fixed ping cadence.
  * Hop-by-hop path comparison that ignores silent hops.
  * Parser: `/22` subnet cap, hostname resolution, and a skipped-lines report.
  * Server-driven `state_sync` WebSocket messages for slots and intervals.
  * One shared AudioContext for alert beeps.
  * Paths resolved from the source files, and `cd /d "%~dp0"` in the launcher.
  * Host allow-list plus Origin checks on writes and the WebSocket.
  * Smaller fixes:
    * Broken `INVALID_HANDLE_VALUE` check.
    * Profile name sanitizing on load and save.
    * Deprecated `on_event` replaced with `lifespan`.
    * Pending hosts no longer counted as Offline.
    * Sub-millisecond RTTs shown as `<1 ms`.
    * The browser opens only once the server is listening.
  * Launcher renamed from `Launch-MonitorIT.bat` to `LaunchIT.bat`.
* **Verification Method:** Exercised the engine, API, and WebSocket against a simulated `iphlpapi.dll` (live/dead hosts, rate-limited hop, reroute). Still to verify on the Windows 11 work PC with real targets.

---

## Troubleshooting Guide

### 1. Port 8000 Already in Use

* **Error Message:** `[Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000): address already in use`
* **Solution:** Close any existing running instances of MonitorIT or Uvicorn, or change `set "PORT=8000"` in `LaunchIT.bat` to an alternative high port (e.g., `8080` or `50000`). The launcher window now stays open when this happens so the error is visible.

### 2. Virtual Environment Execution Policy (PowerShell)

* **Error Message:** `File ...\activate.ps1 cannot be loaded because running scripts is disabled on this system.`
* **Solution:** Run PowerShell command: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process` before activating the virtual environment.

### 3. "Invalid host header" or 403 "Cross-origin request rejected"

* **Cause:** The server only accepts requests addressed to `127.0.0.1` or `localhost`, and rejects writes coming from other websites' pages.
* **Solution:** Browse to `http://127.0.0.1:8000` or `http://localhost:8000`. To use another hostname or address, add it to `ALLOWED_HOSTS` in `app/main.py`.

### 4. Imported Lines Were Skipped

* **Cause:** After import, MonitorIT lists every line it could not use: unresolvable hostnames, duplicates, IPv6 entries, or subnets larger than `/22`.
* **Solution:** Check DNS for the listed names (`nslookup <name>`), enter the IP directly, or split large subnets into `/22` or smaller blocks.
