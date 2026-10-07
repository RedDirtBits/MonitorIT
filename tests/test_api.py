"""REST API, WebSocket and localhost security, against a running test server."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from helpers import ORIGIN, WS_URL, events_of, receive_json, wait_for


# --- Localhost-only security ---------------------------------------------------------

def test_foreign_host_header_is_rejected(client):
    """Blocks DNS rebinding: a web page pointing its own domain at 127.0.0.1."""
    assert client.get("/api/state", headers={"host": "evil.example:8000"}).status_code == 400


def test_cross_site_write_is_rejected(client):
    r = client.post("/api/profiles/load/sample_profile", headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_same_origin_write_is_allowed(client):
    assert client.post("/api/profiles/load/sample_profile", headers={"origin": ORIGIN}).status_code == 200


def test_cross_site_websocket_is_closed(client):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(WS_URL, headers={"origin": "http://evil.example"}) as ws:
            receive_json(ws)
    assert closed.value.code == 1008


# --- WebSocket ---------------------------------------------------------------------

def test_new_dashboard_receives_state_hosts_and_history(client):
    with client.websocket_connect(WS_URL, headers={"origin": ORIGIN}) as ws:
        sync, hosts, history = receive_json(ws), receive_json(ws), receive_json(ws)
    assert sync["type"] == "state_sync" and sync["slots"] == {"1": None, "2": None}
    assert sync["down_threshold"] == 2
    assert hosts["type"] == "ping_update" and {h["ip"] for h in hosts["hosts"]} == {"127.0.0.1", "192.0.2.50"}
    assert history["type"] == "event_history" and history["log_file"].endswith(".csv")
    assert history["events"][0]["type"] == "app_start"


# --- Ping loop ---------------------------------------------------------------------

def test_ping_loop_marks_hosts_and_logs_unreachable(server, client):
    """Startup profile: 127.0.0.1 answers; 192.0.2.50 is dead (Down After 2, 1 s interval)."""
    stats = server.state.host_stats
    wait_for(lambda: stats["127.0.0.1"]["status"] == "Online", message="loopback online")
    wait_for(lambda: stats["192.0.2.50"]["status"] == "Offline", timeout=8, message="dead host offline")
    unreachable = events_of(server.event_log, "host_unreachable")
    assert len(unreachable) == 1 and unreachable[0]["ip"] == "192.0.2.50"


# --- Settings and profiles ------------------------------------------------------------

def test_intervals_and_threshold_are_validated(client):
    for bad in ({"ping_interval": 0, "traceroute_interval": 1},
                {"ping_interval": 3, "traceroute_interval": 1, "down_threshold": 11}):
        assert client.post("/api/config/intervals", json=bad).status_code == 422


def test_settings_change_is_applied_and_logged(server, client):
    r = client.post("/api/config/intervals",
                    json={"ping_interval": 3, "traceroute_interval": 1, "down_threshold": 3})
    assert r.status_code == 200 and client.get("/api/state").json()["down_threshold"] == 3
    logged = events_of(server.event_log, "settings_changed")
    assert "ping interval 1s -> 3s" in logged[0]["message"]
    assert "down after 2 -> 3" in logged[0]["message"]


def test_profile_save_sanitizes_name_and_keeps_threshold(server, client):
    r = client.post("/api/profiles/save",
                    json={"profile_name": "..\\Night: Window", "down_threshold": 5, "hosts": []})
    assert r.status_code == 200 and r.json()["profile_name"] == "_Night_ Window"
    loaded = client.post("/api/profiles/load/_Night_ Window").json()
    assert loaded["down_threshold"] == 5 and server.state.down_threshold == 5


def test_bad_profile_names_are_refused(client):
    assert client.post("/api/profiles/save", json={"profile_name": "...", "hosts": []}).status_code == 400
    assert client.post("/api/profiles/load/..%5C..%5Cwindows").status_code == 404


# --- Hosts ---------------------------------------------------------------------------

def test_parse_endpoint_reports_skipped_lines(client):
    j = client.post("/api/hosts/parse", json={"raw_text": "192.0.2.1 A\n10.0.0.0/8\n2001:db8::1"}).json()
    assert j["count"] == 1 and len(j["skipped"]) == 2


def test_adding_and_removing_hosts_is_logged(server, client):
    client.post("/api/hosts/set", json={"hosts": [{"ip": "192.0.2.60", "name": "New"}], "append": True})
    client.post("/api/hosts/delete", json={"ip": "192.0.2.60"})
    assert events_of(server.event_log, "hosts_changed")[0]["message"] == "Added 1 hosts (3 total)"
    removed = events_of(server.event_log, "host_removed")[0]
    assert removed["ip"] == "192.0.2.60" and removed["name"] == "New"


# --- Event log endpoints --------------------------------------------------------------

def test_event_export_and_clear(server, client):
    r = client.get("/api/events/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert r.text.lstrip("﻿").startswith("Timestamp,Severity")

    client.post("/api/events/clear")
    assert client.get("/api/events").json()["events"] == []
    assert server.event_log.current_file().exists()  # files on disk are kept


def test_shutdown_is_logged_to_disk(server):
    with TestClient(server.app, base_url="http://127.0.0.1:8000"):
        pass  # start the server, then stop it
    assert server.event_log.events[-1]["type"] == "app_stop"
    assert "app_stop" in server.event_log.current_file().read_text(encoding="utf-8-sig")
