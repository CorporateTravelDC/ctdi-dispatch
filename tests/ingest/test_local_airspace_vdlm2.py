"""
tests/ingest/test_local_airspace_vdlm2.py

2026-10-03: the local_airspace ACARS reader only ever consumed acars_router's
plain-ACARS TCP stream (:9080), while every real message on this station is
VDL2 from dumpvdl2 on a separate port. acars_messages held zero real rows
since the feature shipped. These tests pin _normalize_acars to the dumpvdl2
frame shape captured live from acars_router :15555 that night, so a future
format change fails loudly here instead of silently in the idle warning.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("DISPATCH_DB_BACKEND", "sqlite")

from ingest.local_airspace import _normalize_acars  # noqa: E402

# Captured 2026-10-03T01:14Z from acars_router AR_SERVE_TCP_VDLM2 (real
# traffic; ACARS is unencrypted public radio). Trimmed msg_text.
LIVE_VDL2_FRAME = {
    "vdl2": {
        "app": {"name": "dumpvdl2", "ver": "2.7.0"},
        "avlc": {
            "cr": "Command", "dst": {"addr": "10E18F", "type": "Ground station"},
            "frame_type": "I", "poll": False, "rseq": 1, "sseq": 4,
            "src": {"addr": "DC5068", "status": "Airborne", "type": "Aircraft"},
            "acars": {
                "err": False, "crc_ok": True, "more": False, "reg": ".Q0LYY",
                "mode": "2", "label": "H1", "blk_id": "2", "ack": "!",
                "flight": "WN4060", "msg_num": "F94", "msg_num_seq": "A",
                "sublabel": "M1",
                "msg_text": "PRG/LR,011441,SWA4060,KSTL,KDCA,01O,25,1083",
            },
        },
        "burst_len_octets": 136, "freq": 136975000, "idx": 0,
        "freq_skew": 0.1, "hdr_bits_fixed": 0, "noise_level": -40.1,
        "octets_corrected_by_fec": 0, "sig_level": -20.3,
        "station": "CS-KDCA-VDL", "t": {"sec": 1791076482, "usec": 507244},
    }
}

AVLC_ONLY_FRAME = {
    "vdl2": {
        "avlc": {"cr": "Response", "frame_type": "S",
                 "src": {"addr": "DC5068", "type": "Aircraft"},
                 "dst": {"addr": "10E18F", "type": "Ground station"}},
        "freq": 136975000, "station": "CS-KDCA-VDL",
    }
}


class NormalizeVdlm2(unittest.TestCase):
    def test_live_frame_flattens_to_acarsdec_shape(self):
        out = _normalize_acars(LIVE_VDL2_FRAME)
        self.assertIsNotNone(out)
        self.assertEqual(out["tail"], "Q0LYY")      # leading "." pad stripped
        self.assertEqual(out["flight"], "WN4060")
        self.assertEqual(out["label"], "H1")
        self.assertEqual(out["icao"], "DC5068")
        self.assertAlmostEqual(out["freq"], 136.975)   # Hz -> MHz
        self.assertEqual(out["block_id"], "2")
        self.assertEqual(out["ack"], "!")
        self.assertEqual(out["mode"], "2")
        self.assertTrue(out["text"].startswith("PRG/LR"))
        self.assertEqual(out["source"], "vdlm2")
        self.assertIs(out["_raw"], LIVE_VDL2_FRAME)

    def test_avlc_only_frame_is_skipped(self):
        self.assertIsNone(_normalize_acars(AVLC_ONLY_FRAME))

    def test_plain_acars_passes_through_unchanged(self):
        flat = {"tail": "Q1SMY", "flight": "UA777", "label": "H1", "text": "x",
                "freq": 131.550}
        self.assertIs(_normalize_acars(flat), flat)

    def test_garbage_is_none(self):
        self.assertIsNone(_normalize_acars("not a dict"))
        self.assertIsNone(_normalize_acars({"vdl2": "oops"}))


if __name__ == "__main__":
    unittest.main()


# ── Watchlist matching through _process_acars_message ───────────────────────
# ACARS/VDL2 flight ids are IATA (WN4060); watchlist identifiers are ICAO
# (SWA4060). Patch the DB-facing helpers so no database is touched.

from unittest.mock import patch  # noqa: E402

import ingest.local_airspace as la  # noqa: E402


def _run_with_entries(msg: dict, entries: list[dict]) -> dict:
    """Run _process_acars_message against fake watchlist entries; return the
    kwargs passed to db.insert_acars_message (watchlist_hit / entry id)."""
    captured: dict = {}

    def fake_insert(**kw):
        captured.update(kw)

    with patch.object(la, "get_active_entries", return_value=entries), \
         patch.object(la, "watchlist_event_hit") as hit, \
         patch.object(la.db, "insert_acars_message", side_effect=fake_insert):
        la._process_acars_message(msg)
        captured["_event_hit_called"] = hit.called
    return captured


def _vdl2(flight: str, reg: str, label: str = "H1") -> dict:
    return {"vdl2": {"freq": 136975000, "station": "CS-KDCA-VDL",
                     "avlc": {"src": {"addr": "DC5068", "type": "Aircraft"},
                              "acars": {"reg": f".{reg}", "flight": flight,
                                        "label": label, "msg_text": "x"}}}}


class WatchlistMatch(unittest.TestCase):
    def test_iata_flight_matches_icao_watchlist_identifier(self):
        out = _run_with_entries(_vdl2("WN4060", "Q0LYY"),
                                [{"id": "wl-1", "identifier": "SWA4060", "registration": None}])
        self.assertEqual(out["watchlist_hit"], 1)
        self.assertEqual(out["watchlist_entry_id"], "wl-1")

    def test_icao_flight_still_matches(self):
        out = _run_with_entries({"flight": "SWA4060", "tail": "Q0LYY", "label": "H1"},
                                [{"id": "wl-1", "identifier": "SWA4060"}])
        self.assertEqual(out["watchlist_hit"], 1)

    def test_unknown_carrier_never_false_matches(self):
        # "ZZ4060" is not in the table -> normalises to None -> no match,
        # even though the numeric part coincides.
        out = _run_with_entries(_vdl2("ZZ4060", "N00000"),
                                [{"id": "wl-1", "identifier": "SWA4060", "registration": None}])
        self.assertEqual(out["watchlist_hit"], 0)
        self.assertIsNone(out["watchlist_entry_id"])

    def test_different_flight_same_carrier_no_match(self):
        out = _run_with_entries(_vdl2("WN4061", "Q0LYY"),
                                [{"id": "wl-1", "identifier": "SWA4060", "registration": None}])
        self.assertEqual(out["watchlist_hit"], 0)

    def test_zero_padded_flight_matches_unpadded_watchlist(self):
        # 2026-10-04 duel M11: ACARS pads (AA0123 / UA0007), watchlists do not.
        out = _run_with_entries(_vdl2("AA0123", "N00000"),
                                [{"id": "wl-1", "identifier": "AAL123", "registration": None}])
        self.assertEqual(out["watchlist_hit"], 1)
        out = _run_with_entries(_vdl2("UA0007", "N00000"),
                                [{"id": "wl-2", "identifier": "UAL7", "registration": None}])
        self.assertEqual(out["watchlist_hit"], 1)

    def test_zero_strip_does_not_merge_different_numbers(self):
        from common.airline_codes import same_flight
        self.assertFalse(same_flight("AA0123", "AAL1230"))
        self.assertFalse(same_flight("UA0007", "UAL70"))
        self.assertTrue(same_flight("DL0100A", "DAL100A"))
        self.assertTrue(same_flight("AA0000", "AAL0"))

    def test_tail_matches_registration_column(self):
        out = _run_with_entries(_vdl2("XX9999", "Q0LYY"),
                                [{"id": "wl-reg", "identifier": "SWA1", "registration": "Q0LYY"}])
        self.assertEqual(out["watchlist_hit"], 1)
        self.assertEqual(out["watchlist_entry_id"], "wl-reg")

    def test_tail_matches_identifier_as_before(self):
        out = _run_with_entries(_vdl2("XX9999", "Q66L7"),
                                [{"id": "wl-tail", "identifier": "Q66L7"}])
        self.assertEqual(out["watchlist_hit"], 1)
        self.assertEqual(out["watchlist_entry_id"], "wl-tail")

    def test_oooi_label_fires_event_hit(self):
        # label 37 is an OOOI label in _ACARS_LABEL_OOOI only if mapped; use
        # whatever the module maps so the test tracks the table, not a guess.
        label = next(iter(la._ACARS_LABEL_OOOI)) if la._ACARS_LABEL_OOOI else None
        if not label:
            self.skipTest("no OOOI labels mapped")
        out = _run_with_entries(_vdl2("UA1988", "Q24QO", label=label),
                                [{"id": "wl-ua", "identifier": "UAL1988"}])
        self.assertEqual(out["watchlist_hit"], 1)
        self.assertTrue(out["_event_hit_called"])
