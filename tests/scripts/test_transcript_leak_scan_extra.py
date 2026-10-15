"""2026-10-15: single-value secret files (the healthchecks.io ping URL) join the scanner's values,
including the bare check id. Synthetic values only."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/leak-guard/transcript_leak_scan.py"


def load(monkeypatch, extra):
    monkeypatch.setenv("LEAK_GUARD_EXTRA_FILES", str(extra))
    spec = importlib.util.spec_from_file_location("tls_extra", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_ping_url_and_bare_id_are_found_and_redacted(tmp_path, monkeypatch):
    extra = tmp_path / "healthchecks_ping.url"
    extra.write_text("https://hc-ping.com/00000000-0000-0000-0000-00000000000a\n")
    m = load(monkeypatch, extra)
    vals = m.load_secret_values(tmp_path / "no-env-file")
    assert list(vals) == ["file:healthchecks_ping.url"] and len(vals["file:healthchecks_ping.url"]) == 2
    blob = b'{"text":"ping https://hc-ping.com/00000000-0000-0000-0000-00000000000a or id 00000000-0000-0000-0000-00000000000a"}'
    assert m.find(blob, vals)
    red = m.redact_blob(blob, vals)
    assert b"00000000-0000-0000-0000-00000000000a" not in red and not m.find(red, vals)


def test_missing_extra_file_is_ignored(tmp_path, monkeypatch):
    m = load(monkeypatch, tmp_path / "absent.url")
    assert m.load_secret_values(tmp_path / "no-env-file") == {}
