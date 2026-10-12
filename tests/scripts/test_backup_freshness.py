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
    return m


def e(kind, hours_ago, **data):
    return {"kind": kind, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours_ago * 3600)), "data": data}


def test_fresh_backup_and_restore_test_pass(monkeypatch):
    m = load(monkeypatch)
    assert m.check([e("backup", 2), e("restore-test", 30, ok=True)]) == []


def test_stale_backup_failed_restore_and_missing_sync_are_reported(monkeypatch):
    m = load(monkeypatch, target="proton:ctdi-backup")
    p = m.check([e("backup", 50), e("sync", 49, ok=False), e("restore-test", 10, ok=False)])
    assert any("no backup" in x for x in p) and any("off-box sync" in x for x in p) and any("restore test" in x for x in p)


def test_empty_ledger_is_stale(monkeypatch):
    m = load(monkeypatch)
    assert m.check([]) and "no backup" in m.check([])[0]
