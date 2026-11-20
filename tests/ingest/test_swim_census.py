"""SWIM message-type census (ingest/census.py, 2026-11-19): every type counted, unhandled types warned and
sampled (identifiers redacted), one batched flush, hourly summary, drop alert, FDPS AH/HU written."""
from __future__ import annotations

import logging
import os

import pytest

from ingest import census


HOUR = 1_795_053_600.0   # 2026-11-19 02:00Z


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    census._reset_for_tests()
    monkeypatch.setattr(census, "SAMPLE_DIR", str(tmp_path / "samples"))
    yield
    census._reset_for_tests()


class _FakeDB:
    def __init__(self):
        self.rows = []

    def add_swim_message_census(self, rows):
        self.rows.extend(rows)


def test_counts_are_batched_into_one_flush():
    for _ in range(3):
        census.record("fdps", "FH", True, now=HOUR + 10)
    census.record("fdps", "NP", False, now=HOUR + 11)
    db = _FakeDB()
    assert census.flush(now=HOUR + 20, db=db) == 2
    assert sorted(db.rows) == [(int(HOUR), "fdps", "FH", 1, 3), (int(HOUR), "fdps", "NP", 0, 1)]
    assert census.flush(now=HOUR + 30, db=db) == 0          # nothing pending, nothing written


def test_unhandled_type_warns_once_per_hour(caplog):
    caplog.set_level(logging.WARNING, logger="ingest.census")
    for i in range(5):
        census.record("fdps", "DH", False, now=HOUR + i)
    assert sum("unhandled message type 'DH'" in r.getMessage() for r in caplog.records) == 1
    census.flush(now=HOUR + 3700, db=_FakeDB())              # hour rolls; warning may fire again
    census.record("fdps", "DH", False, now=HOUR + 3700)
    assert sum("unhandled message type 'DH'" in r.getMessage() for r in caplog.records) == 2


def test_samples_are_capped_redacted_and_private():
    xml = ('<flight source="NP" centre="ZNY"><aircraftIdentification>QQQ123</aircraftIdentification>'
           '<aircraft registration="QTEST1" aircraftAddress="000001"/><x>keep</x></flight>')
    for i in range(8):
        census.record("fdps", "NP", False, sample=xml, now=HOUR + i)
    d = os.path.join(census.SAMPLE_DIR, "fdps", "2026-11-19")
    files = sorted(os.listdir(d))
    assert len(files) == census.SAMPLES_PER_TYPE_PER_DAY
    body = open(os.path.join(d, files[0])).read()
    assert "QQQ123" not in body and "QTEST1" not in body and "000001" not in body
    assert "<x>keep</x>" in body and 'centre="ZNY"' in body
    assert oct(os.stat(os.path.join(d, files[0])).st_mode & 0o777) == "0o600"


def test_handled_types_are_not_sampled_by_record():
    census.record("fdps", "FH", True, sample="<flight/>", now=HOUR)
    assert not os.path.exists(os.path.join(census.SAMPLE_DIR, "fdps"))


def test_old_samples_age_out():
    old = os.path.join(census.SAMPLE_DIR, "fdps", "2026-10-01")
    os.makedirs(old)
    open(os.path.join(old, "NP-1.xml"), "w").close()
    census.flush(now=HOUR, db=_FakeDB())
    census.flush(now=HOUR + 3601, db=_FakeDB())              # hour roll runs the age-out
    assert not os.path.exists(old)


def test_dropped_type_over_threshold_alerts_once_per_day(monkeypatch):
    sent = []
    monkeypatch.setattr(census, "_alert", lambda feed, mt, n: sent.append((feed, mt, n)))
    monkeypatch.setattr(census, "DROP_ALERT_PER_HOUR", 3)
    for i in range(4):
        census.record("tfms", "fltd/flightPlanInformation", False, now=HOUR + i)
    census.record("fdps", "TH", True, now=HOUR)              # handled: never alerts
    census.flush(now=HOUR + 10, db=_FakeDB())
    census.flush(now=HOUR + 3601, db=_FakeDB())              # summary of the finished hour
    assert sent == [("tfms", "fltd/flightPlanInformation", 4)]
    for i in range(4):
        census.record("tfms", "fltd/flightPlanInformation", False, now=HOUR + 3700 + i)
    census.flush(now=HOUR + 7300, db=_FakeDB())
    assert len(sent) == 1                                    # same UTC day: no second alert


def test_flush_failure_keeps_counts():
    class Broken:
        def add_swim_message_census(self, rows):
            raise RuntimeError("db down")
    census.record("itws", "Microburst Product", True, now=HOUR)
    census.flush(now=HOUR + 1, db=Broken())
    db = _FakeDB()
    census.flush(now=HOUR + 2, db=db)
    assert db.rows == [(int(HOUR), "itws", "Microburst Product", 1, 1)]


def test_doc_type_reads_root_and_msgtype():
    assert census.doc_type(b'<?xml version="1.0"?><ns5:MessageCollection xmlns:ns5="x">') == "MessageCollection"
    assert census.doc_type(b'<tfmDataService><fltdOutput><fltdMessage msgType="trackInformation">') \
        == "tfmDataService/trackInformation"


def test_census_db_roundtrip():
    from common import db
    db.add_swim_message_census([(int(HOUR), "fdps", "NP", 0, 2)])
    db.add_swim_message_census([(int(HOUR), "fdps", "NP", 0, 3)])
    rows = db.get_swim_message_census(HOUR)
    assert [(r["feed"], r["msg_type"], r["handled"], r["n"]) for r in rows] == [("fdps", "NP", 0, 5)]


def test_fdps_handler_writes_ah_and_hu(monkeypatch):
    from ingest import swim_client
    import ingest.parsers.fdps_parser as fp
    written = []
    monkeypatch.setattr(fp, "parse_fdps_messages",
                        lambda payload: [{"source": s} for s in ("FH", "AH", "HU", "HZ", "RH")])
    monkeypatch.setattr(fp, "write_flight_event", lambda parsed: written.append(parsed["source"]) or True)
    monkeypatch.setattr(fp, "check_marine_one", lambda parsed: None)
    monkeypatch.setattr(fp, "check_fdps_watchlist", lambda parsed: None)
    assert swim_client._handle_fdps_message(b"<x/>") is True
    assert written == ["FH", "AH", "HU"]
    db = _FakeDB()
    census.flush(db=db)
    got = {(r[2], r[3]) for r in db.rows}
    assert ("HZ", 0) in got and ("AH", 1) in got and ("FH", 1) in got


def test_unknown_fdps_source_is_counted_not_silent():
    import xml.etree.ElementTree as ET
    import ingest.parsers.fdps_parser as fp
    assert fp._parse_fixm30_flight_element(ET.fromstring('<flight source="NP" centre="ZNY"/>'), "") is None
    db = _FakeDB()
    census.flush(db=db)
    assert any(r[1] == "fdps" and r[2] == "NP" and r[3] == 0 for r in db.rows)


def test_bytearray_payload_is_stored_as_xml_not_its_repr():
    # 2026-11-20: the TFDM consumer passes a bytearray; samples were written as "bytearray(b'...')".
    xml = b'<?xml version="1.0"?><f><flightIdentification aircraftIdentification="QQQ123"/><x>keep</x></f>'
    census.sample("tfdm", "ttp:FlightData/FlightAdd", bytearray(xml), now=HOUR)
    d = os.path.join(census.SAMPLE_DIR, "tfdm", "2026-11-19")
    body = open(os.path.join(d, os.listdir(d)[0])).read()
    assert body.startswith("<?xml") and "<x>keep</x>" in body and "QQQ123" not in body
