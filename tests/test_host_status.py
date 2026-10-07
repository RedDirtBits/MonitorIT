"""Host status, down threshold, outage tracking and RTT statistics (AppState only)."""
import pytest

from app import main
from app.event_log import format_duration
from app.icmp_engine import PingResult

IP = "192.0.2.5"


@pytest.fixture
def host():
    """An AppState with one host and a ping(alive, rtt) helper returning any event."""
    state = main.AppState()
    state.down_threshold = 2
    state.initialize_hosts([main.HostEntry(ip=IP, name="Test host")])

    def ping(alive, rtt=1):
        return state.update_host_ping_stat(
            IP, PingResult(ip=IP, is_alive=alive, rtt_ms=rtt if alive else None,
                           status_code=0 if alive else 11010))

    ping.state = state
    ping.stat = state.host_stats[IP]
    return ping


def backdate_outage(ping, seconds):
    """Pretends the current failure streak began `seconds` earlier."""
    ping.stat["first_failure_at"] -= seconds


def test_never_seen_host_goes_timeout_then_unreachable(host):
    assert host(False) is None and host.stat["status"] == "Timeout"
    event = host(False)
    assert event[0] == "host_unreachable" and host.stat["status"] == "Offline"
    assert host(True)[2] == "Host reachable (first reply)"


def test_single_drop_on_live_host_is_not_an_outage(host):
    host(True)
    assert host(False) is None and host.stat["status"] == "Timeout"
    assert host(True) is None and host.stat["outage_count"] == 0


def test_outage_is_declared_at_threshold_and_reported_on_recovery(host):
    host(True)
    host(False)
    backdate_outage(host, 45)
    event = host(False)
    assert event[:2] == ("host_down", "critical") and host.stat["status"] == "Offline"
    assert host(False) is None  # no repeat while still down
    event = host(True)
    assert event[0] == "host_up" and "45s outage" in event[2]


def test_threshold_of_one_declares_down_on_first_failure(host):
    host.state.down_threshold = 1
    host(True)
    assert host(False)[0] == "host_down"


def test_live_downtime_and_totals(host):
    host(True)
    host(False)
    host(False)
    backdate_outage(host, 30)
    live = host.state.ping_payload()["hosts"][0]
    assert live["outage_count"] == 1
    assert 29 <= live["current_outage_s"] <= 32 and live["downtime_s"] == live["current_outage_s"]

    host(True)
    host(False)
    host(False)
    backdate_outage(host, 10)
    host(True)
    done = host.state.ping_payload()["hosts"][0]
    assert done["current_outage_s"] is None and done["outage_count"] == 2
    assert 39 <= done["downtime_s"] <= 43 and 29 <= done["longest_outage_s"] <= 32


def test_never_online_host_accrues_no_outage(host):
    host(False)
    host(False)
    payload = host.state.ping_payload()["hosts"][0]
    assert payload["last_seen"] is None and payload["outage_count"] == 0 and payload["downtime_s"] == 0


def test_rtt_statistics_ignore_failed_pings(host):
    for rtt in (10, 20, 0):
        host(True, rtt)
    host(False)
    assert (host.stat["rtt_min"], host.stat["rtt_max"], host.stat["rtt_avg"]) == (0, 20, 10.0)
    assert host.stat["last_seen"]


def test_replacing_host_list_resets_statistics(host):
    host(True)
    host.state.initialize_hosts([main.HostEntry(ip=IP, name="Again")])
    assert host.state.host_stats[IP]["sent"] == 0


@pytest.mark.parametrize("seconds, text", [(47, "47s"), (192, "3m 12s"), (3850, "1h 04m 10s")])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text
