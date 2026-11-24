"""2026-11-24: the daily semantic compile's INFO phase lines reach the journal.

Nothing configured logging in semantic_compile_daily.main(), so the root logger
stayed at WARNING and compile_layer()'s "semantic compile: phase=..." lines
were dropped; a 30-minute timeout could not be attributed to a phase.
"""
import logging

from poller.skills import semantic_compile_daily as scd


def test_main_configures_info_logging_when_unconfigured(monkeypatch):
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers = []
    seen = {}

    def fake_run():
        seen["level"] = logging.getLogger().getEffectiveLevel()
        logging.getLogger("second_brain.semantic.compile").info("semantic compile: phase=geometry started")
        return {"status": "ok"}

    monkeypatch.setattr(scd, "run", fake_run)
    try:
        assert scd.main() == 0
        assert seen["level"] == logging.INFO
        assert root.handlers, "main() must install a handler when none exists"
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


def test_main_leaves_existing_logging_alone(monkeypatch):
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    marker = logging.NullHandler()
    root.handlers = [marker]
    root.setLevel(logging.WARNING)
    monkeypatch.setattr(scd, "run", lambda: {"status": "error", "reason": "x"})
    try:
        assert scd.main() == 1
        assert root.handlers == [marker]
        assert root.level == logging.WARNING
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
