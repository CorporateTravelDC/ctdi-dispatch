"""tests/scripts/test_demo_env.py -- 2026-10-19 demo/production database split,
relay 2 (the flip): scripts/demo-env.py, the one tool that edits
/etc/corporatetraveldc/demo.env.

Held here: the SCRAM verifier matches RFC 7677; the password never reaches
argv, stdout or the server (only its verifier, on psql's stdin); the file is
edited in place (mode, inode kept); seed copies only the named non-secret
keys; the rollback switch flips DEMO_SOURCE and DISPATCH_DB_BACKEND as a pair
and the demo code then really reads the sovereign file and demo_access.db.
No podman, no socket, no real /etc file: subprocess.run is faked and every
path is under tmp_path.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import os
import secrets
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


def load():
    spec = importlib.util.spec_from_file_location("demo_env", REPO / "scripts/demo-env.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


@pytest.fixture
def env(tmp_path, monkeypatch):
    m = load()
    f = tmp_path / "demo.env"
    shutil.copy(REPO / "config/demo.env.example", f)
    os.chmod(f, 0o600)
    d = tmp_path / "dispatch.env"
    d.write_text("DISPATCH_PG_DB=corporatetraveldc\nDISPATCH_PG_PASSWORD=synthetic-prod-sentinel\n"
                 "LLAMA_BASE_URL=http://198.51.100.7:8093\nOLLAMA_CHAT_MODEL=synthetic-chat:latest\n"
                 "ULTRAFEEDER_URL=http://198.51.100.8:8080\n")
    monkeypatch.setattr(m, "DEMO_ENV", f)
    monkeypatch.setattr(m, "DISPATCH_ENV", d)
    monkeypatch.delenv("DEMO_ROLE_NEW_PASSWORD", raising=False)
    return m, f


class FakePodman:
    """Records every subprocess.run; `login` decides whether psql -U demo
    succeeds, and an ALTER ROLE on stdin makes later logins succeed with the
    password whose verifier it carried (checked by recomputing it)."""

    def __init__(self, m, login_with: str | None = None):
        self.m, self.calls, self.accepts = m, [], login_with

    def __call__(self, argv, input=None, stdin=None, capture_output=True, text=True, env=None, timeout=None):
        import subprocess
        self.calls.append({"argv": list(argv), "input": input, "env": dict(env or {})})
        if any("-f -" in a for a in argv):   # ALTER ROLE via stdin
            v = input.split("PASSWORD '", 1)[1].split("'", 1)[0]
            self.verifier = v
            return subprocess.CompletedProcess(argv, 0, "", "")
        pw = (env or {}).get("PGPASSWORD", "")
        ok = False
        if hasattr(self, "verifier"):
            it, rest = self.verifier.split("$", 1)[1].split(":", 1)
            salt = base64.b64decode(rest.split("$", 1)[0])
            ok = self.m.scram_verifier(pw, salt=salt, iterations=int(it)) == self.verifier
        elif self.accepts is not None:
            ok = pw == self.accepts
        return subprocess.CompletedProcess(argv, 0 if ok else 2, "1\n" if ok else "", "")


def test_scram_verifier_matches_rfc7677():
    m = load()
    salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    v = m.scram_verifier("pencil", salt=salt, iterations=4096)
    head, keys = v.split("$", 1)[0], v.split("$", 2)
    assert head == "SCRAM-SHA-256" and v.startswith("SCRAM-SHA-256$4096:W22ZaJ0SNY7soEsUEjb6gQ==$")
    stored_b64, server_b64 = keys[2].split(":")
    server_key = base64.b64decode(server_b64)
    auth = (b"n=user,r=rOprNGfwEbeRWgbNEkqO,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,"
            b"s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0")
    assert base64.b64encode(hmac.new(server_key, auth, "sha256").digest()).decode() == \
        "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="
    assert len(base64.b64decode(stored_b64)) == 32
    assert m.scram_verifier("pencil") != m.scram_verifier("pencil"), "fresh salt every time"


def test_password_shape():
    m = load()
    assert m.valid_password(secrets.token_urlsafe(32))
    for bad in ("short", "has space in it 123456789", "quote'xxxxxxxxxxxxxxxxxxxx", 'dq"xxxxxxxxxxxxxxxxxxxxxx',
                "esc\x1b[31mxxxxxxxxxxxxxxxxx", "nl\nxxxxxxxxxxxxxxxxxxxxxxx", "back\\slashxxxxxxxxxxxxxxx",
                "hash#xxxxxxxxxxxxxxxxxxxx", "x" * 129):
        assert not m.valid_password(bad), repr(bad)


def test_write_keys_in_place_keeps_mode_and_inode(env):
    m, f = env
    ino = f.stat().st_ino
    changed = m.write_keys({"DEMO_SOURCE": "sqlite", "NEW_KEY": "v"})
    assert changed == ["DEMO_SOURCE", "NEW_KEY"]
    assert f.stat().st_ino == ino and (f.stat().st_mode & 0o777) == 0o600
    text = f.read_text()
    assert "\nDEMO_SOURCE=sqlite\n" in text and text.endswith("NEW_KEY=v\n")
    assert "# /etc/corporatetraveldc/demo.env" in text, "comments preserved"
    assert m.write_keys({"DEMO_SOURCE": "sqlite"}) == []
    with pytest.raises(m.EnvFileError):
        m.write_keys({"X": 'quoted"value'})
    os.chmod(f, 0o640)
    with pytest.raises(m.EnvFileError, match="0600"):
        m.write_keys({"DEMO_SOURCE": "postgres"})


def test_missing_file_names_the_hand_step(env):
    m, f = env
    f.unlink()
    with pytest.raises(m.EnvFileError, match="sudo install -m 600 -o corporatetraveldc"):
        m._check_file()  # noqa: SLF001


def test_check_seed_and_forbidden_keys(env, capsys):
    m, f = env
    problems = m.check()
    assert "DEMO_PG_PASSWORD not set" in problems and "LLAMA_BASE_URL not seeded" in problems
    assert sorted(m.seed()) == ["LLAMA_BASE_URL", "OLLAMA_CHAT_MODEL"]
    assert m.seed() == []
    text = f.read_text()
    assert "LLAMA_BASE_URL=http://198.51.100.7:8093" in text
    active = [l for l in text.splitlines() if l and not l.startswith("#")]
    assert not any(l.startswith(("DISPATCH_PG_", "ULTRAFEEDER_URL")) for l in active)
    assert "synthetic-prod-sentinel" not in text
    m.write_keys({"DEMO_PG_PASSWORD": secrets.token_urlsafe(32)})
    assert m.check() == []
    m.write_keys({"DISPATCH_PG_USER": "dispatch"})
    assert "forbidden key DISPATCH_PG_USER (production)" in m.check()
    f.write_text("\n".join(l for l in f.read_text().splitlines() if not l.startswith("DISPATCH_PG_USER")) + "\n")
    m.write_keys({"DEMO_SOURCE": "sqlite"})
    assert any(p.startswith("switch half-set") for p in m.check())
    assert m.main(["check"]) == 1


def test_rollback_switch_is_one_step_and_the_code_follows_it(env, monkeypatch):
    """`source sqlite` sets the pair; with the file's values in the process
    environment (what podman --env-file does), demo-api reads the sovereign
    file and the profile store is demo_access.db; `source postgres` undoes it."""
    m, f = env
    assert m.main(["source", "sqlite"]) == 0
    vals = m.read_env()
    assert (vals["DEMO_SOURCE"], vals["DISPATCH_DB_BACKEND"]) == ("sqlite", "sqlite")
    from demo import demo_api, profiles
    # conftest pins backend() to sqlite for every test; read the env here as the real one does (no pool is opened)
    monkeypatch.setattr(profiles.db_backend, "backend", lambda: os.environ.get("DISPATCH_DB_BACKEND", "sqlite").strip().lower())
    for k in ("DEMO_SOURCE", "DISPATCH_DB_BACKEND", "DISPATCH_ROLE"):
        monkeypatch.setenv(k, vals[k])
    assert demo_api.source_kind() == "sqlite" and profiles.store_kind() == "sqlite"
    assert m.main(["source", "postgres"]) == 0
    vals = m.read_env()
    for k in ("DEMO_SOURCE", "DISPATCH_DB_BACKEND"):
        monkeypatch.setenv(k, vals[k])
    assert demo_api.source_kind() == "postgres" and profiles.store_kind() == "postgres"
    assert m.main(["source", "mysql"]) == 2


def test_set_password_generates_writes_first_then_applies_by_verifier(env, monkeypatch, capsys):
    m, f = env
    fake = FakePodman(m)
    monkeypatch.setattr(m.subprocess, "run", fake)
    assert m.main(["password-state"]) == 0 and capsys.readouterr().out.strip() == "unset"
    assert m.main(["set-password"]) == 0
    pw = m.read_env()["DEMO_PG_PASSWORD"]
    assert pw != "CHANGE_ME" and m.valid_password(pw)
    out = capsys.readouterr()
    assert pw not in out.out + out.err
    alter = [c for c in fake.calls if any("-f -" in a for a in c["argv"])]
    assert len(alter) == 1
    assert "SCRAM-SHA-256$" in alter[0]["input"] and pw not in alter[0]["input"]
    assert "log_statement = 'none'" in alter[0]["input"]
    for c in fake.calls:
        assert not any(pw in a for a in c["argv"]), "password on argv"
    logins = [c for c in fake.calls if not any("-f -" in a for a in c["argv"])]
    assert logins and all(c["argv"][:4] == ["podman", "exec", "-e", "PGPASSWORD"] for c in logins)
    assert all(c["env"].get("PGPASSWORD") == pw for c in logins)
    # rerun: the value logs in -> nothing applied
    n = len(fake.calls)
    assert m.main(["set-password"]) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert not [c for c in fake.calls[n:] if any("-f -" in a for a in c["argv"])]
    assert m.main(["password-state"]) == 0 and capsys.readouterr().out.strip() == "ok"


def test_set_password_typed_value_and_mismatch_reapplies_same_value(env, monkeypatch, capsys):
    m, f = env
    typed = "typed-" + secrets.token_urlsafe(24)
    monkeypatch.setenv("DEMO_ROLE_NEW_PASSWORD", typed)
    fake = FakePodman(m)
    monkeypatch.setattr(m.subprocess, "run", fake)
    assert m.main(["set-password"]) == 0
    assert m.read_env()["DEMO_PG_PASSWORD"] == typed
    assert "DEMO_ROLE_NEW_PASSWORD" not in os.environ, "consumed from the environment"
    assert typed not in capsys.readouterr().out
    # the server lost it (e.g. instance rebuilt): the SAME file value is re-applied, file unchanged
    fake2 = FakePodman(m, login_with="something-else")
    monkeypatch.setattr(m.subprocess, "run", fake2)
    assert m.main(["password-state"]) == 0 and capsys.readouterr().out.strip() == "mismatch"
    before = f.read_text()
    assert m.main(["set-password"]) == 0
    assert f.read_text() == before
    assert "re-applying" in capsys.readouterr().out
    assert m.main(["password-state"]) == 0 and capsys.readouterr().out.strip() == "ok"


def test_set_password_refuses_a_bad_typed_value_and_writes_nothing(env, monkeypatch, capsys):
    m, f = env
    monkeypatch.setenv("DEMO_ROLE_NEW_PASSWORD", "bad value with spaces\x1b[0m")
    fake = FakePodman(m)
    monkeypatch.setattr(m.subprocess, "run", fake)
    before = f.read_text()
    assert m.main(["set-password"]) == 2
    assert f.read_text() == before and not fake.calls
    assert "\x1b" not in capsys.readouterr().err


def test_apply_failure_prints_no_statement(env, monkeypatch, capsys):
    m, f = env
    import subprocess

    def failing(argv, **kw):
        return subprocess.CompletedProcess(argv, 3, "", "ERROR: ... ALTER ROLE demo PASSWORD 'SCRAM-SHA-256$x'")
    monkeypatch.setattr(m.subprocess, "run", failing)
    assert m.main(["set-password"]) == 2
    err = capsys.readouterr().err
    assert "psql rc=3" in err and "SCRAM" not in err
