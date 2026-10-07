import ipaddress
import json
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple
from pydantic import BaseModel


# Largest subnet expanded from a CIDR import line (a /22 is 1022 usable hosts)
MAX_CIDR_HOSTS = 1024

# Device names that Windows will not accept as a filename, with or without extension
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# RFC 1123 hostname that contains at least one letter (so bare numbers are never resolved)
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?=.*[A-Za-z])"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$"
)


class HostEntry(BaseModel):
    ip: str
    name: str
    group: Optional[str] = "Default"


class ProfileData(BaseModel):
    profile_name: str
    ping_interval: int = 3
    traceroute_interval: int = 1
    hosts: List[HostEntry] = []


class ParseResult(BaseModel):
    hosts: List[HostEntry] = []
    skipped: List[str] = []


def safe_profile_name(name: str) -> Optional[str]:
    """Reduces a profile name to a safe bare filename stem, or None if nothing usable remains."""
    cleaned = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "_", name).strip().strip(".").strip()
    if not cleaned:
        return None
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _resolve_ipv4(hostname: str) -> Optional[str]:
    """Resolves a DNS/NetBIOS name to its first IPv4 address."""
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return None
    return infos[0][4][0] if infos else None


class ProfileManager:
    def __init__(self, profiles_dir: str = "profiles"):
        self.profiles_dir = profiles_dir
        os.makedirs(self.profiles_dir, exist_ok=True)

    def list_available_profiles(self) -> List[str]:
        """Returns list of all available profile JSON filenames without extension."""
        if not os.path.exists(self.profiles_dir):
            return []

        profiles = []
        for file in os.listdir(self.profiles_dir):
            if file.endswith(".json"):
                profiles.append(os.path.splitext(file)[0])
        return sorted(profiles)

    def load_profile(self, profile_name: str) -> Optional[ProfileData]:
        """Loads a profile JSON file by name."""
        safe_name = safe_profile_name(profile_name)
        if safe_name is None or safe_name != profile_name:
            return None

        file_path = os.path.join(self.profiles_dir, f"{safe_name}.json")
        if not os.path.exists(file_path):
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                profile = ProfileData(**data)
                # The file name is authoritative; the stored name may predate sanitizing
                profile.profile_name = safe_name
                return profile
        except Exception as e:
            print(f"[Error] Failed to load profile '{profile_name}': {e}")
            return None

    def save_profile(self, profile: ProfileData) -> Optional[str]:
        """Saves a ProfileData model to a JSON file in profiles directory.

        Returns the sanitized profile name actually written, or None on failure.
        """
        safe_name = safe_profile_name(profile.profile_name)
        if safe_name is None:
            print(f"[Error] Invalid profile name '{profile.profile_name}'")
            return None

        file_path = os.path.join(self.profiles_dir, f"{safe_name}.json")
        data = profile.model_dump()
        data["profile_name"] = safe_name

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return safe_name
        except Exception as e:
            print(f"[Error] Failed to save profile '{profile.profile_name}': {e}")
            return None

    @staticmethod
    def parse_raw_text(raw_text: str) -> ParseResult:
        """
        Parses raw text containing IPs, Hostnames, PingInfoView tabs/spaces, or CIDR blocks.
        Preserves multi-word friendly names (e.g., '10.10.10.10 Core Router Vlan101').
        Lines that cannot be used are returned in `skipped` with the reason.

        Blocking: hostname lines are resolved through DNS, so call this off the event loop.
        """
        result = ParseResult()
        seen_ips = set()
        # In input order: (original line, ip or None, hostname to resolve or None, name, quiet)
        # quiet entries come from CIDR expansion and drop duplicates without reporting them
        entries: List[Tuple[str, Optional[str], Optional[str], str, bool]] = []
        lines = raw_text.strip().splitlines()

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Check if line is CIDR subnet (e.g., 198.51.100.0/28)
            if "/" in line and "\t" not in line and "," not in line:
                try:
                    net = ipaddress.ip_network(line, strict=False)
                except ValueError:
                    pass
                else:
                    if net.version != 4:
                        result.skipped.append(f"{line}  (IPv6 is not supported)")
                    elif net.num_addresses > MAX_CIDR_HOSTS + 2:
                        result.skipped.append(
                            f"{line}  (subnet too large: max /22, {MAX_CIDR_HOSTS} hosts)"
                        )
                    else:
                        for ip in net.hosts():
                            ip_str = str(ip)
                            entries.append((line, ip_str, None, f"Subnet-{ip_str}", True))
                    continue

            # Delimit tokens on tabs, commas, or whitespace
            words = re.split(r"[\t,]+|\s+", line)
            words = [w.strip() for w in words if w.strip()]

            if not words:
                continue

            ip_found: Optional[str] = None
            ipv6_found = False
            name_words: List[str] = []

            for word in words:
                try:
                    ip_obj = ipaddress.ip_address(word)
                except ValueError:
                    name_words.append(word)
                    continue
                if ip_obj.version != 4:
                    ipv6_found = True
                elif not ip_found:
                    ip_found = str(ip_obj)

            if ip_found:
                full_name = " ".join(name_words) if name_words else ip_found
                entries.append((line, ip_found, None, full_name, False))
            elif ipv6_found:
                result.skipped.append(f"{line}  (IPv6 is not supported)")
            elif _HOSTNAME_RE.match(words[0]):
                hostname = words[0].rstrip(".")
                full_name = " ".join(words[1:]) or hostname
                entries.append((line, None, hostname, full_name, False))
            else:
                result.skipped.append(f"{line}  (no IPv4 address or hostname found)")

        hostnames = sorted({e[2] for e in entries if e[2]})
        resolved = {}
        if hostnames:
            with ThreadPoolExecutor(max_workers=min(32, len(hostnames))) as pool:
                resolved = dict(zip(hostnames, pool.map(_resolve_ipv4, hostnames)))

        for line, ip_str, hostname, name, quiet in entries:
            if hostname:
                ip_str = resolved.get(hostname)
                if ip_str is None:
                    result.skipped.append(f"{line}  (could not resolve '{hostname}')")
                    continue
            if ip_str in seen_ips:
                if not quiet:
                    result.skipped.append(f"{line}  (duplicate of {ip_str})")
                continue
            seen_ips.add(ip_str)
            result.hosts.append(HostEntry(ip=ip_str, name=name))

        return result
