"""tests/scripts/test_agent_home_snapshot.py -- 2026-10-12: agent home snapshots
must never carry credentials. The 2026-10-11/12 snapshots captured a Claude Code
session key and a browser profile; the excludes now cover them, and a tripwire
deletes any archive that still holds a credential-shaped name."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/backup/agent-home-snapshot.sh"
pytestmark = pytest.mark.skipif(not (shutil.which("zstd") and shutil.which("tar")), reason="needs tar + zstd")


def _home(root: Path) -> Path:
    h = root / "home"
    for rel, body in {
        "work/notes.md": "keep me",
        ".claude/settings.json": "{}",
        ".claude/sessions/2122.abc.key": "SESSION-KEY",
        ".claude/sessions/2122.json": "{}",
        ".config/BraveSoftware/Brave-Browser/Local State": "{}",
        ".config/BraveSoftware/Brave-Browser/Default/Cookies": "x",
        ".ssh/id_ed25519": "PRIVATE",
        ".ssh/id_ed25519.pub": "ssh-ed25519 AAAA",
        ".codex/auth.json": "{}",
        "certs/client.pem": "PEM",
        ".mozilla/firefox/profiles.ini": "x",
    }.items():
        p = h / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return h


def _run(tmp_path, home, extra_env=None):
    env = dict(os.environ, CTDC_SNAPSHOT_HOMES=f"ctdc-agent-test:{home}", CTDC_SNAPSHOT_OUT=str(tmp_path / "out"),
               CTDC_SNAPSHOT_OWNER=os.environ.get("USER") or subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip())
    env.update(extra_env or {})
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def _names(tmp_path):
    arcs = sorted((tmp_path / "out" / "ctdc-agent-test").glob("*.tar.zst"))
    assert len(arcs) == 1
    out = subprocess.run(f"zstd -dc '{arcs[0]}' | tar -tf -", shell=True, capture_output=True, text=True).stdout
    return arcs[0], out.splitlines()


def test_credentials_and_browser_profiles_are_excluded(tmp_path):
    r = _run(tmp_path, _home(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "tripwire clean" in r.stdout
    arc, names = _names(tmp_path)
    assert "./work/notes.md" in names and "./.claude/settings.json" in names
    for bad in (".claude/sessions/2122.abc.key", "Local State", "Cookies", ".ssh/id_ed25519", ".codex/auth.json",
                "client.pem", ".mozilla/firefox/profiles.ini", ".claude/sessions/2122.json"):
        assert not any(n.endswith(bad) or ("/" + bad) in n for n in names if not n.endswith(".pub")), bad
    assert (arc.stat().st_mode & 0o777) == 0o600 and Path(str(arc) + ".sha256").exists()


def test_tripwire_deletes_an_archive_that_still_holds_a_credential(tmp_path):
    home = _home(tmp_path)
    (home / "vault").mkdir()
    (home / "vault" / "deploy_rsa").write_text("x")            # not excluded by name ...
    (home / ".ssh" / "work_rsa").write_text("PRIVATE")          # ... excluded (.ssh/*_rsa)
    (home / "keys").mkdir()
    (home / "keys" / "Login Data").write_text("x")              # excluded by name
    # simulate a missed exclude: a credential-shaped name the excludes do not cover
    odd = home / "x" / ".ssh"
    odd.mkdir(parents=True)
    (odd / "id_ecdsa").write_text("PRIVATE")                    # nested .ssh: excluded? tripwire must catch it if not
    r = _run(tmp_path, home)
    arcs = list((tmp_path / "out" / "ctdc-agent-test").glob("*.tar.zst"))
    if r.returncode == 0:   # excludes caught everything: archive kept, and it is clean
        assert len(arcs) == 1
        _, names = _names(tmp_path)
        assert not any(n.endswith("/id_ecdsa") for n in names)
    else:                   # tripwire fired: nothing kept, loud failure
        assert "REFUSED ctdc-agent-test" in r.stderr and arcs == []


def test_tripwire_fires_when_an_exclude_is_missing(tmp_path, monkeypatch):
    """Strip one exclude from a copy of the script: the tripwire, not the excludes, must stop it."""
    s = SCRIPT.read_text().replace("--exclude='.claude/sessions' ", "").replace("--exclude='*.key' ", "")
    copy = tmp_path / "snap.sh"
    copy.write_text(s)
    home = _home(tmp_path)
    env = dict(os.environ, CTDC_SNAPSHOT_HOMES=f"ctdc-agent-test:{home}", CTDC_SNAPSHOT_OUT=str(tmp_path / "out"),
               CTDC_SNAPSHOT_OWNER=subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip())
    r = subprocess.run(["bash", str(copy)], env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "REFUSED ctdc-agent-test" in r.stderr
    assert list((tmp_path / "out" / "ctdc-agent-test").glob("*.tar.zst")) == []
