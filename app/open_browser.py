"""Opens the dashboard once the MonitorIT server is accepting connections.

LaunchIT.bat starts this in the background so the browser never races uvicorn's
startup and lands on a "connection refused" page. Standard library only, so it
runs under the portable embedded Python without any packages.

Usage: open_browser.py [host] [port]
"""
import socket
import sys
import time
import webbrowser

STARTUP_TIMEOUT_SECONDS = 60


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                webbrowser.open(f"http://{host}:{port}")
                return
        except OSError:
            time.sleep(0.5)


if __name__ == "__main__":
    main()
