"""2026-10-18: ACARS message-type flags (operator: "build in filtering flags for specific
types and tie those to OOOIs as well as TFRs") and the acars-watcher's nested dumpvdl2
extraction fix (uplink diagnosis: every local VDL2 datagram was dropped because the
registration sits at vdl2.avlc.acars.reg). Synthetic fixtures only (N0... registrations
cannot be real); assertions are on flags, fields and counts -- nothing is printed."""
from __future__ import annotations

import asyncio
import collections
import importlib
import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from common import acars_flags

REPO = Path(__file__).resolve().parents[2]
FIX = json.loads((REPO / "tests/fixtures/acars/messages_synthetic.json").read_text())


def test_the_two_copies_are_byte_identical():
    a = (REPO / "src/common/acars_flags.py").read_bytes()
    b = (REPO / "src/acars_watcher/acars_flags.py").read_bytes()
    assert a == b, "src/acars_watcher/acars_flags.py must be a byte copy of src/common/acars_flags.py"


def test_classifier_cannot_write_anything():
    """A flag is a label: the module imports nothing from the platform and names no
    phase writer, so classification cannot assert an OOOI phase by construction."""
    src = (REPO / "src/common/acars_flags.py").read_text()
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert imports == ["from __future__ import annotations", "import re"]
    for banned in ("update_watchlist_oooi_phase", "conn(", "requests", "watchlist_event_hit"):
        assert banned not in src.split('"""', 2)[2]          # outside the module docstring


@pytest.mark.parametrize("shape", sorted(FIX["shapes"]))
def test_extract_fields_reads_every_shape(shape):
    f = acars_flags.extract_fields(FIX["shapes"][shape])
    for k, v in FIX["expected_fields"][shape].items():
        assert f[k] == v, (shape, k)
    assert f["text"]


def test_classification_table_and_counts():
    counts = collections.Counter()
    for msg, want in FIX["cases"]:
        got = acars_flags.classify(msg)
        assert got == want, (msg["label"], got, want)
        counts.update(got)
    assert sum(counts.values()) == 15 and counts["oooi"] == 3
    assert all(counts[f] == 1 for f in acars_flags.FLAGS if f not in ("oooi", "tfr_area"))
    assert counts["tfr_area"] == 0                        # needs TFR context (below)


def test_shapes_classify_from_nested_fields():
    s = FIX["shapes"]
    assert acars_flags.classify(s["dumpvdl2"]) == ["oooi"]
    assert acars_flags.classify(s["acarsdec"]) == ["position_report"]
    assert acars_flags.classify(s["airframes"]) == ["baggage", "gate_assign"]
    assert acars_flags.classify(s["jumpseat"]) == ["eta", "gate_request"]


def test_position_parse_and_tfr_ties():
    assert acars_flags.parse_position("#M1BPOSN38512W077123,FIX01") == (38.8533, -77.205)
    assert acars_flags.parse_position("POS N 38.851, W 77.205") == (38.851, -77.205)
    assert acars_flags.parse_position("N38992W077123") == (None, None)     # 99 minutes: rejected
    pos = {"label": "H1", "text": "#M1BPOSN38512W077123,FIX01", "block_id": "7"}
    assert "tfr_area" in acars_flags.classify(pos, in_tfr=lambda la, lo: True)
    assert "tfr_area" not in acars_flags.classify(pos, in_tfr=lambda la, lo: False)
    hold = {"label": "5Z", "text": "HOLDING AT FIX01 POS N38512W077123", "block_id": "1"}
    assert "tfr_notam_mention" in acars_flags.classify(hold, near_tfr=lambda la, lo: True)
    assert "tfr_notam_mention" not in acars_flags.classify(hold, near_tfr=lambda la, lo: False)
    assert "tfr_notam_mention" not in acars_flags.classify(hold)            # no context: text rule only
    boom = lambda la, lo: 1 / 0                                             # noqa: E731
    assert "tfr_area" not in acars_flags.classify(pos, in_tfr=boom)         # context failure: no flag


