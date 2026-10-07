import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.icmp_engine import (
    async_ping_host,
    async_run_traceroute_sweep,
    shutdown_executors,
    PingResult,
    TracerouteHop,
)
from app.event_log import EventLog, format_duration
from app.profile_manager import ProfileManager, ProfileData, HostEntry


# Resolve paths from this file so the app works regardless of the launch directory
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
PROFILES_DIR = APP_DIR.parent / "profiles"
LOGS_DIR = APP_DIR.parent / "logs"

DEFAULT_PROFILE = "sample_profile"

# Host headers the server answers to. Rejecting anything else blocks DNS-rebinding
# attacks, where a web page re-points its own domain at 127.0.0.1 to reach this API.
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]


MIN_DOWN_THRESHOLD = 1
MAX_DOWN_THRESHOLD = 10


def clamp_down_threshold(value: int) -> int:
    return max(MIN_DOWN_THRESHOLD, min(MAX_DOWN_THRESHOLD, value))


class IntervalConfig(BaseModel):
    ping_interval: int = Field(ge=1, le=3600)
    traceroute_interval: int = Field(ge=1, le=3600)
    down_threshold: int = Field(default=2, ge=MIN_DOWN_THRESHOLD, le=MAX_DOWN_THRESHOLD)


class ParseHostsRequest(BaseModel):
    raw_text: str


class DeleteHostRequest(BaseModel):
    ip: str


class RenameHostRequest(BaseModel):
    ip: str
    name: str


class SetHostsRequest(BaseModel):
    hosts: List[HostEntry]
    append: bool = False


class TracerouteStartRequest(BaseModel):
    target_ip: str
    slot: Optional[int] = Field(default=None, ge=1, le=2)


class TracerouteTargetRequest(BaseModel):
    target_ip: str


