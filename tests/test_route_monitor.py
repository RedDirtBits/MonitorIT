"""Live route monitoring: real traceroute workers running 1-second sweeps.

These are the slowest tests (several seconds each); skip them with: pytest -m "not slow"
"""
import pytest

from helpers import ORIGIN, WS_URL, events_of, receive_json, traceroute_updates, wait_for, wait_for_sweeps

pytestmark = pytest.mark.slow

DEST = "203.0.113.9"
ROUTERS = ["198.51.100.1", "198.51.100.2", "198.51.100.3"]


@pytest.fixture
def route(net, client):
    """DEST behind three routers, added to the host list so events carry its name."""
    net.add_route(DEST, ROUTERS)
    client.post("/api/hosts/set", json={"hosts": [{"ip": DEST, "name": "Target"}], "append": True})
    return net


def start(client, slot=1):
    r = client.post("/api/traceroute/start", json={"target_ip": DEST, "slot": slot})
    assert r.status_code == 200 and r.json()["slot"] == slot


def lock_baseline(client):
    """Locks once the first sweep has produced a path."""
    wait_for(lambda: client.post("/api/traceroute/baseline/lock", json={"target_ip": DEST}).status_code == 200,
             message="baseline lock")


def test_rate_limited_hop_raises_no_false_alerts(server, client, broadcasts, route):
    route.rate_limit(DEST, 2, answer_every=3)  # hop 2 answers one probe in three
    start(client)
    lock_baseline(client)
    updates = wait_for_sweeps(broadcasts, DEST, 7)

    assert not any(u["path_changed"] or u["baseline_diff"] for u in updates)
    assert not events_of(server.event_log, "route_change")
    assert not events_of(server.event_log, "baseline_deviation")
    # Hop 2 was silent when the baseline was locked; it adopts the router once heard
    assert server.state.active_traceroutes[DEST]["baseline"][1] == ROUTERS[1]
    # Its loss stops at hop 3, so it is classed as rate limiting
    assert updates[-1]["hop_stats"][1]["loss_kind"] == "rate_limited"


def test_reroute_alerts_once_then_recovers(server, client, broadcasts, route):
    start(client)
    wait_for_sweeps(broadcasts, DEST, 2)
    lock_baseline(client)

    route.routes[DEST][1] = "192.0.2.99"  # hop 2 moves to another router
    wait_for(lambda: any(u["baseline_diff"] for u in traceroute_updates(broadcasts, DEST)),
             message="baseline deviation")
    wait_for_sweeps(broadcasts, DEST, len(traceroute_updates(broadcasts, DEST)) + 2)

    deviations = events_of(server.event_log, "baseline_deviation")
    changes = events_of(server.event_log, "route_change")
    assert len(deviations) == 1 and f"hop 2: {ROUTERS[1]} -> 192.0.2.99" in deviations[0]["message"]
    assert len(changes) == 1 and changes[0]["name"] == "Target"

    route.routes[DEST][1] = ROUTERS[1]
    wait_for(lambda: events_of(server.event_log, "baseline_restored"), message="baseline restored")
    assert any(m["type"] == "events" and m["events"][0]["type"] == "baseline_restored" for m in broadcasts)


def test_hop_stats_ride_along_and_reset_per_slot(server, client, broadcasts, route):
    start(client)
    lock_baseline(client)
    updates = wait_for_sweeps(broadcasts, DEST, 4)
    latest = updates[-1]
    assert len(latest["hop_stats"]) == len(latest["hops"]) and latest["hop_stats"][0]["sent"] >= 3

    seen = len(updates)
    r = client.post("/api/traceroute/stats/reset", json={"target_ip": DEST})
    assert r.status_code == 200
    after = wait_for_sweeps(broadcasts, DEST, seen + 2)[-1]
    assert after["hop_stats"][0]["sent"] <= 2
    assert server.state.active_traceroutes[DEST]["baseline"]  # baseline untouched
    assert events_of(server.event_log, "trace_stats_reset")[0]["message"] == "Hop statistics reset (slot 1)"

    missing = client.post("/api/traceroute/stats/reset", json={"target_ip": "192.0.2.44"})
    assert missing.status_code == 404


def test_refreshed_dashboard_learns_the_running_slot(client, route):
    start(client, slot=2)
    with client.websocket_connect(WS_URL, headers={"origin": ORIGIN}) as ws:
        sync = receive_json(ws)
    assert sync["slots"] == {"1": None, "2": DEST}


def test_starting_a_target_in_a_used_slot_replaces_it(server, client, route):
    start(client, slot=1)
    other = "203.0.113.20"
    route.add_route(other, ROUTERS)
    r = client.post("/api/traceroute/start", json={"target_ip": other, "slot": 1})
    assert r.json()["slot"] == 1 and list(server.state.active_traceroutes) == [other]

    assert client.post("/api/traceroute/stop", json={"target_ip": other}).json()["status"] == "success"
    assert not server.state.active_traceroutes