def test_filter_parse_and_column():
    assert acars_flags.parse_filter(" oooi, ETA ,") == ["eta", "oooi"]
    assert acars_flags.parse_filter("") == []
    with pytest.raises(ValueError):
        acars_flags.parse_filter("oooi,bogus")
    assert acars_flags.to_column(["oooi", "eta", "oooi"]) == "eta,oooi"
    assert acars_flags.to_column([]) is None


# ── acars-watcher: nested dumpvdl2 reaches the watchlist match ─────────────────

@pytest.fixture
def aw(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DISPATCH_ADMIN_TOKEN", "dummy-test-token")
    monkeypatch.setenv("DISPATCH_BASE_URL", "http://127.0.0.1:9")
    sys.modules.pop("acars_watcher.acars_watcher", None)
    mod = importlib.import_module("acars_watcher.acars_watcher")
    yield mod
    sys.modules.pop("acars_watcher.acars_watcher", None)


@pytest.mark.parametrize("shape", sorted(FIX["shapes"]))
def test_watcher_extracts_registration_from_every_shape(aw, shape):
    want = FIX["expected_fields"][shape]["tail"]
    assert aw.normalize_reg(aw.extract_reg_from_msg(FIX["shapes"][shape])) == want


def test_nested_vdl2_message_for_watched_reg_reaches_the_hit(aw, monkeypatch, caplog):
    sent = []
    monkeypatch.setattr(aw, "fire_ntfy", lambda payload: sent.append(payload))
    msg = dict(FIX["shapes"]["dumpvdl2"], _source="LOCAL")
    watched = {"N0TEST1"}
    reg = aw.extract_reg_from_msg(msg)
    assert reg and aw.normalize_reg(reg) in watched          # was "" before the fix: dropped
    with caplog.at_level(logging.INFO, logger=aw.log.name):
        payload = aw.handle_watched_match(reg, msg)
    assert len(sent) == 1 and "acars_flags" not in sent[0]
    assert "(TST101)" in sent[0]["title"] and "{oooi}" in sent[0]["title"]
    assert sent[0]["message"].startswith("OFF 1912") and "Freq: 136.975" in sent[0]["message"]
    assert payload["acars_flags"] == ["oooi"]
    assert all("N0TEST1" not in r.getMessage() for r in caplog.records)   # counts, never the reg


# ── runner API filter ───────────────────────────────────────────────────────

def _request():
    from starlette.requests import Request
    return Request({"type": "http", "headers": [], "method": "GET", "path": "/api/acars/messages",
                    "client": ("127.0.0.1", 1)})


def test_runner_routes_annotate_and_filter(monkeypatch):
    from unittest.mock import AsyncMock, patch
    from fastapi import HTTPException
    from runner import main as rm
    msgs = [{"text": "OUT 1850", "label": "QP"}, {"text": "BAGS ON CAROUSEL 3", "direction": "uplink"},
            {"text": "THANKS"}]
    with patch.object(rm, "DEMO_MODE", False), \
            patch.object(rm, "_acarshub_messages", new=AsyncMock(return_value=msgs)):
        all_ = asyncio.run(rm.acars_messages(_request(), since=0, lat=0, lon=0, dist=1, flags=""))
        assert [m["flags"] for m in all_["messages"]] == [["oooi"], ["baggage"], ["free_text"]]
        only = asyncio.run(rm.acars_messages(_request(), since=0, lat=0, lon=0, dist=1, flags="oooi,baggage"))
        assert only["count"] == 2 and only["flags_filter"] == ["baggage", "oooi"]
        with pytest.raises(HTTPException) as e:
            asyncio.run(rm.vdl2_messages(_request(), since=0, lat=0, lon=0, dist=1, flags="nope"))
        assert e.value.status_code == 400
    with patch.object(rm, "DEMO_MODE", True):              # the demo stays synthetic, still annotated
        demo = asyncio.run(rm.hfdl_messages(_request()))
        assert demo["source"] == "synthetic" and all("flags" in m for m in demo["messages"])
