"""
2026-10-09: transcript leak guard (operator rule: leaks are acknowledged and
provably redacted on the box before anything leaves it). Synthetic secrets only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FAKE = "Zq7FakeSecretValue0123456789"
TOKEN = "ctdc_tester_" + "A1b2C3d4" * 4


def load(monkeypatch, tmp_path):
    env = tmp_path / "secrets.env"
    env.write_text(f"FAKE_PASSWORD={FAKE}\nSHORT=abc\nPUBLIC_HOST=tcps://ems1.swim.faa.gov:55443\n")
    monkeypatch.setenv("LEAK_GUARD_SECRETS_ENV", str(env))
    spec = importlib.util.spec_from_file_location("tls", REPO / "scripts/leak-guard/transcript_leak_scan.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _old(p: Path):
    t = time.time() - 3600
    os.utime(p, (t, t))


def test_scan_finds_values_json_escaped_forms_and_token_shapes_without_printing_them(monkeypatch, tmp_path, capsys):
    m = load(monkeypatch, tmp_path)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.jsonl").write_text(json.dumps({"out": f"pw={FAKE} and Authorization: Bearer {TOKEN}"}) + "\n")
    (root / "clean.jsonl").write_text(json.dumps({"out": "nothing here; short abc; tcps://ems1.swim.faa.gov:55443"}) + "\n")
    _old(root / "a.jsonl")                                              # a closed (not live) transcript
    rc = m.main(["scan", "--root", str(root)])
    out = capsys.readouterr().out
    assert rc == 1 and "FAKE_PASSWORD x1" in out and "api-token" in out
    assert FAKE not in out and TOKEN not in out                       # names and counts only
    assert "clean.jsonl" not in out


def test_redact_is_atomic_keeps_json_valid_and_proves_zero(monkeypatch, tmp_path, capsys):
    m = load(monkeypatch, tmp_path)
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "s.jsonl"
    f.write_text(json.dumps({"out": f"X-Board-Key: {TOKEN}\n{FAKE}"}) + "\n")
    f.chmod(0o600)
    _old(f)
    assert m.main(["redact", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "PROOF: 1 file(s) redacted; rescan hits = 0" in out
    text = f.read_text()
    json.loads(text)                                                   # still valid JSONL
    assert FAKE not in text and TOKEN not in text and "[REDACTED:FAKE_PASSWORD]" in text
    assert oct(f.stat().st_mode & 0o777) == "0o600"
    assert m.main(["scan", "--root", str(root)]) == 0


def test_a_live_session_file_is_left_and_reported(monkeypatch, tmp_path, capsys):
    m = load(monkeypatch, tmp_path)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "live.jsonl").write_text(FAKE)                             # just written: a running session
    m.main(["redact", "--root", str(root)])
    out = capsys.readouterr().out
    assert "SKIPPED" in out and "1 live file(s) left" in out
    assert (root / "live.jsonl").read_text() == FAKE
    m.main(["redact", "--root", str(root), "--include-live"])
    assert (root / "live.jsonl").read_text() == "[REDACTED:FAKE_PASSWORD]"


def test_placeholders_are_not_flagged(monkeypatch, tmp_path):
    m = load(monkeypatch, tmp_path)
    assert m.find(b"ctdc_admin_" + b"0" * 32 + b" ctdc_x_" + b"X" * 32, m.load_secret_values()) == {}


def test_scan_blocks_closed_files_and_only_warns_on_a_live_one(monkeypatch, tmp_path, capsys):
    m = load(monkeypatch, tmp_path)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "live.jsonl").write_text(FAKE)
    assert m.main(["scan", "--root", str(root)]) == 0 and "WARNING 1 LIVE" in capsys.readouterr().out
    old = root / "old.jsonl"
    old.write_text(FAKE)
    _old(old)
    assert m.main(["scan", "--root", str(root)]) == 1
