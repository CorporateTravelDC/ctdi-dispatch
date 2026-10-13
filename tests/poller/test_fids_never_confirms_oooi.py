"""tests/poller/test_fids_never_confirms_oooi.py -- 2026-10-12: FIDS is gate + baggage enrichment only
(operator rule 2026-09-27). The poller must have no path where FIDS confirms an OOOI phase."""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[2] / "src/poller/main.py").read_text()


def test_no_fids_phase_confirmation_path():
    assert "_fids_confirms_phase(" not in SRC
    assert 'confirm_src = "fids"' not in SRC
    assert '"FIDS"' not in SRC.split("schedule window for IN reached")[1].split("db.update_watchlist_oooi_phase")[0]
