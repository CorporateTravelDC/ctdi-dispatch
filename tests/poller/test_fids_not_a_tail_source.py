"""Guardrail (2026-10-14, casebook P33): FIDS is gate + baggage enrichment only (operator 2026-09-27, after
FIDS 'Landed' at cruise). It is not an OOOI source (test_fids_never_confirms_oooi.py) and not a tail source:
no FIDS path may reach the tail-change guard or repoint a watch's hex."""
from pathlib import Path

from common import flight_instance as fi

SRC = (Path(__file__).resolve().parents[2] / "src/poller/main.py").read_text()


def test_fids_check_never_touches_identity():
    body = SRC[SRC.index("def _check_flight_fids("):SRC.index("def _check_vessel_aishub(")]
    body = body.split('"""', 2)[2]                                  # past the docstring
    fids = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())  # code, not comments
    for forbidden in ("_guarded_tail_change", "decide_tail_change", "hex_for_registration",
                      "update_watchlist_hex_registration", "update_watchlist_oooi_phase"):
        assert forbidden not in fids, forbidden


def test_no_fids_source_is_authoritative_for_tails():
    assert not any("fids" in s for s in fi.AUTHORITATIVE_TAIL_SOURCES)
    assert fi.AUTHORITATIVE_TAIL_SOURCES <= fi.SWIM_TAIL_SOURCES
