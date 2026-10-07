
# MonitorIT - Network Operations Dashboard

**MonitorIT** is a lightweight, zero-install portable network diagnostic tool inspired by PingInfoView and WinMTR. It combines multi-host continuous ping monitoring with dual-slot MTR-style continuous traceroutes to detect and alert on route failovers during network maintenance windows.

Designed specifically for restricted enterprise Windows environments, **MonitorIT** calls native Windows IP Helper APIs (`iphlpapi.dll`) via Python `ctypes`, allowing standard non-administrator domain accounts to perform low-level ICMP operations without elevated privileges.

> **Platform:** Windows 10/11 only. The ICMP engine depends on `iphlpapi.dll` and will not start on Linux or macOS.

## Key Features

* **Zero Administrator Privileges Required:** Executes unprivileged ICMP Echo Requests and TTL-restricted traceroute sweeps using Windows C APIs (`IcmpSendEcho`).
* **Dual-Slot Live Route Monitor:** Continuously monitors intermediate routing paths for up to two targets simultaneously (stacked 50/50 vertically).
* **Automated Path Shift & Baseline Alerts:** Visual flashes and audio beeps trigger instantly when intermediate hop IPs deviate from previous sweeps or locked baseline routes. Hops that simply fail to answer (routers rate-limit ICMP replies) are not treated as route changes.
* **Scales to Large Host Lists:** Pings run on a dedicated pool of worker threads, so a profile with 100+ unreachable hosts still completes each round in about one timeout window (1 second) and never delays the traceroute sweeps.
* **Flexible Target Parser:** Imports raw host lists, DNS hostnames, legacy PingInfoView `.txt` exports, tab/comma-separated strings, and CIDR subnet notations (e.g., `198.51.100.0/28`). Lines that cannot be used are listed back to you with the reason.
* **Inline Editing & Row Removal:** Edit friendly host names directly inside table inputs, or click `✕` to drop unreachable firewall targets without resetting active packet statistics.
* **JSON Profile Persistence:** Save and load custom host groups, ping rates, and traceroute intervals (`profiles/*.json`).
* **Survives Page Refreshes:** Running traceroute slots and interval settings live on the server, so a refreshed page or a second browser tab picks them back up.

## Directory Structure

```text
MonitorIT/
├── app/
│   ├── static/
│   │   ├── app.js           # WebSocket connection, table updating & route change alerting
│   │   ├── index.html       # Single-page dashboard layout
│   │   └── style.css        # Dark operational theme
│   ├── __init__.py
│   ├── icmp_engine.py       # Windows iphlpapi.dll Ctypes wrapper (Ping & TTL traceroute)
│   ├── main.py              # FastAPI server, WebSocket endpoints & REST API
│   ├── open_browser.py      # Opens the dashboard once the server is listening
│   └── profile_manager.py   # Profile loader, JSON serializer & raw host text parser
├── profiles/
│   └── sample_profile.json  # Default profile loaded at startup
├── python/                  # Optional portable embedded Python (with packages installed)
├── LaunchIT.bat             # One-click launcher (portable Python or venv)
├── README.md                # Project documentation
├── requirements.txt         # Python dependencies
└── TROUBLESHOOTING.md       # Operational issue and bug log
```

## Quick Start & Installation

### Option A: Portable (Embedded Python)

If the `python\` folder (Windows embeddable Python with the requirements installed into `python\Lib\site-packages`) is present, no installation is needed:

1. Double-click `LaunchIT.bat`.
2. The dashboard opens in your default browser at `http://127.0.0.1:8000` as soon as the server is ready.
3. Press **Ctrl+C** in the console window to stop the server.

`LaunchIT.bat` works from any location, including desktop shortcuts. If the server fails to start (for example, the port is already in use), the window stays open so you can read the error.

### Option B: Local Development Setup (Virtual Environment)

1. **Clone or Extract Project:** Ensure the `MonitorIT` directory is on your local machine.
2. **Initialize Python Virtual Environment:**

