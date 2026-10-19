"""2026-10-18 (gate batch): the ACARS watcher's gate forwarder
(src/acars_watcher/acars_gate_forward.py). It keeps only gate/baggage
announcements, sends parsed fields + a small envelope -- never the message text,
a registration or a hex -- and never raises into the watcher loop. The dispatch
POST is a fake session; nothing leaves the box. Synthetic messages only."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def fwd(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "src/acars_watcher"))
    for m in ("acars_gate_forward", "acars_gate_parser"):
        sys.modules.pop(m, None)
    mod = importlib.import_module("acars_gate_forward")
    monkeypatch.setattr(mod, "_ensure_thread", lambda: None)      # no background thread in tests
    while not mod._Q.empty():
        mod._Q.get_nowait()
    yield mod
    for m in ("acars_gate_forward", "acars_gate_parser"):
        sys.modules.pop(m, None)


UA = ("QUNDCULUA~1GATE ASSIGN UA0999/17 KORD KIAD SENT: 14:23:53Z ***ACCOMPLISH TERMINATION CHECKLIST*** "
      "GATE C9 AVAIL 1544 RAMP FREQ 129.075")


def test_announcement_forwarded_without_text_registration_or_hex(fwd):
    fwd.offer({"_source": "AIRFRAMES", "text": UA, "tail": "N000TS", "icao": "000001", "label": "RA",
               "timestamp": "2026-10-17T14:23:55Z", "id": 7})
    (item,) = fwd.drain()
    assert item["hit"]["gate"] == "C9" and item["hit"]["fmt"] == "UA_GATE_ASSIGN"
    assert item["source"] == "AIRFRAMES" and item["msg_id"] == "7"
    blob = repr(item)
    for leaked in ("N000TS", "000001", "ACCOMPLISH", "RAMP FREQ"):
        assert leaked not in blob


def test_skywest_crew_names_never_leave(fwd):
    fwd.offer({"_source": "JUMPSEAT", "text": "ORD ARRIVAL GATE F22 NEXT FLIGHT INFORMATION- CAPT SAMPLE NAME",
               "directionLabel": "Ground to Air", "timestamp": "2026-10-17T14:00:00Z"})
    (item,) = fwd.drain()
    assert "SAMPLE" not in repr(item) and item["hit"]["gate"] == "F22"


def test_requests_acks_noise_and_unknown_sources_are_not_forwarded(fwd):
    for msg in ({"_source": "LOCAL", "text": "/C3 GATE REQ / KORD KIAD 17 ABC123 0999 ----"},
                {"_source": "LOCAL", "text": "/ACKFLT GATE E5"},
                {"_source": "AIRFRAMES", "text": "PLZ HAVE MED PERSONNEL MEET US AT GATE"},
                {"_source": "AIRFRAMES", "text": "POSITION REPORT 3900N"},
                {"_source": "ELSEWHERE", "text": UA}):
        fwd.offer(msg)
    assert fwd.drain() == []


def test_local_dumpvdl2_nested_message(fwd):
    fwd.offer({"_source": "LOCAL", "vdl2": {"t": {"sec": 1792245835}, "avlc": {
        "src": {"type": "Ground station"}, "acars": {"msg_text": UA, "label": "RA", "flight": "UA0999"}}}})
    (item,) = fwd.drain()
    assert item["direction"] == "up" and item["flight"] == "UA0999" and item["timestamp"] == 1792245835


def test_offer_never_raises(fwd, monkeypatch):
    def boom(_t):
        raise RuntimeError("parser exploded")
    monkeypatch.setattr(fwd.agp, "is_candidate", boom)
    fwd.offer({"_source": "AIRFRAMES", "text": UA})      # swallowed
    fwd.offer(None)                                      # not even a dict


def test_post_sends_items_with_the_admin_header(fwd, monkeypatch):
    monkeypatch.setenv("DISPATCH_ADMIN_TOKEN", "dummy-token")
    sent = []

    class S:
        def post(self, url, json=None, headers=None, timeout=None):
            sent.append((url, json, headers))

            class R:
                status_code = 200
            return R()
    fwd._post([{"hit": {"gate": "C9"}}], session=S())
    url, body, headers = sent[0]
    assert url.endswith("/api/v1/gate-observations/acars") and body == {"items": [{"hit": {"gate": "C9"}}]}
    assert headers["Authorization"] == "Bearer dummy-token"


def test_watcher_has_one_marked_call_site():
    src = (REPO / "src/acars_watcher/acars_watcher.py").read_text()
    assert src.count("acars_gate_forward.offer(msg)") == 1
    assert "# >>> gate/baggage hook" in src and "# <<< gate/baggage hook" in src
    cf = (REPO / "src/acars_watcher/Containerfile").read_text()
    assert "acars_gate_parser.py" in cf and "acars_gate_forward.py" in cf
