"""Route comparison and per-hop statistics (pure functions, no server)."""
from app import main
from app.icmp_engine import TracerouteHop

A, B, C, D = "198.51.100.1", "198.51.100.2", "198.51.100.3", "203.0.113.9"
TARGET = D


# --- Route comparison: silent hops are never a change -------------------------------

def test_silent_hop_is_not_a_change():
    assert not main.paths_differ([A, None, C, D], [A, B, C, D])


def test_all_silent_sweep_is_not_a_change():
    assert not main.paths_differ([None] * 4, [A, B, C, D])


def test_different_router_is_a_change():
    assert main.path_differences([A, "192.0.2.99", C, D], [A, B, C, D]) == [f"hop 2: {B} -> 192.0.2.99"]


def test_shorter_and_longer_routes_are_changes():
    assert main.paths_differ([A, B, D], [A, B, C, D])
    assert main.paths_differ([A, B, C, D], [A, B, D])


def test_known_path_fills_silent_hops_and_follows_new_length():
    assert main.merge_known_path([A, B, C, D], [A, None, C, D]) == [A, B, C, D]
    assert main.merge_known_path([A, B, C, D], [A, None, D]) == [A, B, D]
    assert main.merge_known_path([], [A, None]) == [A, None]


# --- Per-hop statistics ------------------------------------------------------------

def sweep(*hops):
    """sweep(("ip", rtt), (None, 0), ...): one traceroute result; None = no reply."""
    return [
        TracerouteHop(hop_number=i, ip=ip or "*", rtt_ms=rtt if ip else None,
                      is_destination=(ip == TARGET), status_code=0)
        for i, (ip, rtt) in enumerate(hops, 1)
    ]


KNOWN = [A, B, C, D]


def collect(*sweeps, known=KNOWN, target=TARGET):
    stats = []
    for n, s in enumerate(sweeps):
        main.update_hop_stats(stats, s, target, known, 1000.0 + n)
    return stats


def test_hop_loss_and_rtt_figures():
    stats = collect(
        sweep((A, 1), (B, 10), (C, 3), (D, 4)),
        sweep((A, 1), (None, 0), (C, 3), (D, 4)),
        sweep((A, 1), (B, 20), (C, 3), (D, 4)),
        sweep((A, 1), (B, 15), (C, 3), (D, 4)),
    )
    hop2 = main.summarize_hop_stats(stats, destination_known=True)[1]
    assert (hop2["sent"], hop2["received"], hop2["loss_pct"]) == (4, 3, 25.0)
    assert (hop2["last"], hop2["best"], hop2["worst"], hop2["avg"]) == (15, 10, 20, 15.0)
    assert hop2["jitter"] == 7.5  # mean of |20-10| and |15-20|
    assert hop2["ip"] == B


def test_loss_that_stops_at_a_later_hop_is_rate_limiting():
    stats = collect(sweep((A, 1), (None, 0), (C, 3), (D, 4)), sweep((A, 1), (B, 2), (C, 3), (D, 4)))
    summary = main.summarize_hop_stats(stats, destination_known=True)
    assert summary[1]["loss_kind"] == "rate_limited"
    assert summary[0]["loss_kind"] == summary[3]["loss_kind"] == "none"


def test_loss_carried_to_the_destination_is_real():
    # Hop 2 and every hop after it lose a probe
    stats = collect(sweep((A, 1), (None, 0), (None, 0), (None, 0), (D, 5)), sweep((A, 1), (B, 2), (C, 3), (D, 4)))
    summary = main.summarize_hop_stats(stats, destination_known=True)
    assert [s["loss_kind"] for s in summary] == ["none", "real", "real", "real"]


def test_probes_past_the_destination_are_not_counted():
    # Destination silent at TTL 4 but answering the TTL 5 probe: still only 4 hops
    stats = collect(sweep((A, 1), (B, 2), (C, 3), (None, 0), (D, 5)))
    assert len(stats) == 4 and stats[3]["received"] == 0


def test_router_change_restarts_that_hops_counters():
    stats = collect(sweep((A, 1), (B, 2), (C, 3), (D, 4)), sweep((A, 1), ("192.0.2.99", 9), (C, 3), (D, 4)))
    assert stats[1]["ip"] == "192.0.2.99" and stats[1]["sent"] == 1 and stats[1]["since"] == 1001.0
    assert stats[0]["sent"] == 2


def test_shorter_route_drops_stale_hops():
    stats = collect(sweep((A, 1), (B, 2), (C, 3), (D, 4)))
    main.update_hop_stats(stats, sweep((A, 1), (D, 2)), TARGET, [A, D], 2000.0)
    assert len(stats) == 2


def test_unreachable_target_counts_to_last_answering_hop():
    target = "192.0.2.77"
    known = [A, B, None, None]
    stats = collect(sweep((A, 1), (B, 2), (None, 0), (None, 0)),
                    sweep((A, 1), (None, 0), (None, 0), (None, 0)), known=known, target=target)
    summary = main.summarize_hop_stats(stats, destination_known=False)
    assert len(stats) == 2 and summary[1]["loss_kind"] == "unknown"