```powershell
cd MonitorIT
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. **Launch Application:** Double-click `LaunchIT.bat` (it uses the venv with auto-reload when `python\` is absent), or run from the project folder:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

4. **Access Dashboard:** Open your browser to `http://127.0.0.1:8000`.

To use a different port, change `set "PORT=8000"` near the top of `LaunchIT.bat`.

## Usage Guide

### 1. Host Monitoring & Inline Editing

- **Filter Hosts:** Type IP addresses or names into the search bar at the top of the host table.
- **Friendly Names:** Click into any **Friendly Name** input box to modify host labels on the fly. Press **Enter** or click away to sync changes.
- **Remove Dead Targets:** Click the red `✕` icon next to any host row to instantly delete it from the active session.

### 2. Live Continuous Traceroute

- Click **"Monitor Route"** next to any host.
- Target assigned to **Slot 1** or **Slot 2** in the right drawer panel.
- Click **"Lock Base"** to establish a reference path. Any intermediate router IP shift triggers visual flashing and audio beeps (toggleable via **🔊 Audio: ON/OFF**).
- Hops are compared by hop number. A hop that shows `*` (no reply) is never counted as a change, because routers routinely drop or rate-limit these replies. A route that gets longer or shorter is still detected.
- If a hop was silent at the moment you locked the baseline, the first IP that later answers at that hop is adopted into the baseline. Lock the baseline once the path looks complete for the strongest comparison.
- Traceroutes keep running on the server if you refresh or close the page; reopening the dashboard reconnects to them. Use **Stop** to end one.

### 3. Adding & Importing Hosts

- Click **"+ Add / Import Hosts"**.
- Paste target IPs, tabbed lists, or CIDR blocks:

```
10.0.0.1 Core Switch Vlan10
10.0.0.2, Edge Router
198.51.100.0/28
```

- **Hostnames** (e.g., `fileserver01.corp.local File Server`) are resolved to their IPv4 address at import time; the rest of the line becomes the friendly name.
- **CIDR blocks** are limited to a `/22` (1,022 hosts) or smaller to prevent accidental huge imports such as `10.0.0.0/8`.
- **IPv4 only:** IPv6 addresses and subnets are skipped.
- After import, any lines that were skipped (unresolvable names, duplicates, oversized subnets, IPv6) are listed with the reason.
- Leave **"Append to active host list"** checked to keep current metrics running, or uncheck to replace the list entirely.

## REST API Specification

|**Method**|**Endpoint**|**Description**|
|---|---|---|
|`GET`|`/`|Serves main UI dashboard (`index.html`)|
|`GET`|`/api/profiles`|Lists available JSON profile names|
|`GET`|`/api/state`|Active profile, intervals, and traceroute slot assignments|
|`POST`|`/api/profiles/load/{name}`|Loads profile and initializes hosts|
|`POST`|`/api/profiles/save`|Saves host list and settings to profile JSON|
|`POST`|`/api/hosts/parse`|Parses raw text / hostnames / CIDR into structured entries, plus a `skipped` list|
|`POST`|`/api/hosts/set`|Sets or appends host grid (`{ hosts: [...], append: bool }`)|
|`POST`|`/api/hosts/delete`|Deletes a host row by IP (`{ ip: "x.x.x.x" }`)|
|`POST`|`/api/hosts/rename`|Renames host in memory (`{ ip: "x.x.x.x", name: "y" }`)|
|`POST`|`/api/config/intervals`|Configures ping and traceroute timers (seconds, 1–3600)|
|`POST`|`/api/traceroute/start`|Starts continuous worker for target IP|
|`POST`|`/api/traceroute/stop`|Stops worker for target IP|
|`POST`|`/api/traceroute/baseline/lock`|Locks current path as route baseline|
|`WS`|`/ws`|Real-time WebSocket feed: `state_sync`, `ping_update`, `traceroute_update` messages|

### Local-Only Access

The server only answers requests addressed to `127.0.0.1` or `localhost`, and rejects write requests and WebSocket connections from other websites' pages. This stops a web page open in your browser from reading your monitored host inventory or changing settings. To reach the dashboard under another name or address, add it to `ALLOWED_HOSTS` in `app/main.py` (and change `--host` in `LaunchIT.bat`).

