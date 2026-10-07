"""Smoke test against the REAL Windows iphlpapi.dll (skipped on other platforms).

Every other test uses the simulated DLL from conftest.py, so this is the one check that
the real Windows ICMP layer still works. It runs in a separate Python process because
this test session has replaced ctypes.windll with the fake.
"""
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.windows,
    pytest.mark.skipif(sys.platform != "win32", reason="needs the real Windows iphlpapi.dll"),
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The project folder is passed in explicitly: the portable embeddable Python (python\)
# ignores the current directory when importing, so "import app" would otherwise fail
SCRIPT = """
import asyncio, sys
sys.path.insert(0, sys.argv[1])
from app.icmp_engine import raw_icmp_echo, raw_icmp_ttl_probe, async_run_traceroute_sweep

ping = raw_icmp_echo("127.0.0.1")
assert ping.is_alive, f"ping 127.0.0.1 failed: status {ping.status_code}"

hop = raw_icmp_ttl_probe("127.0.0.1", 1)
assert hop.is_destination, f"TTL probe to 127.0.0.1 failed: status {hop.status_code}"

hops = asyncio.run(async_run_traceroute_sweep("127.0.0.1"))
assert len(hops) == 1 and hops[0].is_destination, hops
print("OK")
"""


def test_real_iphlpapi_ping_and_traceroute_to_loopback():
    result = subprocess.run([sys.executable, "-c", SCRIPT, str(PROJECT_ROOT)], cwd=PROJECT_ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and "OK" in result.stdout, result.stderr
