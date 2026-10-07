"""Timestamped event log: kept in memory for the dashboard and appended to one CSV
file per day under logs/ as a durable record for reviews and post-mortems."""
import csv
import io
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, List, Optional

CSV_COLUMNS = ["Timestamp", "Severity", "Event", "IP", "Name", "Message"]

# Severities, most to least urgent: critical, warning, success, info
SEVERITIES = ("critical", "warning", "success", "info")


class EventLog:
    def __init__(self, log_dir: Path, max_events: int = 2000):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.events: Deque[dict] = deque(maxlen=max_events)
        self._next_id = 1
        # Events not yet on disk because the day's file was locked (e.g. open in Excel)
        self._unwritten: List[dict] = []
        self._write_error_reported = False
        self._load_today()

    @property
    def unsaved_count(self) -> int:
        return len(self._unwritten)

    def file_for(self, day: str) -> Path:
        return self.log_dir / f"MonitorIT_events_{day}.csv"

    def current_file(self) -> Path:
        return self.file_for(datetime.now().strftime("%Y-%m-%d"))

    def _load_today(self) -> None:
        """Restores today's earlier events so a server restart mid-window keeps its history."""
        path = self.current_file()
        if not path.exists():
            return
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    self._append_memory(
                        row["Timestamp"], row["Severity"], row["Event"],
                        row["IP"] or None, row["Name"] or None, row["Message"],
                    )
        except (OSError, KeyError, csv.Error) as e:
            print(f"[Event Log] Could not read {path.name}: {e}")

    def _append_memory(self, ts: str, severity: str, event_type: str,
                       ip: Optional[str], name: Optional[str], message: str) -> dict:
        event = {
            "id": self._next_id,
            "ts": ts,
            "severity": severity,
            "type": event_type,
            "ip": ip,
            "name": name,
            "message": message,
        }
        self._next_id += 1
        self.events.append(event)
        return event

    def record(self, event_type: str, severity: str, message: str,
               ip: Optional[str] = None, name: Optional[str] = None) -> dict:
        """Adds an event and writes it to today's CSV file."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        event = self._append_memory(ts, severity, event_type, ip, name, message)
        self._unwritten.append(event)
        self.flush()
        return event

    def flush(self) -> None:
        """Appends queued events to their day's CSV file.

        Excel locks a CSV it has open, so a failed write keeps the events queued; the
        ping loop calls this every round to retry once the file is closed.
        """
        while self._unwritten:
            day = self._unwritten[0]["ts"][:10]
            batch = [e for e in self._unwritten if e["ts"][:10] == day]
            path = self.file_for(day)
            try:
                new_file = not path.exists() or path.stat().st_size == 0
                # BOM only at the start of a new file, so Excel opens it as UTF-8
                encoding = "utf-8-sig" if new_file else "utf-8"
                with open(path, "a", newline="", encoding=encoding) as f:
                    writer = csv.writer(f)
                    if new_file:
                        writer.writerow(CSV_COLUMNS)
                    writer.writerows(_csv_row(e) for e in batch)
            except OSError as e:
                if not self._write_error_reported:
                    print(f"[Event Log] Cannot write {path} ({e}). "
                          "Will retry; close the file if it is open in Excel.")
                    self._write_error_reported = True
                return
            self._write_error_reported = False
            self._unwritten = [e for e in self._unwritten if e["ts"][:10] != day]

    def recent(self, limit: int = 500) -> List[dict]:
        """Newest `limit` events, oldest first."""
        return list(self.events)[-limit:]

    def clear(self) -> None:
        """Clears the in-memory view only; the CSV files on disk are kept."""
        self.events.clear()

    def to_csv(self) -> str:
        """In-memory events as CSV text (with BOM for Excel), oldest first."""
        buf = io.StringIO()
        buf.write("﻿")
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(_csv_row(e) for e in self.events)
        return buf.getvalue()


def _csv_row(e: dict) -> list:
    return [e["ts"], e["severity"], e["type"], e["ip"] or "", e["name"] or "", e["message"]]


def format_duration(seconds: float) -> str:
    """47 -> '47s', 192 -> '3m 12s', 3850 -> '1h 04m 10s'."""
    seconds = int(round(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
