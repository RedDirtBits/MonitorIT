import ipaddress
import json
import os
import re
from typing import Dict, List, Optional
from pydantic import BaseModel


class HostEntry(BaseModel):
    ip: str
    name: str
    group: Optional[str] = "Default"


class ProfileData(BaseModel):
    profile_name: str
    ping_interval: int = 3
    traceroute_interval: int = 1
    hosts: List[HostEntry] = []


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
        file_path = os.path.join(self.profiles_dir, f"{profile_name}.json")
        if not os.path.exists(file_path):
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return ProfileData(**data)
        except Exception as e:
            print(f"[Error] Failed to load profile '{profile_name}': {e}")
            return None

    def save_profile(self, profile: ProfileData) -> bool:
        """Saves a ProfileData model to a JSON file in profiles directory."""
        # Sanitize profile filename
        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", profile.profile_name).strip()
        file_path = os.path.join(self.profiles_dir, f"{safe_filename}.json")

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(profile.model_dump(), f, indent=2)
            return True
        except Exception as e:
            print(f"[Error] Failed to save profile '{profile.profile_name}': {e}")
            return False

    @staticmethod
    def parse_raw_text(raw_text: str) -> List[HostEntry]:
        """
        Parses raw text containing IPs, Hostnames, PingInfoView tabs/spaces, or CIDR blocks.
        Preserves multi-word friendly names (e.g., '10.10.10.10 Core Router Vlan101').
        """
        parsed_hosts: List[HostEntry] = []
        seen_ips = set()
        lines = raw_text.strip().splitlines()

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Check if line is CIDR subnet (e.g., 198.51.100.0/28)
            if "/" in line and "\t" not in line and "," not in line:
                try:
                    net = ipaddress.ip_network(line, strict=False)
                    for ip in net.hosts():
                        ip_str = str(ip)
                        if ip_str not in seen_ips:
                            seen_ips.add(ip_str)
                            parsed_hosts.append(
                                HostEntry(ip=ip_str, name=f"Subnet-{ip_str}")
                            )
                    continue
                except ValueError:
                    pass

            # Delimit tokens on tabs, commas, or whitespace
            words = re.split(r"[\t,]+|\s+", line)
            words = [w.strip() for w in words if w.strip()]

            if not words:
                continue

            ip_found: Optional[str] = None
            name_words: List[str] = []

            for word in words:
                try:
                    ip_obj = ipaddress.ip_address(word)
                    if not ip_found:
                        ip_found = str(ip_obj)
                except ValueError:
                    name_words.append(word)

            if ip_found and ip_found not in seen_ips:
                seen_ips.add(ip_found)
                full_name = " ".join(name_words) if name_words else ip_found
                parsed_hosts.append(
                    HostEntry(
                        ip=ip_found,
                        name=full_name,
                    )
                )

        return parsed_hosts