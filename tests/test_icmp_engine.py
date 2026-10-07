"""ICMP engine wrappers, run against the simulated iphlpapi.dll."""
import asyncio
import time

from app import icmp_engine as engine

DEST = "203.0.113.9"
ROUTERS = ["198.51.100.1", "198.51.100.2", "198.51.100.3"]


def test_alive_host_replies(net):
    result = engine.raw_icmp_echo("127.0.0.1")
    assert result.is_alive and result.status_code == engine.IP_SUCCESS


def test_dead_host_times_out(net):
    result = engine.raw_icmp_echo("192.0.2.50", timeout_ms=10)
    assert not result.is_alive and result.status_code == engine.IP_REQ_TIMED_OUT


def test_invalid_address_is_rejected(net):
    assert engine.raw_icmp_echo("not-an-ip").status_code == -1


def test_invalid_handle_value_is_detected(net):
    # IcmpCreateFile failing returns INVALID_HANDLE_VALUE, which must not be used
    net.create_fails = True
    assert engine.raw_icmp_echo("127.0.0.1").status_code == -2
    assert engine.raw_icmp_ttl_probe(DEST, 1).status_code == -2


def test_many_dead_hosts_ping_in_parallel(net):
    """A large profile full of dead hosts finishes in about one timeout, not N timeouts."""
    async def ping_round():
        start = time.monotonic()
        await asyncio.gather(*(engine.async_ping_host(f"192.0.2.{i}") for i in range(1, 103)))
        return time.monotonic() - start

    assert asyncio.run(ping_round()) < 1.6


def test_ttl_probe_reports_intermediate_router(net):
    net.add_route(DEST, ROUTERS)
    hop = engine.raw_icmp_ttl_probe(DEST, 2)
    assert hop.ip == ROUTERS[1] and not hop.is_destination
    assert hop.status_code == engine.IP_TTL_EXPIRED_IN_TRANSIT


def test_sweep_stops_at_destination(net):
    net.add_route(DEST, ROUTERS)
    hops = asyncio.run(engine.async_run_traceroute_sweep(DEST))
    assert [h.ip for h in hops] == ROUTERS + [DEST]
    assert hops[-1].is_destination
