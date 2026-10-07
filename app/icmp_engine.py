import asyncio
import ctypes
from ctypes import wintypes
import socket
from typing import List, Optional
from pydantic import BaseModel


class IP_OPTION_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.c_void_p),
    ]


class ICMP_ECHO_REPLY(ctypes.Structure):
    _fields_ = [
        ("Address", wintypes.DWORD),
        ("Status", wintypes.ULONG),
        ("RoundTripTime", wintypes.ULONG),
        ("DataSize", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("Data", ctypes.c_void_p),
        ("Options", IP_OPTION_INFORMATION),
    ]


# Windows IP status constants
IP_SUCCESS = 0
IP_REQ_TIMED_OUT = 11010
IP_TTL_EXPIRED_IN_TRANSIT = 11013

# Load Windows IP Helper DLL
iphlpapi = ctypes.windll.iphlpapi

IcmpCreateFile = iphlpapi.IcmpCreateFile
IcmpCreateFile.restype = wintypes.HANDLE
IcmpCreateFile.argtypes = []

IcmpCloseHandle = iphlpapi.IcmpCloseHandle
IcmpCloseHandle.restype = wintypes.BOOL
IcmpCloseHandle.argtypes = [wintypes.HANDLE]

IcmpSendEcho = iphlpapi.IcmpSendEcho
IcmpSendEcho.restype = wintypes.DWORD
IcmpSendEcho.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.WORD,
    ctypes.POINTER(IP_OPTION_INFORMATION),
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
]


class PingResult(BaseModel):
    ip: str
    is_alive: bool
    rtt_ms: Optional[int] = None
    status_code: int


class TracerouteHop(BaseModel):
    hop_number: int
    ip: str
    rtt_ms: Optional[int] = None
    is_destination: bool
    status_code: int


def _ip_to_dword(ip_str: str) -> int:
    """Converts an IPv4 string (e.g. '192.168.1.1') into a 32-bit integer for Windows API."""
    packed = socket.inet_aton(ip_str)
    return int.from_bytes(packed, byteorder="little")


def _dword_to_ip(dword: int) -> str:
    """Converts a 32-bit Windows API IP integer back to standard IPv4 string."""
    packed = dword.to_bytes(4, byteorder="little")
    return socket.inet_ntoa(packed)


def raw_icmp_echo(
    ip_str: str, ttl: int = 128, timeout_ms: int = 1000
) -> PingResult:
    """Performs a standard unprivileged ICMP Echo Request using iphlpapi.dll."""
    try:
        dest_addr = _ip_to_dword(ip_str)
    except socket.error:
        return PingResult(ip=ip_str, is_alive=False, rtt_ms=None, status_code=-1)

    handle = IcmpCreateFile()
    if handle == wintypes.HANDLE(-1) or not handle:
        return PingResult(ip=ip_str, is_alive=False, rtt_ms=None, status_code=-2)

    try:
        send_data = b"MonitorIT_Probe_Data"
        data_size = len(send_data)

        options = IP_OPTION_INFORMATION()
        options.Ttl = ttl
        options.Tos = 0
        options.Flags = 0
        options.OptionsSize = 0
        options.OptionsData = None

        reply_buffer_size = ctypes.sizeof(ICMP_ECHO_REPLY) + data_size + 8
        reply_buffer = ctypes.create_string_buffer(reply_buffer_size)

        replies = IcmpSendEcho(
            handle,
            dest_addr,
            send_data,
            data_size,
            ctypes.byref(options),
            reply_buffer,
            reply_buffer_size,
            timeout_ms,
        )

        if replies > 0:
            reply = ICMP_ECHO_REPLY.from_buffer(reply_buffer)
            status = reply.Status
            if status == IP_SUCCESS:
                return PingResult(
                    ip=ip_str,
                    is_alive=True,
                    rtt_ms=int(reply.RoundTripTime),
                    status_code=status,
                )
            else:
                return PingResult(
                    ip=ip_str, is_alive=False, rtt_ms=None, status_code=status
                )
        else:
            return PingResult(
                ip=ip_str,
                is_alive=False,
                rtt_ms=None,
                status_code=IP_REQ_TIMED_OUT,
            )
    finally:
        IcmpCloseHandle(handle)