class ConnectionManager:
    """Manages active WebSocket client connections and broadcasts."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        disconnected = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)


def _new_host_stat(h: HostEntry) -> dict:
    return {
        "ip": h.ip,
        "name": h.name,
        "group": h.group,
        "is_alive": False,
        "rtt_ms": None,
        "sent": 0,
        "received": 0,
        "loss_pct": 0,
        # Pending -> Online / Timeout (failed, under the down threshold) / Offline
        "status": "Pending",
        "consecutive_failures": 0,
        # Epoch seconds of the first failed ping in the current failure streak
        "first_failure_at": None,
        # Wall-clock text of when the current outage began, for the dashboard tooltip
        "down_since": None,
        "ever_online": False,
    }


def path_from_hops(hops: List[TracerouteHop]) -> List[Optional[str]]:
    """Hop-indexed route (index 0 = hop 1); None marks a hop that did not answer."""
    return [None if h.ip == "*" else h.ip for h in hops]


def path_differences(current: List[Optional[str]], reference: List[Optional[str]]) -> List[str]:
    """Hops that answered from a different router in both paths, e.g. 'hop 3: A -> B'.

    A hop that is silent in either path proves nothing (routers rate-limit ICMP
    TTL-exceeded replies), so only hops known on both sides are compared. A longer
    or shorter route still shows up: the destination lands on a hop number where
    the other path has an intermediate router.
    """
    return [
        f"hop {i + 1}: {old} -> {new}"
        for i, (new, old) in enumerate(zip(current, reference))
        if new is not None and old is not None and new != old
    ]


def paths_differ(a: List[Optional[str]], b: List[Optional[str]]) -> bool:
    return bool(path_differences(a, b))


def merge_known_path(known: List[Optional[str]], current: List[Optional[str]]) -> List[Optional[str]]:
    """Current sweep with silent hops filled in from the last sweep that heard them.

    Follows the current sweep's length, so hops past a newly shorter route drop off.
    """
    return [
        ip if ip is not None or i >= len(known) else known[i]
        for i, ip in enumerate(current)
    ]


class AppState:
    """Central state engine for host statuses, background loops, and dual traceroutes."""

    def __init__(self):
        self.profile_mgr = ProfileManager(str(PROFILES_DIR))
        self.active_profile_name: str = DEFAULT_PROFILE
        self.ping_interval: int = 3
        self.traceroute_interval: int = 1
        self.down_threshold: int = 2

        self.hosts: List[HostEntry] = []
        self.host_stats: Dict[str, dict] = {}
        # Bumped whenever the host list is replaced so in-flight ping rounds can be discarded
        self.hosts_generation: int = 0

        # Dual Traceroute State Slots
        # Key: target_ip -> Dict: {"slot": 1/2, "baseline": [...], "known_path": [...], "task": Task}
        # Paths are hop-indexed lists of IPs with None for silent hops (see path_from_hops);
        # known_path keeps the last IP heard at each hop so one silent sweep loses nothing
        self.active_traceroutes: Dict[str, dict] = {}

        self.ping_task: Optional[asyncio.Task] = None

    def initialize_hosts(self, host_entries: List[HostEntry]):
        """Sets active host list and resets statistics counters."""
        self.hosts = list(host_entries)
        self.host_stats = {h.ip: _new_host_stat(h) for h in host_entries}
        self.hosts_generation += 1

    def add_hosts(self, new_entries: List[HostEntry]):
        """Appends new unique hosts to active list without clearing existing stats."""
        existing_ips = {h.ip for h in self.hosts}
        for h in new_entries:
            if h.ip not in existing_ips:
                self.hosts.append(h)
                existing_ips.add(h.ip)
                self.host_stats[h.ip] = _new_host_stat(h)

    def update_host_ping_stat(self, ip: str, result: PingResult) -> Optional[tuple]:
        """Applies one ping result. Returns (event_type, severity, message) when the
        host changes between up and down, otherwise None."""
        if ip not in self.host_stats:
            return None

        stat = self.host_stats[ip]
        stat["sent"] += 1
        stat["is_alive"] = result.is_alive
        stat["rtt_ms"] = result.rtt_ms
        now = time.time()
        event = None

        if result.is_alive:
            stat["received"] += 1
            if stat["status"] == "Offline":
                if stat["ever_online"]:
                    outage = format_duration(now - stat["first_failure_at"])
                    event = ("host_up", "success", f"Host UP after {outage} outage")
                else:
                    event = ("host_up", "success", "Host reachable (first reply)")
            stat["status"] = "Online"
            stat["ever_online"] = True
            stat["consecutive_failures"] = 0
            stat["first_failure_at"] = None
            stat["down_since"] = None
        else:
            stat["consecutive_failures"] += 1
            if stat["consecutive_failures"] == 1:
                stat["first_failure_at"] = now
            if stat["status"] != "Offline":
                if stat["consecutive_failures"] >= self.down_threshold:
                    stat["status"] = "Offline"
                    started = datetime.fromtimestamp(stat["first_failure_at"])
                    stat["down_since"] = started.strftime("%Y-%m-%d %H:%M:%S")
                    if stat["ever_online"]:
                        event = ("host_down", "critical",
                                 f"Host DOWN ({stat['consecutive_failures']} failed pings "
                                 f"since {started.strftime('%H:%M:%S')})")
                    else:
                        event = ("host_unreachable", "warning",
                                 "Host unreachable (no reply since monitoring started)")
                else:
                    stat["status"] = "Timeout"

        if stat["sent"] > 0:
            loss = ((stat["sent"] - stat["received"]) / stat["sent"]) * 100
            stat["loss_pct"] = round(loss, 1)

        return event

    def host_name(self, ip: str) -> Optional[str]:
        stat = self.host_stats.get(ip)
        return stat["name"] if stat else None

    def stop_traceroute(self, target_ip: str) -> Optional[int]:
        """Cancels a traceroute worker and frees its slot. Returns the slot it held."""
        data = self.active_traceroutes.pop(target_ip, None)
        if not data:
            return None
        task = data.get("task")
        if task and not task.done():
            task.cancel()
        return data["slot"]

    def stop_all_traceroutes(self):
        for ip in list(self.active_traceroutes):
            self.stop_traceroute(ip)

    def sync_payload(self) -> dict:
        """Server-side settings and slot assignments, so every browser tab matches the server."""
        slots: Dict[str, Optional[str]] = {"1": None, "2": None}
        for ip, data in self.active_traceroutes.items():
            slots[str(data["slot"])] = ip
        return {
            "type": "state_sync",
            "active_profile": self.active_profile_name,
            "ping_interval": self.ping_interval,
            "traceroute_interval": self.traceroute_interval,
            "down_threshold": self.down_threshold,
            "slots": slots,
        }

    def ping_payload(self) -> dict:
        return {
            "type": "ping_update",
            "hosts": list(self.host_stats.values()),
            "ping_interval": self.ping_interval,
        }


ws_manager = ConnectionManager()
state = AppState()
event_log = EventLog(LOGS_DIR)


async def emit_events(events: List[dict]):
    """Sends newly recorded events to every dashboard."""
    if events:
        await ws_manager.broadcast({
            "type": "events",
            "events": events,
            "unsaved": event_log.unsaved_count,
        })


async def log_event(event_type: str, severity: str, message: str,
                    ip: Optional[str] = None, name: Optional[str] = None):
    await emit_events([event_log.record(event_type, severity, message, ip=ip, name=name)])


def event_history_payload() -> dict:
    log_file = event_log.current_file()
    try:
        log_file = log_file.relative_to(APP_DIR.parent)
    except ValueError:
        pass  # log folder outside the project; show the full path
    return {
        "type": "event_history",
        "events": event_log.recent(500),
        "unsaved": event_log.unsaved_count,
        "log_file": str(log_file),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Loads the default profile and runs the ping loop for the life of the server."""
    default_profile = state.profile_mgr.load_profile(DEFAULT_PROFILE)
    if default_profile:
        state.active_profile_name = DEFAULT_PROFILE
        state.ping_interval = default_profile.ping_interval
        state.traceroute_interval = default_profile.traceroute_interval
        state.down_threshold = clamp_down_threshold(default_profile.down_threshold)
        state.initialize_hosts(default_profile.hosts)

    event_log.record(
        "app_start", "info",
        f"MonitorIT started (profile '{state.active_profile_name}', {len(state.hosts)} hosts)",
    )
    state.ping_task = asyncio.create_task(global_ping_loop())

    yield

    state.ping_task.cancel()
    state.stop_all_traceroutes()
    shutdown_executors()
    event_log.record("app_stop", "info", "MonitorIT stopped")
    event_log.flush()


