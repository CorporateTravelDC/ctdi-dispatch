"""
Regression test for the 2026-08-25 C-7 fix (Opus blind review):
the old hash_gate() wrote its hash file BEFORE the caller did any of the
guarded work, so a crashed run (e.g. Ollama down) permanently suppressed
retries for identical inputs -- including the hot VIP/TFR path. This locks
in the corrected contract: check_gate() never writes, and only an explicit
commit_gate() call (made by the caller after confirming success) persists
the hash.
"""
import tempfile
from pathlib import Path

import common.sr2_gate as sr2_gate


def test_check_gate_does_not_persist_on_its_own():
    with tempfile.TemporaryDirectory() as tmp:
        sr2_gate.GATE_STATE_DIR = Path(tmp)
        inputs = {"a": 1}

        result, current_hash = sr2_gate.check_gate("skill-x", inputs)
        assert result == "new"

        # Simulating the caller crashing before commit_gate() is ever
        # called -- the gate file must not exist yet.
        assert not (Path(tmp) / "skill-x.hash").exists()

        # A second check with identical inputs, still uncommitted, must
        # still report "new" -- this is the exact bug: the old code would
        # have already written the hash here and returned "skipped" forever.
        result2, _ = sr2_gate.check_gate("skill-x", inputs)
        assert result2 == "new"


def test_commit_gate_then_check_gate_reports_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        sr2_gate.GATE_STATE_DIR = Path(tmp)
        inputs = {"a": 1}

        result, current_hash = sr2_gate.check_gate("skill-y", inputs)
        assert result == "new"
        sr2_gate.commit_gate("skill-y", current_hash)

        result2, _ = sr2_gate.check_gate("skill-y", inputs)
        assert result2 == "skipped"


def test_changed_inputs_report_new_even_after_a_commit():
    with tempfile.TemporaryDirectory() as tmp:
        sr2_gate.GATE_STATE_DIR = Path(tmp)
        result, h1 = sr2_gate.check_gate("skill-z", {"a": 1})
        sr2_gate.commit_gate("skill-z", h1)

        result2, h2 = sr2_gate.check_gate("skill-z", {"a": 2})
        assert result2 == "new"
        assert h2 != h1


def test_force_bypasses_gate_without_reading_or_writing_state():
    with tempfile.TemporaryDirectory() as tmp:
        sr2_gate.GATE_STATE_DIR = Path(tmp)
        result, _ = sr2_gate.check_gate("skill-w", {"a": 1}, force=True)
        assert result == "forced"
        assert not (Path(tmp) / "skill-w.hash").exists()
