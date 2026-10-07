import asyncio
import json
import os
from typing import Dict, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.icmp_engine import (
    async_ping_host,
    async_run_traceroute_sweep,
    PingResult,
    TracerouteHop,
)
from app.profile_manager import ProfileManager, ProfileData, HostEntry


class IntervalConfig(BaseModel):
    ping_interval: int
    traceroute_interval: int


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
    slot: Optional[int] = None  # 1 or 2


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
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)


class AppState:
    """Central state engine for host statuses, background loops, and dual traceroutes."""

    def __init__(self):
        self.profile_mgr = ProfileManager()
        self.active_profile_name: str = "sample_profile"
        self.ping_interval: int = 3
        self.traceroute_interval: int = 1

        self.hosts: List[HostEntry] = []
        self.host_stats: Dict[str, dict] = {}

        # Dual Traceroute State Slots
        # Key: target_ip -> Dict: {"slot": 1/2, "baseline": [...], "previous_path": [...], "task": Task}
        self.active_traceroutes: Dict[str, dict] = {}

        self.ping_task: Optional[asyncio.Task] = None

    def initialize_hosts(self, host_entries: List[HostEntry]):
        """Sets active host list and resets statistics counters."""
        self.hosts = list(host_entries)
        self.host_stats = {}
        for h in host_entries:
            self.host_stats[h.ip] = {
                "ip": h.ip,
                "name": h.name,
                "group": h.group,
                "is_alive": False,
                "rtt_ms": None,
                "sent": 0,
                "received": 0,
                "loss_pct": 0,
                "status": "Pending",
            }

    def add_hosts(self, new_entries: List[HostEntry]):
        """Appends new unique hosts to active list without clearing existing stats."""
        existing_ips = {h.ip for h in self.hosts}
        for h in new_entries:
            if h.ip not in existing_ips:
                self.hosts.append(h)
                existing_ips.add(h.ip)
                self.host_stats[h.ip] = {
                    "ip": h.ip,
                    "name": h.name,
                    "group": h.group,
                    "is_alive": False,
                    "rtt_ms": None,
                    "sent": 0,
                    "received": 0,
                    "loss_pct": 0,
                    "status": "Pending",
                }

    def update_host_ping_stat(self, ip: str, result: PingResult):
        if ip not in self.host_stats:
            return

        stat = self.host_stats[ip]
        stat["sent"] += 1
        stat["is_alive"] = result.is_alive
        stat["rtt_ms"] = result.rtt_ms

        if result.is_alive:
            stat["received"] += 1
            stat["status"] = "Online"
        else:
            stat["status"] = "Offline"

        if stat["sent"] > 0:
            loss = ((stat["sent"] - stat["received"]) / stat["sent"]) * 100
            stat["loss_pct"] = round(loss, 1)