app = FastAPI(title="MonitorIT Engine", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


def _origin_allowed(origin: Optional[str], host: Optional[str]) -> bool:
    """Same-origin check. Browsers attach Origin to cross-site POSTs and WebSockets;
    requests without one (curl, scripts) are not coming from a web page."""
    if not origin:
        return True
    return urlsplit(origin).netloc == host


@app.middleware("http")
async def reject_cross_site_writes(request: Request, call_next):
    """Stops other websites open in the browser from driving this API (CSRF)."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_allowed(
        request.headers.get("origin"), request.headers.get("host")
    ):
        return JSONResponse(status_code=403, content={"detail": "Cross-origin request rejected"})
    return await call_next(request)


# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


async def global_ping_loop():
    """Background task pinging all active hosts on a fixed cadence and broadcasting results."""
    loop = asyncio.get_running_loop()
    next_round = loop.time()

    while True:
        try:
            if state.hosts:
                generation = state.hosts_generation
                tasks = [async_ping_host(h.ip) for h in state.hosts]
                results: List[PingResult] = await asyncio.gather(*tasks)

                # A profile load or full import during the round replaced the host list
                events: List[dict] = []
                if generation == state.hosts_generation:
                    for res in results:
                        change = state.update_host_ping_stat(res.ip, res)
                        if change:
                            event_type, severity, message = change
                            events.append(event_log.record(
                                event_type, severity, message,
                                ip=res.ip, name=state.host_name(res.ip),
                            ))

                await ws_manager.broadcast(state.ping_payload())
                await emit_events(events)

            # Retry log writes that failed because the CSV was open in Excel
            if event_log.unsaved_count:
                event_log.flush()

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Loop Error] Ping loop exception: {e}")

        # Rounds start every ping_interval seconds; a round that overruns starts the next immediately
        next_round = max(next_round + state.ping_interval, loop.time())
        try:
            await asyncio.sleep(next_round - loop.time())
        except asyncio.CancelledError:
            break


async def live_traceroute_worker(target_ip: str, slot: int):
    """Background worker executing continuous MTR-style traceroutes for a specific slot."""
    while target_ip in state.active_traceroutes:
        try:
            target_data = state.active_traceroutes[target_ip]
            hops: List[TracerouteHop] = await async_run_traceroute_sweep(target_ip)
            current_path = path_from_hops(hops)
            has_answers = any(current_path)

            name = state.host_name(target_ip)

            path_changed = False
            known_path = target_data.get("known_path", [])
            if known_path and has_answers:
                changes = path_differences(current_path, known_path)
                if changes:
                    path_changed = True
                    await log_event("route_change", "warning",
                                    "Route changed: " + "; ".join(changes),
                                    ip=target_ip, name=name)

            if has_answers:
                known_path = merge_known_path(known_path, current_path)
                target_data["known_path"] = known_path

            # Judged on the last-known path so a silent hop neither raises nor clears it
            baseline_diff = False
            baseline = target_data.get("baseline", [])
            if baseline and known_path:
                deviations = path_differences(known_path, baseline)
                baseline_diff = bool(deviations)
                if baseline_diff and not target_data.get("baseline_mismatch"):
                    await log_event("baseline_deviation", "critical",
                                    "Route deviates from locked baseline: " + "; ".join(deviations),
                                    ip=target_ip, name=name)
                elif not baseline_diff and target_data.get("baseline_mismatch"):
                    await log_event("baseline_restored", "success",
                                    "Route back on locked baseline",
                                    ip=target_ip, name=name)
                target_data["baseline_mismatch"] = baseline_diff

            # Hops that were silent when the baseline was locked adopt the first IP heard there
            if baseline and has_answers:
                known = target_data["known_path"]
                for i in range(min(len(baseline), len(known))):
                    if baseline[i] is None and known[i] is not None:
                        baseline[i] = known[i]

            payload = {
                "type": "traceroute_update",
                "slot": slot,
                "target_ip": target_ip,
                "hops": [h.model_dump() for h in hops],
                "path_changed": path_changed,
                "baseline_diff": baseline_diff,
                "baseline_path": baseline,
                "traceroute_interval": state.traceroute_interval,
            }
            await ws_manager.broadcast(payload)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Traceroute Error] Worker exception for {target_ip}: {e}")

        try:
            await asyncio.sleep(state.traceroute_interval)
        except asyncio.CancelledError:
            break


# API Routes


@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/profiles")
async def get_profiles():
    profiles = state.profile_mgr.list_available_profiles()
    return {"profiles": profiles, "active_profile": state.active_profile_name}


@app.get("/api/state")
async def get_state():
    return state.sync_payload()


@app.get("/api/events")
async def get_events(limit: int = 500):
    return {"events": event_log.recent(max(1, min(limit, 2000))), "unsaved": event_log.unsaved_count}


@app.get("/api/events/export")
async def export_events():
    filename = f"MonitorIT_events_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=event_log.to_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/events/clear")
async def clear_events():
    """Clears the dashboard view only; the daily CSV files in logs/ are kept."""
    event_log.clear()
    await ws_manager.broadcast(event_history_payload())
    return {"status": "success"}


@app.post("/api/profiles/load/{profile_name}")
async def load_profile(profile_name: str):
    data = state.profile_mgr.load_profile(profile_name)
    if not data:
        raise HTTPException(status_code=404, detail="Profile not found")

    # Stop and clear all running traceroute background workers on profile switch
    state.stop_all_traceroutes()

    state.active_profile_name = data.profile_name
    state.ping_interval = data.ping_interval
    state.traceroute_interval = data.traceroute_interval
    state.down_threshold = clamp_down_threshold(data.down_threshold)
    state.initialize_hosts(data.hosts)

    await ws_manager.broadcast(state.sync_payload())
    await ws_manager.broadcast(state.ping_payload())
    await log_event("profile_loaded", "info",
                    f"Profile '{data.profile_name}' loaded ({len(data.hosts)} hosts)")

    return {
        "status": "success",
        "profile_name": data.profile_name,
        "host_count": len(data.hosts),
        "ping_interval": state.ping_interval,
        "traceroute_interval": state.traceroute_interval,
        "down_threshold": state.down_threshold,
    }


@app.post("/api/profiles/save")
async def save_profile(profile: ProfileData):
    saved_name = state.profile_mgr.save_profile(profile)
    if not saved_name:
        raise HTTPException(status_code=400, detail="Failed to save profile (check the profile name)")

    state.active_profile_name = saved_name
    await ws_manager.broadcast(state.sync_payload())
    return {"status": "success", "profile_name": saved_name}


@app.post("/api/hosts/parse")
async def parse_hosts(req: ParseHostsRequest):
    # Parsing may resolve hostnames through DNS, so keep it off the event loop
    parsed = await asyncio.to_thread(ProfileManager.parse_raw_text, req.raw_text)
    return {
        "status": "success",
        "count": len(parsed.hosts),
        "hosts": [h.model_dump() for h in parsed.hosts],
        "skipped": parsed.skipped,
    }


@app.post("/api/hosts/set")
async def set_active_hosts(req: SetHostsRequest):
    """Sets or appends active host grid."""
    if req.append:
        before = len(state.hosts)
        state.add_hosts(req.hosts)
        message = f"Added {len(state.hosts) - before} hosts ({len(state.hosts)} total)"
    else:
        state.initialize_hosts(req.hosts)
        message = f"Host list replaced ({len(state.hosts)} hosts)"
    await ws_manager.broadcast(state.ping_payload())
    await log_event("hosts_changed", "info", message)
    return {"status": "success", "count": len(state.hosts)}


@app.post("/api/hosts/delete")
async def delete_host(req: DeleteHostRequest):
    """Removes a host from active monitoring state without resetting other stats."""
    ip = req.ip
    name = state.host_name(ip)

    state.hosts = [h for h in state.hosts if h.ip != ip]
    if ip in state.host_stats:
        del state.host_stats[ip]

    if state.stop_traceroute(ip) is not None:
        await ws_manager.broadcast(state.sync_payload())
    await log_event("host_removed", "info", "Host removed from monitoring", ip=ip, name=name)

    return {"status": "success", "deleted_ip": ip, "remaining_count": len(state.hosts)}


@app.post("/api/hosts/rename")
async def rename_host(req: RenameHostRequest):
    """Updates friendly name for a host in memory so ping broadcasts reflect it."""
    for h in state.hosts:
        if h.ip == req.ip:
            h.name = req.name
            break

    if req.ip in state.host_stats:
        state.host_stats[req.ip]["name"] = req.name

    return {"status": "success", "ip": req.ip, "name": req.name}


@app.post("/api/config/intervals")
async def set_intervals(config: IntervalConfig):
    changes = []
    if config.ping_interval != state.ping_interval:
        changes.append(f"ping interval {state.ping_interval}s -> {config.ping_interval}s")
    if config.traceroute_interval != state.traceroute_interval:
        changes.append(f"traceroute interval {state.traceroute_interval}s -> {config.traceroute_interval}s")
    if config.down_threshold != state.down_threshold:
        changes.append(f"down after {state.down_threshold} -> {config.down_threshold} failed pings")

    state.ping_interval = config.ping_interval
    state.traceroute_interval = config.traceroute_interval
    state.down_threshold = config.down_threshold
    await ws_manager.broadcast(state.sync_payload())
    if changes:
        await log_event("settings_changed", "info", "Settings: " + "; ".join(changes))
    return {
        "status": "success",
        "ping_interval": state.ping_interval,
        "traceroute_interval": state.traceroute_interval,
        "down_threshold": state.down_threshold,
    }


@app.post("/api/traceroute/start")
async def start_traceroute(req: TracerouteStartRequest):
    target_ip = req.target_ip

    if target_ip in state.active_traceroutes:
        return {"status": "already_running", "target_ip": target_ip, "slot": state.active_traceroutes[target_ip]["slot"]}

    assigned_slot = req.slot
    if not assigned_slot:
        used_slots = [data["slot"] for data in state.active_traceroutes.values()]
        if 1 not in used_slots:
            assigned_slot = 1
        elif 2 not in used_slots:
            assigned_slot = 2
        else:
            raise HTTPException(status_code=400, detail="Both traceroute slots (Slot 1 and Slot 2) are full. Stop one target first.")

    to_remove = [ip for ip, data in state.active_traceroutes.items() if data["slot"] == assigned_slot]
    for ip in to_remove:
        state.stop_traceroute(ip)

    # Register before the task starts so the worker's loop condition sees it
    state.active_traceroutes[target_ip] = {
        "slot": assigned_slot,
        "baseline": [],
        "known_path": [],
    }
    state.active_traceroutes[target_ip]["task"] = asyncio.create_task(
        live_traceroute_worker(target_ip, assigned_slot)
    )

    await ws_manager.broadcast(state.sync_payload())
    await log_event("trace_started", "info", f"Route monitoring started (slot {assigned_slot})",
                    ip=target_ip, name=state.host_name(target_ip))
    return {"status": "success", "target_ip": target_ip, "slot": assigned_slot}


@app.post("/api/traceroute/stop")
async def stop_traceroute(req: TracerouteTargetRequest):
    target_ip = req.target_ip
    slot = state.stop_traceroute(target_ip)
    if slot is not None:
        await ws_manager.broadcast(state.sync_payload())
        await log_event("trace_stopped", "info", f"Route monitoring stopped (slot {slot})",
                        ip=target_ip, name=state.host_name(target_ip))
        return {"status": "success", "stopped_target": target_ip, "slot": slot}

    return {"status": "not_found", "target_ip": target_ip}


@app.post("/api/traceroute/baseline/lock")
async def lock_baseline(req: TracerouteTargetRequest):
    target_ip = req.target_ip
    if target_ip not in state.active_traceroutes:
        raise HTTPException(status_code=404, detail="Target IP not actively monitored")

    data = state.active_traceroutes[target_ip]
    known_path = data.get("known_path", [])
    if not any(known_path):
        raise HTTPException(status_code=400, detail="No active traceroute path available to lock")

    data["baseline"] = list(known_path)
    data["baseline_mismatch"] = False
    route = " > ".join(ip or "*" for ip in known_path)
    await log_event("baseline_locked", "info", f"Baseline locked: {route}",
                    ip=target_ip, name=state.host_name(target_ip))
    return {"status": "success", "target_ip": target_ip, "baseline_path": data["baseline"]}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # WebSockets bypass the browser's same-origin policy, so check Origin explicitly;
    # otherwise any open web page could read the monitored host inventory
    if not _origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=1008)
        return

    await ws_manager.connect(websocket)
    try:
        await websocket.send_json(state.sync_payload())
        await websocket.send_json(state.ping_payload())
        await websocket.send_json(event_history_payload())

        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)
