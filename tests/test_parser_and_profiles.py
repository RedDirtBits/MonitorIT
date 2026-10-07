"""Host import parsing and profile file handling (no network or server needed)."""
import json

import pytest

from app import profile_manager
from app.profile_manager import ProfileData, ProfileManager, safe_profile_name

FAKE_DNS = {"core-sw01.corp.example": "192.0.2.10", "edge-rtr02.corp.example": "192.0.2.20"}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    """Hostname lookups come from FAKE_DNS so results never depend on the machine's DNS."""
    monkeypatch.setattr(profile_manager, "_resolve_ipv4", lambda name: FAKE_DNS.get(name))


def parse(text):
    return ProfileManager.parse_raw_text(text)


def test_ip_lines_keep_multi_word_names():
    result = parse("192.0.2.1 Core Switch Vlan10\n192.0.2.2, Edge Router\n192.0.2.3\tTabbed Name")
    assert [(h.ip, h.name) for h in result.hosts] == [
        ("192.0.2.1", "Core Switch Vlan10"),
        ("192.0.2.2", "Edge Router"),
        ("192.0.2.3", "Tabbed Name"),
    ]
    assert result.skipped == []


def test_bare_ip_uses_ip_as_name_and_comments_are_ignored():
    result = parse("# a comment\n\n192.0.2.7")
    assert [(h.ip, h.name) for h in result.hosts] == [("192.0.2.7", "192.0.2.7")]


def test_cidr_is_expanded():
    result = parse("192.0.2.0/30")
    assert [h.ip for h in result.hosts] == ["192.0.2.1", "192.0.2.2"]


def test_cidr_size_cap():
    assert len(parse("10.10.0.0/22").hosts) == 1022
    result = parse("10.0.0.0/8")
    assert result.hosts == [] and "subnet too large" in result.skipped[0]


def test_ipv6_is_skipped_with_reason():
    result = parse("2001:db8::1 v6 host\n2001:db8::/64")
    assert result.hosts == []
    assert all("IPv6 is not supported" in s for s in result.skipped) and len(result.skipped) == 2


def test_duplicates_are_reported_but_cidr_overlap_is_silent():
    result = parse("192.0.2.1 First\n192.0.2.1 Again\n192.0.2.0/30")
    assert [h.ip for h in result.hosts] == ["192.0.2.1", "192.0.2.2"]
    assert len(result.skipped) == 1 and "duplicate of 192.0.2.1" in result.skipped[0]


def test_hostnames_are_resolved_in_input_order():
    result = parse("core-sw01.corp.example Core Switch\n192.0.2.5 Plain IP\nedge-rtr02.corp.example")
    assert [(h.ip, h.name) for h in result.hosts] == [
        ("192.0.2.10", "Core Switch"),
        ("192.0.2.5", "Plain IP"),
        ("192.0.2.20", "edge-rtr02.corp.example"),
    ]


def test_unresolvable_and_junk_lines_are_reported():
    result = parse("missing.corp.example Ghost\n??? junk\n12345")
    assert result.hosts == []
    reasons = sorted(result.skipped)
    assert reasons == [
        "12345  (no IPv4 address or hostname found)",  # bare numbers are never hostnames
        "??? junk  (no IPv4 address or hostname found)",
        "missing.corp.example Ghost  (could not resolve 'missing.corp.example')",
    ]


@pytest.mark.parametrize("raw, expected", [
    ("Network Servers", "Network Servers"),
    ("My: Prof?", "My_ Prof_"),
    ("..\\..\\x", "_.._x"),
    ("CON", "_CON"),
    ("con.txt", "_con.txt"),
    ("...", None),
])
def test_safe_profile_name(raw, expected):
    assert safe_profile_name(raw) == expected


def test_profile_save_and_load_round_trip(tmp_path):
    mgr = ProfileManager(str(tmp_path))
    saved = mgr.save_profile(ProfileData(profile_name="Test: Window", down_threshold=3, hosts=[]))
    assert saved == "Test_ Window"
    loaded = mgr.load_profile(saved)
    assert loaded.profile_name == saved and loaded.down_threshold == 3
    assert mgr.list_available_profiles() == [saved]


def test_load_rejects_path_traversal(tmp_path):
    outside = tmp_path / "secret.json"
    outside.write_text(json.dumps({"profile_name": "secret"}), encoding="utf-8")
    mgr = ProfileManager(str(tmp_path / "profiles"))
    assert mgr.load_profile("../secret") is None
    assert mgr.load_profile("..\\secret") is None


def test_old_profiles_without_threshold_default_to_two(tmp_path):
    (tmp_path / "old.json").write_text(json.dumps({"profile_name": "old", "hosts": []}), encoding="utf-8")
    assert ProfileManager(str(tmp_path)).load_profile("old").down_threshold == 2
