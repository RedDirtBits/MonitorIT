"""Event log: daily CSV files, Excel file locks, reload after restart."""
import builtins

from app import event_log as event_log_module
from app.event_log import EventLog

BOM = b"\xef\xbb\xbf"


def test_csv_starts_with_one_bom_and_a_header(tmp_path):
    log = EventLog(tmp_path)
    log.record("host_down", "critical", "Host DOWN", ip="192.0.2.1")
    log.record("host_up", "success", "Host UP after 5s outage", ip="192.0.2.1")
    raw = log.current_file().read_bytes()
    assert raw.startswith(BOM) and raw.count(BOM) == 1
    assert raw[len(BOM):].decode("utf-8").splitlines()[0] == "Timestamp,Severity,Event,IP,Name,Message"


def test_locked_file_queues_events_until_it_is_closed(tmp_path, monkeypatch):
    log = EventLog(tmp_path)
    log.record("app_start", "info", "MonitorIT started")
    path = str(log.current_file())

    def locked_open(file, mode="r", *args, **kwargs):
        if str(file) == path and "a" in mode:
            raise PermissionError(13, "simulated Excel lock")
        return builtins.open(file, mode, *args, **kwargs)

    monkeypatch.setattr(event_log_module, "open", locked_open, raising=False)
    log.record("host_down", "critical", "Host DOWN", ip="192.0.2.1")
    log.record("route_change", "warning", "Route changed", ip="203.0.113.9")
    assert log.unsaved_count == 2 and len(log.events) == 3  # still shown on screen

    monkeypatch.undo()  # "Excel closed"
    log.flush()
    assert log.unsaved_count == 0
    assert len(log.current_file().read_text(encoding="utf-8-sig").splitlines()) == 4


def test_restart_restores_todays_events_intact(tmp_path):
    first = EventLog(tmp_path)
    first.record("host_down", "critical", "Host DOWN, with a comma", ip="192.0.2.1", name='Core "SW"')
    first.record("baseline_locked", "info", "Baseline locked")

    restarted = EventLog(tmp_path)
    assert [e["type"] for e in restarted.events] == ["host_down", "baseline_locked"]
    assert restarted.events[0]["name"] == 'Core "SW"'
    assert restarted.events[0]["message"] == "Host DOWN, with a comma"


def test_clear_view_keeps_the_file(tmp_path):
    log = EventLog(tmp_path)
    log.record("app_start", "info", "MonitorIT started")
    log.clear()
    assert len(log.events) == 0 and log.current_file().exists()


def test_export_contains_the_current_view(tmp_path):
    log = EventLog(tmp_path)
    log.record("host_down", "critical", "Host DOWN", ip="192.0.2.1")
    exported = log.to_csv()
    assert exported.startswith("﻿Timestamp,Severity")
    assert "host_down" in exported
