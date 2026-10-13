"""2026-10-11: backup freshness rules."""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load(monkeypatch, target=""):
    spec = importlib.util.spec_from_file_location("bf", REPO / "scripts/backup/backup_freshness.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    monkeypatch.setenv("BACKUP_TARGET", target)
    m.ENV = Path("/nonexistent/dispatch.env")   # 2026-10-12: never read the live settings file in tests
    return m


def e(kind, hours_ago, **data):
    return {"kind": kind, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours_ago * 3600)), "data": data}


def test_fresh_backup_and_restore_test_pass(monkeypatch):
    m = load(monkeypatch)
    monkeypatch.setenv("BACKUP_REQUIRE_OFFBOX", "0")
    assert m.check([e("backup", 2), e("restore-test", 30, ok=True)]) == []


def test_stale_backup_failed_restore_and_missing_sync_are_reported(monkeypatch):
    m = load(monkeypatch, target="proton:ctdi-backup")
    p = m.check([e("backup", 50), e("sync", 49, ok=False), e("restore-test", 10, ok=False)])
    assert any("no backup" in x for x in p) and any("off-box sync" in x for x in p) and any("restore test" in x for x in p)


def test_empty_ledger_is_stale(monkeypatch):
    m = load(monkeypatch)
    assert m.check([]) and "no backup" in m.check([])[0]


# -- 2026-10-12 (docs-parity audit): on-box-only is a standing failure; every agent is checked --

def test_no_offbox_target_is_reported_unless_deliberately_accepted(monkeypatch):
    m = load(monkeypatch)
    p = m.check([e("backup", 2), e("restore-test", 30, ok=True)])
    assert any("no off-box target configured" in x for x in p)
    monkeypatch.setenv("BACKUP_REQUIRE_OFFBOX", "0")
    assert m.check([e("backup", 2), e("restore-test", 30, ok=True)]) == []


def test_offbox_target_with_fresh_sync_passes(monkeypatch):
    m = load(monkeypatch, target="proton:ctdi-backup")
    assert m.check([e("backup", 2), e("sync", 2, ok=True), e("restore-test", 30, ok=True)]) == []


def test_snapshot_agents_default_to_every_agent_account(monkeypatch):
    import pwd
    from types import SimpleNamespace as NS
    m = load(monkeypatch)
    monkeypatch.delenv("CTDC_SNAPSHOT_AGENTS", raising=False)
    monkeypatch.setenv("CTDC_SNAPSHOT_EXCLUDE", "ctdc-agent-llama")
    fake = [NS(pw_name=n) for n in ("root", "ctdc-agent-openai-codex", "ctdc-agent-anthropic-claude", "ctdc-agent-llama", "corporatetraveldc")]
    monkeypatch.setattr(pwd, "getpwall", lambda: fake)
    assert m._snapshot_agents() == ["ctdc-agent-anthropic-claude", "ctdc-agent-openai-codex"]
    monkeypatch.setenv("CTDC_SNAPSHOT_AGENTS", "ctdc-agent-x")
    assert m._snapshot_agents() == ["ctdc-agent-x"]
