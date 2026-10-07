"""Small utilities shared by the test modules."""
import threading
import time

WS_URL = "ws://127.0.0.1:8000/ws"
ORIGIN = "http://127.0.0.1:8000"


def wait_for(predicate, timeout=10.0, interval=0.05, message="condition"):
    """Polls predicate() until it returns something truthy; fails the test on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise AssertionError(f"Timed out after {timeout}s waiting for {message}")


def receive_json(ws, timeout=5.0):
    """ws.receive_json() with a time limit, so a missing message fails instead of hanging."""
    result = {}

    def receive():
        try:
            result["message"] = ws.receive_json()
        except Exception as e:  # surfaced to the test below
            result["error"] = e

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise AssertionError(f"No WebSocket message within {timeout}s")
    if "error" in result:
        raise result["error"]
    return result["message"]


def traceroute_updates(broadcasts, target_ip):
    return [m for m in broadcasts if m["type"] == "traceroute_update" and m["target_ip"] == target_ip]


def wait_for_sweeps(broadcasts, target_ip, count, timeout=None):
    """Waits until at least `count` traceroute sweeps for target_ip have been broadcast."""
    # A sweep with a silent hop takes ~2 s (1 s probe timeout + 1 s interval)
    timeout = timeout or count * 3 + 5
    return wait_for(
        lambda: (lambda u: u if len(u) >= count else None)(traceroute_updates(broadcasts, target_ip)),
        timeout=timeout,
        message=f"{count} traceroute sweeps to {target_ip}",
    )


def events_of(event_log, event_type):
    return [e for e in event_log.events if e["type"] == event_type]
