"""2026-10-11: relay nudge -- token only in the header, never fails the caller."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Synthetic token assembled at runtime so no literal "Bearer <token>" or long env value
# sits in the source (the pre-commit credential scan rightly rejects those).
TOK = "tk_" + "Synth" + "Nudge" + "0" * 4


def load():
    spec = importlib.util.spec_from_file_location("rn", REPO / "scripts/relay-nudge.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_posts_with_bearer_header_and_never_prints_token(tmp_path, monkeypatch, capsys):
    m = load()
    (tmp_path / "e").write_text("NTFY_URL=http://host.containers.internal:2586\n")
    (tmp_path / "s").write_text("NTFY_TOKEN=" + TOK + ":x\n")
    monkeypatch.setattr(m, "ENV", tmp_path / "e"); monkeypatch.setattr(m, "SECRETS", tmp_path / "s")
    seen = {}
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda req, timeout: seen.update(url=req.full_url, auth=req.get_header("Authorization"), data=req.data))
    assert m.main(["Relay 7: GPG passphrase needed", "waiting in Remote-Admin", "--priority", "5"]) == 0
    assert seen["url"] == "http://127.0.0.1:2586/ops-health" and seen["auth"] == "Bearer " + TOK
    assert TOK not in capsys.readouterr().out


def test_network_failure_never_fails_the_relay(tmp_path, monkeypatch, capsys):
    m = load()
    monkeypatch.setattr(m, "ENV", tmp_path / "missing"); monkeypatch.setattr(m, "SECRETS", tmp_path / "missing")
    def boom(*a, **k): raise OSError("down")
    monkeypatch.setattr(m.urllib.request, "urlopen", boom)
    assert m.main(["t", "m"]) == 0 and "not sent" in capsys.readouterr().out
