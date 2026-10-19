"""2026-10-18 (leak hygiene): fdps_parser no longer logs an aircraft identifier per
message at INFO -- the per-message line is DEBUG without the callsign, and INFO gets one
count summary per FDPS_WRITE_SUMMARY_S."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import ingest.parsers.fdps_parser as fp

SRC = Path(fp.__file__).read_text()


def test_no_identifier_in_the_per_message_line():
    assert 'log.info("fdps: wrote flight_event for' not in SRC
    m = re.search(r'log\.debug\("fdps: wrote flight_event \(%s -> %s, source=%s\)",\s*'
                  r'parsed\.get\("origin"\), parsed\.get\("destination"\), parsed\.get\("source"\)\)', SRC)
    assert m, "the per-message line must be DEBUG and name no aircraft"


def test_periodic_count_summary(monkeypatch, caplog):
    t = [1000.0]
    monkeypatch.setattr(fp.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(fp, "_write_window_start", 1000.0)
    fp._write_counts.clear()
    with caplog.at_level(logging.INFO, logger=fp.log.name):
        for src in ("FH", "TH", "TH"):
            fp._note_flight_event_written(src)
        assert not [r for r in caplog.records if r.levelno == logging.INFO]
        t[0] += fp.FDPS_WRITE_SUMMARY_S
        fp._note_flight_event_written(None)
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert infos == ["fdps: wrote 4 flight_event(s) in the last 300s (FH=1, TH=2, unknown=1)"]
    assert fp._write_counts == {}