def raw_icmp_ttl_probe(
    ip_str: str, hop: int, timeout_ms: int = 1000
) -> TracerouteHop:
    """Sends an ICMP Echo Request with a restricted TTL to probe intermediate route hops."""
    try:
        dest_addr = _ip_to_dword(ip_str)
    except socket.error:
        return TracerouteHop(
            hop_number=hop,
            ip="*",
            rtt_ms=None,
            is_destination=False,
            status_code=-1,
        )

    handle = IcmpCreateFile()
    if handle == wintypes.HANDLE(-1) or not handle:
        return TracerouteHop(
            hop_number=hop,
            ip="*",
            rtt_ms=None,
            is_destination=False,
            status_code=-2,
        )

    try:
        send_data = b"MonitorIT_TTL_Probe"
        data_size = len(send_data)

        options = IP_OPTION_INFORMATION()
        options.Ttl = hop
        options.Tos = 0
        options.Flags = 0
        options.OptionsSize = 0
        options.OptionsData = None

        reply_buffer_size = ctypes.sizeof(ICMP_ECHO_REPLY) + data_size + 8
        reply_buffer = ctypes.create_string_buffer(reply_buffer_size)

        replies = IcmpSendEcho(
            handle,
            dest_addr,
            send_data,
            data_size,
            ctypes.byref(options),
            reply_buffer,
            reply_buffer_size,
            timeout_ms,
        )

        if replies > 0:
            reply = ICMP_ECHO_REPLY.from_buffer(reply_buffer)
            status = reply.Status
            responding_ip = _dword_to_ip(reply.Address)
            rtt = int(reply.RoundTripTime)

            if status == IP_SUCCESS:
                return TracerouteHop(
                    hop_number=hop,
                    ip=responding_ip,
                    rtt_ms=rtt,
                    is_destination=True,
                    status_code=status,
                )
            elif status == IP_TTL_EXPIRED_IN_TRANSIT:
                return TracerouteHop(
                    hop_number=hop,
                    ip=responding_ip,
                    rtt_ms=rtt,
                    is_destination=False,
                    status_code=status,
                )
            else:
                return TracerouteHop(
                    hop_number=hop,
                    ip="*",
                    rtt_ms=None,
                    is_destination=False,
                    status_code=status,
                )
        else:
            return TracerouteHop(
                hop_number=hop,
                ip="*",
                rtt_ms=None,
                is_destination=False,
                status_code=IP_REQ_TIMED_OUT,
            )
    finally:
        IcmpCloseHandle(handle)


async def async_ping_host(
    ip_str: str, timeout_ms: int = 1000
) -> PingResult:
    """Async thread-pool wrapper for unprivileged ping execution."""
    return await asyncio.to_thread(raw_icmp_echo, ip_str, 128, timeout_ms)


async def async_probe_ttl_hop(
    ip_str: str, hop: int, timeout_ms: int = 1000
) -> TracerouteHop:
    """Async thread-pool wrapper for single TTL hop probe execution."""
    return await asyncio.to_thread(raw_icmp_ttl_probe, ip_str, hop, timeout_ms)


async def async_run_traceroute_sweep(
    target_ip: str, max_hops: int = 30, timeout_ms: int = 1000
) -> List[TracerouteHop]:
    """Executes a full MTR-style traceroute sweep concurrently across all TTL hops."""
    tasks = [async_probe_ttl_hop(target_ip, hop, timeout_ms) for hop in range(1, max_hops + 1)]
    raw_hops: List[TracerouteHop] = await asyncio.gather(*tasks)

    hops: List[TracerouteHop] = []
    for hop_res in raw_hops:
        hops.append(hop_res)
        if hop_res.is_destination:
            break

    return hops