app = FastAPI(title="MonitorIT Engine")
ws_manager = ConnectionManager()
state = AppState()

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
async def startup_event():
    """Initializes default profile and launches background loops on server startup."""
    default_profile = state.profile_mgr.load_profile("sample_profile")
    if default_profile:
        state.active_profile_name = "sample_profile"
        state.ping_interval = default_profile.ping_interval
        state.traceroute_interval = default_profile.traceroute_interval
        state.initialize_hosts(default_profile.hosts)

    state.ping_task = asyncio.create_task(global_ping_loop())


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanly cancels background tasks on shutdown."""
    if state.ping_task:
        state.ping_task.cancel()

    for target_data in state.active_traceroutes.values():
        task = target_data.get("task")
        if task and not task.done():
            task.cancel()


async def global_ping_loop():
    """Background task pinging all active hosts and broadcasting results."""
    while True:
        try:
            if state.hosts:
                tasks = [async_ping_host(h.ip) for h in state.hosts]
                results: List[PingResult] = await asyncio.gather(*tasks)

                for res in results:
                    state.update_host_ping_stat(res.ip, res)

                payload = {
                    "type": "ping_update",
                    "hosts": list(state.host_stats.values()),
                    "ping_interval": state.ping_interval,
                }
                await ws_manager.broadcast(payload)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Loop Error] Ping loop exception: {e}")

        await asyncio.sleep(state.ping_interval)


async def live_traceroute_worker(target_ip: str, slot: int):
    """Background worker executing continuous MTR-style traceroutes for a specific slot."""
    while target_ip in state.active_traceroutes:
        try:
            target_data = state.active_traceroutes[target_ip]
            hops: List[TracerouteHop] = await async_run_traceroute_sweep(target_ip)
            current_path = [h.ip for h in hops if h.ip != "*"]

            path_changed = False
            prev_path = target_data.get("previous_path", [])
            if prev_path and current_path and current_path != prev_path:
                path_changed = True

            target_data["previous_path"] = current_path

            baseline_diff = False
            baseline = target_data.get("baseline", [])
            if baseline and current_path and current_path != baseline:
                baseline_diff = True

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

        await asyncio.sleep(state.traceroute_interval)


# API Routes


@app.get("/")
async def serve_index():
    return FileResponse("app/static/index.html")


@app.get("/api/profiles")
async def get_profiles():
    profiles = state.profile_mgr.list_available_profiles()
    return {"profiles": profiles, "active_profile": state.active_profile_name}


@app.post("/api/profiles/load/{profile_name}")
async def load_profile(profile_name: str):
    data = state.profile_mgr.load_profile(profile_name)
    if not data:
        raise HTTPException(status_code=404, detail="Profile not found")

    # Stop and clear all running traceroute background workers on profile switch
    for target_data in list(state.active_traceroutes.values()):
        task = target_data.get("task")
        if task and not task.done():
            task.cancel()
    state.active_traceroutes.clear()

    state.active_profile_name = profile_name
    state.ping_interval = data.ping_interval
    state.traceroute_interval = data.traceroute_interval
    state.initialize_hosts(data.hosts)

    return {
        "status": "success",
        "profile_name": profile_name,
        "host_count": len(data.hosts),
        "ping_interval": state.ping_interval,
        "traceroute_interval": state.traceroute_interval,
    }


@app.post("/api/profiles/save")
async def save_profile(profile: ProfileData):
    success = state.profile_mgr.save_profile(profile)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save profile")

    state.active_profile_name = profile.profile_name
    return {"status": "success", "profile_name": profile.profile_name}


@app.post("/api/hosts/parse")
async def parse_hosts(req: ParseHostsRequest):
    parsed_hosts = ProfileManager.parse_raw_text(req.raw_text)
    return {"status": "success", "count": len(parsed_hosts), "hosts": [h.model_dump() for h in parsed_hosts]}


@app.post("/api/hosts/set")
async def set_active_hosts(req: SetHostsRequest):
    """Sets or appends active host grid."""
    if req.append:
        state.add_hosts(req.hosts)
    else:
        state.initialize_hosts(req.hosts)
    return {"status": "success", "count": len(state.hosts)}


@app.post("/api/hosts/delete")
async def delete_host(req: DeleteHostRequest):
    """Removes a host from active monitoring state without resetting other stats."""
    ip = req.ip

    state.hosts = [h for h in state.hosts if h.ip != ip]
    if ip in state.host_stats:
        del state.host_stats[ip]

    if ip in state.active_traceroutes:
        task = state.active_traceroutes[ip].get("task")
        if task and not task.done():
            task.cancel()
        del state.active_traceroutes[ip]

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
    state.ping_interval = config.ping_interval
    state.traceroute_interval = config.traceroute_interval
    return {
        "status": "success",
        "ping_interval": state.ping_interval,
        "traceroute_interval": state.traceroute_interval,
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
        task = state.active_traceroutes[ip].get("task")
        if task and not task.done():
            task.cancel()
        del state.active_traceroutes[ip]

    task = asyncio.create_task(live_traceroute_worker(target_ip, assigned_slot))
    state.active_traceroutes[target_ip] = {
        "slot": assigned_slot,
        "baseline": [],
        "previous_path": [],
        "task": task,
    }

    return {"status": "success", "target_ip": target_ip, "slot": assigned_slot}


@app.post("/api/traceroute/stop")
async def stop_traceroute(req: TracerouteTargetRequest):
    target_ip = req.target_ip
    if target_ip in state.active_traceroutes:
        task = state.active_traceroutes[target_ip].get("task")
        if task and not task.done():
            task.cancel()
        slot = state.active_traceroutes[target_ip]["slot"]
        del state.active_traceroutes[target_ip]
        return {"status": "success", "stopped_target": target_ip, "slot": slot}

    return {"status": "not_found", "target_ip": target_ip}


@app.post("/api/traceroute/baseline/lock")
async def lock_baseline(req: TracerouteTargetRequest):
    target_ip = req.target_ip
    if target_ip not in state.active_traceroutes:
        raise HTTPException(status_code=404, detail="Target IP not actively monitored")

    data = state.active_traceroutes[target_ip]
    prev_path = data.get("previous_path", [])
    if not prev_path:
        raise HTTPException(status_code=400, detail="No active traceroute path available to lock")

    data["baseline"] = list(prev_path)
    return {"status": "success", "target_ip": target_ip, "baseline_path": data["baseline"]}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        initial_payload = {
            "type": "ping_update",
            "hosts": list(state.host_stats.values()),
            "ping_interval": state.ping_interval,
        }
        await websocket.send_json(initial_payload)

        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)