## Backup & Profile Management

Profiles are stored as standard JSON files inside the `profiles/` directory:

```json
{
  "profile_name": "Sample Profile",
  "ping_interval": 3,
  "traceroute_interval": 1,
  "hosts": [
    { "ip": "127.0.0.1", "name": "Localhost", "group": "Default" },
    { "ip": "1.1.1.1", "name": "Cloudflare DNS", "group": "Default" }
  ]
}
```

- **Backup:** Copy the `profiles/` folder to back up host lists. Profiles other than `sample_profile.json` are deliberately excluded from Git (see [Version Control](#version-control-git)), so Git is **not** a backup for them.
- **Restore:** Paste JSON profiles into `profiles/` and select them from the top bar dropdown menu.
- **Profile names:** Characters Windows does not allow in filenames (`\ / : * ? " < > |`) are replaced with `_` when saving, and the dashboard shows the name actually used.
- **Default profile:** `sample_profile.json` is loaded automatically at startup.

## Version Control (Git)

The project is a **local-only** Git repository (no remote), used to track changes to the app and roll back if something breaks. Commits use the GitHub noreply address `673674+RedDirtBits@users.noreply.github.com`, which is set for this repository only.

### What Is and Isn't Tracked

| Tracked | Ignored (see `.gitignore`) |
|---|---|
| `app/` source, `LaunchIT.bat`, docs, `requirements.txt` | `python/` portable runtime (64 MB of binaries; reinstallable) |
| `profiles/sample_profile.json` | All other `profiles/*.json` (internal IPs and device names) |
| `.gitignore`, `snapshot_v3.py`, `monitorit_project.ps1` | `venv/`, `__pycache__/`, `snapshot.md`, editor folders |

### First-Time Setup on a New Machine

If you copy the project folder (including the hidden `.git` folder) to another PC, set the identity and line-ending behavior for this repository once:

```powershell
cd MonitorIT
git config user.name "RedDirt"
git config user.email "673674+RedDirtBits@users.noreply.github.com"
git config core.autocrlf false
```

The files use Windows (CRLF) line endings and are stored that way in the repository. `core.autocrlf false` stops Git from converting them, which would otherwise make every line of a file show as changed.

### Everyday Workflow

```powershell
git status                      # What has changed since the last commit?
git diff                        # Show the exact line changes
git add -A                      # Stage all changes (new, modified, deleted files)
git commit -m "Describe what changed and why"
git log --oneline               # List commits, newest first
```

Commit after each working change, so there is always a known-good point to return to.

### Undoing Mistakes

```powershell
# Throw away uncommitted edits to one file (restore it from the last commit)
git restore app/main.py

# Throw away ALL uncommitted edits (cannot be undone)
git restore .

# Unstage a file you added by mistake (keeps your edits)
git restore --staged app/main.py

# See what a past commit changed
git show <commit-id>

# Bring back one file as it was in an earlier commit
git checkout <commit-id> -- app/static/app.js

# Undo a whole commit by adding a new commit that reverses it (history is kept)
git revert <commit-id>
```

`<commit-id>` is the short code shown by `git log --oneline` (e.g., `5056c60`).

### Trying Risky Changes on a Branch

```powershell
git switch -c experiment        # Create and switch to a new branch
# ...edit, test, commit as usual...
git switch main                 # Back to the working version (experiment is untouched)
git merge experiment            # Keep the experiment's changes, or...
git branch -D experiment        # ...throw the experiment away
```

### Marking Known-Good Versions

```powershell
git tag v1.0 -m "Used during maintenance window"
git tag                         # List tags
git checkout v1.0 -- .          # Restore every tracked file to that version
```

### Tracking a New Profile Anyway

Profiles are ignored by default. To version a specific profile that contains nothing sensitive, add an exception line to `.gitignore` below the existing ones:

```gitignore
!profiles/My_Profile.json
```

> **Before ever pushing this repository to GitHub:** the first commit (`c123f8d`) contains two work profiles from before they were ignored. Ignoring a file does not remove it from earlier commits, so those must be scrubbed from history before any push.
