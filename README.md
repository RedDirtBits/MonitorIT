
# MonitorIT - Network Operations Dashboard

**MonitorIT** is a lightweight, zero-install portable network diagnostic tool inspired by PingInfoView and WinMTR. It combines multi-host continuous ping monitoring with dual-slot MTR-style continuous traceroutes to detect and alert on route failovers during network maintenance windows.

Designed specifically for restricted enterprise Windows environments, **MonitorIT** calls native Windows IP Helper APIs (`iphlpapi.dll`) via Python `ctypes`, allowing standard non-administrator domain accounts to perform low-level ICMP operations without elevated privileges.

## Key Features

* **Zero Administrator Privileges Required:** Executes unprivileged ICMP Echo Requests and TTL-restricted traceroute sweeps using Windows C APIs (`IcmpSendEcho`).
* **Dual-Slot Live Route Monitor:** Continuously monitors intermediate routing paths for up to two targets simultaneously (stacked 50/50 vertically).
* **Automated Path Shift & Baseline Alerts:** Visual flashes and audio beeps trigger instantly when intermediate hop IPs deviate from previous sweeps or locked baseline routes.
* **Flexible Target Parser:** Imports raw host lists, legacy PingInfoView `.txt` exports, tab/comma-separated strings, and CIDR subnet notations (e.g., `198.51.100.0/28`).
* **Inline Editing & Row Removal:** Edit friendly host names directly inside table inputs, or click `✕` to drop unreachable firewall targets without resetting active packet statistics.
* **JSON Profile Persistence:** Save and load custom host groups, ping rates, and traceroute intervals (`profiles/*.json`).

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
│   └── profile_manager.py   # Profile loader, JSON serializer & raw host text parser
├── profiles/
│   └── sample_profile.json  # Initial default profile
├── Launch-MonitorIT.bat     # One-click portable launcher
├── README.md                # Project documentation
├── requirements.txt         # Python dependencies
└── TROUBLESHOOTING.md       # Operational issue and bug log
```

## Quick Start & Installation

### Option A: Local Development Setup (Virtual Environment)

1. **Clone or Extract Project:** Ensure the `MonitorIT` directory is on your local machine.
2. **Initialize Python Virtual Environment:**

```python
cd MonitorIT
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. **Launch Application:** Double-click `Launch-MonitorIT.bat` or run:

```python
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

4. **Access Dashboard:** Open your browser to `http://127.0.0.1:8000`.

## Usage Guide

### 1. Host Monitoring & Inline Editing

- **Filter Hosts:** Type IP addresses or names into the search bar at the top of the host table.
- **Friendly Names:** Click into any **Friendly Name** input box to modify host labels on the fly. Press **Enter** or click away to sync changes.
- **Remove Dead Targets:** Click the red `✕` icon next to any host row to instantly delete it from the active session.

### 2. Live Continuous Traceroute

- Click **"Monitor Route"** next to any host.
- Target assigned to **Slot 1** or **Slot 2** in the right drawer panel.
- Click **"Lock Base"** to establish a reference path. Any intermediate router IP shift triggers visual flashing and audio beeps (toggleable via **🔊 Audio: ON/OFF**).

### 3. Adding & Importing Hosts

- Click **"+ Add / Import Hosts"**.
- Paste target IPs, tabbed lists, or CIDR blocks:

```
10.0.0.1 Core Switch Vlan10
10.0.0.2, Edge Router
198.51.100.0/28
```

- Leave **"Append to active host list"** checked to keep current metrics running, or uncheck to replace the list entirely.

## REST API Specification

|**Method**|**Endpoint**|**Description**|
|---|---|---|
|`GET`|`/`|Serves main UI dashboard (`index.html`)|
|`GET`|`/api/profiles`|Lists available JSON profile names|
|`POST`|`/api/profiles/load/{name}`|Loads profile and initializes hosts|
|`POST`|`/api/profiles/save`|Saves host list and settings to profile JSON|
|`POST`|`/api/hosts/parse`|Parses raw text / CIDR into structured entries|
|`POST`|`/api/hosts/set`|Sets or appends host grid (`{ hosts: [...], append: bool }`)|
|`POST`|`/api/hosts/delete`|Deletes a host row by IP (`{ ip: "x.x.x.x" }`)|
|`POST`|`/api/hosts/rename`|Renames host in memory (`{ ip: "x.x.x.x", name: "y" }`)|
|`POST`|`/api/config/intervals`|Configures ping and traceroute timers|
|`POST`|`/api/traceroute/start`|Starts continuous worker for target IP|
|`POST`|`/api/traceroute/stop`|Stops worker for target IP|
|`POST`|`/api/traceroute/baseline/lock`|Locks current path as route baseline|
|`WS`|`/ws`|Real-time WebSocket feed for ping & route metrics|

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

- **Backup:** Copy the `profiles/` folder to back up host lists.
- **Restore:** Paste JSON profiles into `profiles/` and select them from the top bar dropdown menu.