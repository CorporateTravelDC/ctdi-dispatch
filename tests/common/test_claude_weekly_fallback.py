"""
Tests for common.claude_weekly_fallback's marker bookkeeping (2026-09-17):
the overnight Llama backstop for docs-drift-weekly / second-brain-weekly-dump
must only ever see a job as "owed" between a recorded failure and either a
confirmed clear (docs-drift path) or an explicit downgrade to
llama-fallback-partial (second-brain-dump path, whose destructive
CLAUDE.md/memory reset must never fire off an unconfirmed Llama draft).
"""
import tempfile
from pathlib import Path

import common.claude_weekly_fallback as cwf


def _with_tmp_state_dir(fn):
    tmp = tempfile.TemporaryDirectory()
    orig = cwf.STATE_DIR
    try:
        cwf.STATE_DIR = Path(tmp.name)
        fn()
    finally:
        cwf.STATE_DIR = orig
        tmp.cleanup()


def test_no_marker_by_default():
    _with_tmp_state_dir(lambda: (
        None if cwf.get_marker("docs-drift-weekly") is None
        else (_ for _ in ()).throw(AssertionError("expected no marker"))
    ))


def test_mark_failure_then_clear():
    def body():
        cwf.mark_failure("docs-drift-weekly", "claude exited 1")
        marker = cwf.get_marker("docs-drift-weekly")
        assert marker is not None
        assert marker["state"] == cwf.STATE_PENDING
        assert "claude exited 1" in marker["reason"]

        cwf.clear_marker("docs-drift-weekly")
        assert cwf.get_marker("docs-drift-weekly") is None
    _with_tmp_state_dir(body)


def test_second_brain_dump_never_auto_clears_to_none_on_llama_partial():
    def body():
        cwf.mark_failure("second-brain-weekly-dump", "opus rc=1")
        cwf.set_llama_partial("second-brain-weekly-dump", "llama wrote a draft note")
        marker = cwf.get_marker("second-brain-weekly-dump")
        # Must NOT be None/cleared -- a real Opus-confirmed run is still owed.
        assert marker is not None
        assert marker["state"] == cwf.STATE_LLAMA_PARTIAL
        # And the fallback runner's own re-entry guard (checked in
        # claude_weekly_llama_fallback.py) only fires on STATE_PENDING, so
        # this state must be distinguishable from it.
        assert marker["state"] != cwf.STATE_PENDING
    _with_tmp_state_dir(body)


def test_jobs_are_independent():
    def body():
        cwf.mark_failure("docs-drift-weekly", "x")
        assert cwf.get_marker("second-brain-weekly-dump") is None
        cwf.clear_marker("docs-drift-weekly")
    _with_tmp_state_dir(body)
