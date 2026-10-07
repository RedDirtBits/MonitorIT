"""Shared test setup: a scripted stand-in for the Windows iphlpapi.dll.

MonitorIT's ICMP engine calls IcmpSendEcho through ctypes.windll, which only exists on
Windows. Installing FakeNetwork *before* `app` is imported lets the whole application
(thread pools, ping loop, traceroute workers, event log, API, WebSocket) run on any OS
against a deterministic simulated network. The only thing faked is the DLL.

Addresses come from the RFC 5737 documentation ranges (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24), so nothing here refers to a real network.
"""
import ctypes
import json
import time
import types

import pytest


FAKE_HANDLE = 1234


class FakeNetwork:
    """Answers IcmpSendEcho calls from a scripted network.

    - Hosts in `alive` reply to pings.
    - `routes[destination]` lists the routers in front of a destination; a probe whose
      TTL runs out at one of them gets a TTL-expired reply from that router.
    - `answer_every[(destination, ttl)] = n` makes that hop answer only every nth probe,
      the way routers rate-limit traceroute replies.
    - Anything else is silent: the call blocks for the timeout, as Windows does.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.alive = {"127.0.0.1"}
        self.routes = {}
        self.answer_every = {}
        self._probe_counts = {}
        self.create_fails = False

    def add_route(self, destination, routers):
        self.routes[destination] = list(routers)
        self.alive.add(destination)

    def rate_limit(self, destination, ttl, answer_every):
        self.answer_every[(destination, ttl)] = answer_every

    # --- iphlpapi.dll entry points -------------------------------------------------

    def IcmpCreateFile(self):
        return ctypes.c_void_p(-1).value if self.create_fails else FAKE_HANDLE

    def IcmpCloseHandle(self, handle):
        return 1

    def IcmpSendEcho(self, handle, dest, data, size, options, reply_buffer, reply_size, timeout_ms):
        from app import icmp_engine as engine

        if handle != FAKE_HANDLE:
            return 0
        ip = engine._dword_to_ip(dest)
        ttl = options._obj.Ttl
        reply = engine.ICMP_ECHO_REPLY.from_buffer(reply_buffer)
        route = self.routes.get(ip, [])

        if ttl <= len(route):
            key = (ip, ttl)
            every = self.answer_every.get(key)
            if every:
                count = self._probe_counts.get(key, 0) + 1
                self._probe_counts[key] = count
                if count % every:
                    time.sleep(timeout_ms / 1000)
                    return 0
            reply.Address = engine._ip_to_dword(route[ttl - 1])
            reply.Status = engine.IP_TTL_EXPIRED_IN_TRANSIT
            reply.RoundTripTime = ttl
            return 1

        if ip in self.alive:
            reply.Address = dest
            reply.Status = engine.IP_SUCCESS
            reply.RoundTripTime = len(route)
            return 1

        time.sleep(timeout_ms / 1000)
        return 0


NET = FakeNetwork()


class _WindllWithFakeIphlpapi:
    """ctypes.windll replacement: fake iphlpapi, everything else passed through (on Windows)."""

    def __init__(self, real):
        self._real = real
        # Plain functions, not bound methods: the engine sets .restype/.argtypes on them
        self.iphlpapi = types.SimpleNamespace(
            IcmpCreateFile=lambda: NET.IcmpCreateFile(),
            IcmpCloseHandle=lambda *a: NET.IcmpCloseHandle(*a),
            IcmpSendEcho=lambda *a: NET.IcmpSendEcho(*a),
        )

    def __getattr__(self, name):
        if self._real is None:
            raise AttributeError(name)
        return getattr(self._real, name)


ctypes.windll = _WindllWithFakeIphlpapi(getattr(ctypes, "windll", None))

# Imported only now, so the engine binds to the fake DLL
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.event_log import EventLog  # noqa: E402
from app.profile_manager import ProfileManager  # noqa: E402


# Default profile the test server starts with: one live host, one dead host
TEST_PROFILE = {
    "profile_name": "sample_profile",
    "ping_interval": 1,
    "traceroute_interval": 1,
    "down_threshold": 2,
    "hosts": [
        {"ip": "127.0.0.1", "name": "Loopback", "group": "Default"},
        {"ip": "192.0.2.50", "name": "Dead host", "group": "Default"},
    ],
}


@pytest.fixture
def net():
    """The simulated network, reset before and after each test."""
    NET.reset()
    yield NET
    NET.reset()


@pytest.fixture
def server(tmp_path, monkeypatch, net):
    """The app module with fresh state; profiles and logs live in a temp folder."""
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "sample_profile.json").write_text(json.dumps(TEST_PROFILE), encoding="utf-8")

    state = main.AppState()
    state.profile_mgr = ProfileManager(str(profiles_dir))
    monkeypatch.setattr(main, "state", state)
    monkeypatch.setattr(main, "event_log", EventLog(tmp_path / "logs"))
    monkeypatch.setattr(main, "ws_manager", main.ConnectionManager())
    # The ICMP thread pools are module-wide; keep them alive for the next test
    monkeypatch.setattr(main, "shutdown_executors", lambda: None)
    return main


@pytest.fixture
def broadcasts(server, monkeypatch):
    """Every message the server broadcasts to dashboards, in order."""
    messages = []
    original = server.ws_manager.broadcast

    async def record(message):
        messages.append(message)
        await original(message)

    monkeypatch.setattr(server.ws_manager, "broadcast", record)
    return messages


@pytest.fixture
def client(server, broadcasts):
    """A running server (lifespan, ping loop) and an HTTP/WebSocket client for it.

    The base URL must be 127.0.0.1: the app's Host allow-list rejects TestClient's
    default 'testserver'.
    """
    with TestClient(server.app, base_url="http://127.0.0.1:8000") as test_client:
        yield test_client
