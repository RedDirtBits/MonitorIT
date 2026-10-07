# MonitorIT - Troubleshooting & Bug Log

This document serves as an operational record for tracking errors, unexpected behaviors, root causes, and verified fixes across the lifecycle of the **MonitorIT** project.

---

## Log Entries

### [2026-09-21] - Project Initial Scaffolding

* **Component:** System Scaffolding & Setup
* **Issue Description:** Potential privilege denial when executing raw socket ICMP operations on domain-managed Windows workstations (`OSError: [WinError 10013]`).
* **Root Cause:** Standard Windows security policy restricts raw socket creation (`IPPROTO_ICMP`) to elevated/Administrator accounts.
* **Resolution Strategy:** Implement `icmp_engine.py` using Windows `iphlpapi.dll` via Python's `ctypes` library. Calling `IcmpSendEcho` allows standard, unprivileged domain users to perform ICMP pings and TTL traceroutes without requiring elevation.
* **Verification Method:** Run ping and TTL traceroute unit functions under a standard (non-admin) user prompt.

---

## Troubleshooting Guide

### 1. Port 8000 Already in Use

* **Error Message:** `[Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000): address already in use`
* **Solution:** Close any existing running instances of MonitorIT or Uvicorn, or adjust the port number in `Launch-MonitorIT.bat` to an alternative high port (e.g., `8080` or `50000`).

### 2. Virtual Environment Execution Policy (PowerShell)

* **Error Message:** `File ...\activate.ps1 cannot be loaded because running scripts is disabled on this system.`
* **Solution:** Run PowerShell command: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process` before activating the virtual environment.