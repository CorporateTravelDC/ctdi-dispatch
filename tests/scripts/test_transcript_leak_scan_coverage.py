"""2026-10-20 (freeze bug fix, cleanup sweep): the transcript leak scan

  * keys "dispatch env loaded" on the Postgres settings common.db.conn() connects with
    (DISPATCH_PG_*), not DISPATCH_DB / DISPATCH_DB_BACKEND (dropping DISPATCH_DB would
    have switched the LADD class off silently);
  * reads the LADD list from stdin for the root unit (--ladd-stdin, implies --require-ladd)
    and agent-leak-scan.sh wires it;
  * covers every credential file the platform holds (SOURCES), env files by secret-named
    key only, binary and multi-line key files, public material skipped, unreadable files
    named as NOT CHECKED;
  * knows the vendor shapes (private-key blocks, GitHub, Anthropic, Tailscale, ntfy).

Every value below is built at runtime from random characters: nothing credential-shaped is
committed, and no real file or database is read."""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import random
import string
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/leak-guard/transcript_leak_scan.py"
WRAPPER = REPO / "scripts/leak-guard/agent-leak-scan.sh"
RNG = random.Random(20261020)
ALNUM = string.ascii_letters + string.digits


def rnd(n, alphabet=ALNUM):
    return "".join(RNG.choice(alphabet) for _ in range(n))


def load(monkeypatch, tmp_path, sources=""):
    env = tmp_path / "dispatch-secrets.env"
    env.write_text("")
    monkeypatch.setenv("LEAK_GUARD_SECRETS_ENV", str(env))
    monkeypatch.setenv("LEAK_GUARD_EXTRA_FILES", str(tmp_path / "none"))
    monkeypatch.setenv("LEAK_GUARD_SOURCES", sources)
    for k in ("DISPATCH_PG_HOST", "DISPATCH_PG_DB", "DISPATCH_PG_USER", "DISPATCH_DB", "DISPATCH_DB_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("tls_cov", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _old(p: Path):
    t = time.time() - 3600
    os.utime(p, (t, t))


def _root(tmp_path, text):
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    f = root / "t.jsonl"
    f.write_text(text)
    _old(f)
    return root, f


# ── task 2: the dispatch-env key ──────────────────────────────────────────────

def test_dispatch_env_keys_on_the_postgres_settings_not_dispatch_db(monkeypatch, tmp_path):
    m = load(monkeypatch, tmp_path)
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    monkeypatch.setenv("DISPATCH_DB", "/nonexistent/x.db")
    assert not m._dispatch_env()                    # the retired SQLite names alone are not the dispatch env
    monkeypatch.delenv("DISPATCH_DB")
    monkeypatch.delenv("DISPATCH_DB_BACKEND")
    for k in m.DISPATCH_ENV_KEYS:
        monkeypatch.setenv(k, "synthetic")
        assert m._dispatch_env(), k                 # without DISPATCH_DB the class is still attempted
        monkeypatch.delenv(k)
    assert set(m.DISPATCH_ENV_KEYS) == {"DISPATCH_PG_HOST", "DISPATCH_PG_DB", "DISPATCH_PG_USER"}


def test_the_keys_are_the_ones_config_dispatch_env_sets():
    names = {l.split("=", 1)[0] for l in (REPO / "config/dispatch.env").read_text().splitlines()
             if l and not l.startswith("#") and "=" in l}
    spec = importlib.util.spec_from_file_location("tls_keys", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert set(m.DISPATCH_ENV_KEYS) <= names


def test_dispatch_env_without_dispatch_db_still_fails_loud_when_unreadable(monkeypatch, tmp_path, capsys):
    m = load(monkeypatch, tmp_path)
    monkeypatch.setenv("DISPATCH_PG_HOST", str(tmp_path / "no-socket"))
    monkeypatch.setattr(m, "_ladd_from_db", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    root, _ = _root(tmp_path, "nothing\n")
    assert m.main(["scan", "--root", str(root)]) == 3
    assert "ERROR LADD class 'ladd-identifier' NOT CHECKED" in capsys.readouterr().out


# ── --ladd-stdin (the root unit) ──────────────────────────────────────────────

def _ladd_list(n=1200):
    marks = {"Q" + rnd(5, "ABCDEFGHJKLMNPRSTUVWXYZ0123456789") for _ in range(n)}
    return sorted(marks)


def test_ladd_stdin_checks_the_class_and_redacts(monkeypatch, tmp_path, capsys):
    m = load(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "_ladd_from_db", lambda: pytest.fail("--ladd-stdin never reads the DB"))
    monkeypatch.setattr(m, "_ladd_allow", lambda: set())
    ids = _ladd_list()
    root, f = _root(tmp_path, json.dumps({"out": f"seen {ids[3]} here"}) + "\n")
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO("\n".join(ids).encode())))
    assert m.main(["scan", "--root", str(root), "--ladd-stdin"]) == 1
    out = capsys.readouterr().out
    assert "ladd-identifier x1" in out and ids[3] not in out
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO("\n".join(ids).encode())))
    assert m.main(["redact", "--root", str(root), "--ladd-stdin"]) == 0
    assert "[ladd-redacted]" in f.read_text() and ids[3] not in f.read_text()


def test_ladd_stdin_empty_or_short_fails_closed(monkeypatch, tmp_path, capsys):
    m = load(monkeypatch, tmp_path)
    root, _ = _root(tmp_path, "nothing\n")
    for feed in (b"", b"Q12345\n"):
        monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(feed)))
        assert m.main(["scan", "--root", str(root), "--ladd-stdin"]) == 3
        assert "fail closed" in capsys.readouterr().out


def _wrapper_env(tmp_path, ladd_cmd):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DISPATCH_", "LEAK_GUARD_"))}
    env.update({"LEAK_GUARD_SECRETS_ENV": str(tmp_path / "none.env"), "LEAK_GUARD_EXTRA_FILES": str(tmp_path / "none"),
                "LEAK_GUARD_SOURCES": "", "LEAK_GUARD_LADD_CMD": ladd_cmd})
    return env


@pytest.mark.skipif(os.geteuid() == 0, reason="the test hook is ignored as root")
def test_root_wrapper_requires_the_ladd_class(tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("\n".join(_ladd_list()) + "\n")
    root, _ = _root(tmp_path, "nothing listed\n")
    ok = subprocess.run(["bash", str(WRAPPER), "--root", str(root)], capture_output=True, text=True,
                        env=_wrapper_env(tmp_path, f"cat {ids}"))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "PROOF:" in ok.stdout and "LADD class checked" in ok.stdout
    down = subprocess.run(["bash", str(WRAPPER), "--root", str(root)], capture_output=True, text=True,
                          env=_wrapper_env(tmp_path, "exit 1"))
    assert down.returncode == 3 and "NOT CHECKED" in down.stdout


def test_root_unit_runs_the_wrapper_at_nice_19_and_the_wrapper_is_installed():
    unit = (REPO / "systemd/system/corporatetraveldc-agent-leak-scan.service").read_text()
    execs = [l for l in unit.splitlines() if l.startswith("ExecStart=")]
    assert execs == ["ExecStart=/usr/local/libexec/ctdc/agent-leak-scan.sh"]
    assert "Nice=19" in unit
    wrapper = WRAPPER.read_text()
    assert wrapper.count("--require-ladd --ladd-stdin") == 2 and wrapper.count("nice -n 19") == 2
    assert "scripts/leak-guard/agent-leak-scan.sh" in (REPO / "scripts/install-root-copies.sh").read_text()


@pytest.mark.parametrize("path", ["scripts/push-public.sh", "scripts/relay-preflight.sh", "scripts/pre-push"])
def test_operator_side_callers_require_ladd_through_the_dispatch_env(path):
    text = (REPO / path).read_text()
    calls = [l for l in text.replace("\\\n", " ").splitlines()
             if "transcript_leak_scan.py" in l and " scan" in l and not l.lstrip().startswith(("#", "echo"))]
    assert calls, path
    for c in calls:
        assert "with-dispatch-env.sh" in c and "--require-ladd" in c and "nice -n 19" in c, (path, c)


# ── task 3: credential sources ────────────────────────────────────────────────

def test_sources_env_files_by_secret_name_binary_and_key_files(monkeypatch, tmp_path):
    etc, sec = tmp_path / "etc", tmp_path / "secrets"
    etc.mkdir(), sec.mkdir()
    pg_pw, demo_pw, sess, fr24, lat = rnd(32), rnd(28), rnd(48), rnd(16), "38.8" + "1" * 10
    (etc / "pgsql-secrets.env").write_text(f"POSTGRES_PASSWORD={pg_pw}\n")
    (etc / "demo.env").write_text(f"DEMO_PG_PASSWORD={demo_pw}\nLLAMA_BASE_URL=http://{rnd(20)}.local:8080\n")
    (etc / "demo-secrets.env").write_text(f"DEMO_SESSION_SECRET={sess}\nDISPATCH_DB_BACKEND=sqlite\n")
    (etc / "fr24feed-secrets.env").write_text(f"FR24KEY={fr24}\nLAT={lat}\nLONG=CHANGE_ME\n")
    tok = rnd(40)
    (etc / "ntfy-root.token").write_text(tok + "\n")
    binkey = bytes(RNG.randrange(256) for _ in range(32)).replace(b"\n", b"\x01")
    (sec / "demo-q-registry.key").write_bytes(binkey)
    body = [rnd(70, ALNUM + "+/") for _ in range(6)]
    (sec / "id_synthetic").write_text("-----BEGIN " + "OPENSSH PRIVATE" + " KEY-----\n" + "\n".join(body)
                                      + "\n-----END " + "OPENSSH PRIVATE" + " KEY-----\n")
    pub = rnd(40)
    (sec / "x.pub.asc").write_text(pub + "\n")                     # public material: skipped
    (sec / "signing.env").write_text(f"SIGNING_KEY_FINGERPRINT={rnd(40)}\n")
    (sec / "tailnet-disable").write_text(rnd(20) + "\n")
    m = load(monkeypatch, tmp_path, f"{etc}/*secrets*.env:{etc}/demo.env:{etc}/*.token:{sec}/*")
    vals = m.load_secret_values(tmp_path / "dispatch-secrets.env")
    flat = {f for forms in vals.values() for f in forms}
    assert vals["pgsql-secrets.env:POSTGRES_PASSWORD"] == [pg_pw.encode()]
    assert vals["demo.env:DEMO_PG_PASSWORD"] == [demo_pw.encode()]
    assert vals["demo-secrets.env:DEMO_SESSION_SECRET"] == [sess.encode()]
    assert vals["fr24feed-secrets.env:FR24KEY"] == [fr24.encode()]
    assert lat.encode() not in flat and not any(k.endswith(":LLAMA_BASE_URL") for k in vals)
    assert vals["file:ntfy-root.token"] == [tok.encode()]
    q = vals["file:demo-q-registry.key"]
    assert binkey.hex().encode() in q and base64.b64encode(binkey) in q
    ssh = vals["file:id_synthetic"]
    assert len(ssh) == m.KEY_BODY_LINES and ssh[0] == body[0].encode() and ssh[-1] == body[-1].encode()
    assert pub.encode() not in flat and not any("signing.env" in k or "tailnet-disable" in k for k in vals)
    blob = json.dumps({"o": f"pw {pg_pw} key {binkey.hex()} line {body[0]}"}).encode()
    hits = m.find(blob, vals)
    assert {"pgsql-secrets.env:POSTGRES_PASSWORD", "file:demo-q-registry.key", "file:id_synthetic"} <= set(hits)
    red = m.redact_blob(blob, vals)
    assert not m.find(red, vals) and pg_pw.encode() not in red


def test_a_value_already_loaded_is_counted_once(monkeypatch, tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    v = rnd(30)
    (etc / "ntfy-secrets.env").write_text(f"NTFY_SMTP_SENDER_PASS={v}\n")
    m = load(monkeypatch, tmp_path, f"{etc}/*secrets*.env")
    (tmp_path / "dispatch-secrets.env").write_text(f"NTFY_SMTP_SENDER_PASS={v}\n")
    vals = m.load_secret_values(tmp_path / "dispatch-secrets.env")
    assert list(vals) == ["NTFY_SMTP_SENDER_PASS"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_unreadable_files_are_named_not_checked_and_never_read(monkeypatch, tmp_path, capsys):
    etc = tmp_path / "etc"
    etc.mkdir()
    locked = etc / "cf-honeypot.token"
    locked.write_text(rnd(40))
    locked.chmod(0)
    try:
        m = load(monkeypatch, tmp_path, f"{etc}/*.token")
        root, _ = _root(tmp_path, "nothing\n")
        assert m.main(["scan", "--root", str(root)]) == 0              # a note, not a failure
        out = capsys.readouterr().out
        assert "1 credential file(s) NOT CHECKED" in out and str(locked) in out
    finally:
        locked.chmod(0o600)


def test_default_sources_cover_the_platform_files(monkeypatch):
    import pwd
    monkeypatch.delenv("LEAK_GUARD_SOURCES", raising=False)
    spec = importlib.util.spec_from_file_location("tls_defaults", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)                                         # import only: nothing is read
    home = pwd.getpwnam("corporatetraveldc").pw_dir
    assert m.SOURCES == ["/etc/corporatetraveldc/*secrets*.env", "/etc/corporatetraveldc/demo.env",
                         "/etc/corporatetraveldc/*.token", f"{home}/.secrets/*", f"{home}/*.ts.net.key"]


# ── task 3: token shapes ──────────────────────────────────────────────────────

def _pem(kind):
    # real newlines: json.dumps turns them into the backslash-n escapes a JSONL transcript carries
    return ("-----BEGIN " + kind + "-----\n" + "\n".join(rnd(64, ALNUM + "+/") for _ in range(4))
            + "\n-----END " + kind + "-----")


@pytest.mark.parametrize("label,value", [
    ("private-key-block", lambda: _pem("OPENSSH PRIVATE" + " KEY")),
    ("private-key-block", lambda: _pem("RSA PRIVATE" + " KEY")),
    ("private-key-block", lambda: _pem("PGP PRIVATE" + " KEY BLOCK")),
    ("github-token", lambda: "gh" + "p_" + rnd(36)),
    ("github-pat", lambda: "github" + "_pat_" + rnd(22) + "_" + rnd(59)),
    ("anthropic-key", lambda: "sk-" + "ant-api03-" + rnd(80, ALNUM + "-_")),
    ("tailscale-key", lambda: "ts" + "key-auth-" + rnd(12) + "-" + rnd(30)),
    ("tailscale-private-key", lambda: "priv" + "key:" + rnd(64, "0123456789abcdef")),
    ("ntfy-token", lambda: "t" + "k_" + rnd(29, string.ascii_lowercase + string.digits)),
])
def test_vendor_shapes_are_found_and_redacted(monkeypatch, tmp_path, label, value):
    m = load(monkeypatch, tmp_path)
    v = value()
    blob = json.dumps({"out": f"before {v} after"}).encode()
    hits = m.find(blob, {})
    assert hits.get(label) == 1, hits
    red = m.redact_blob(blob, {})
    assert f"[REDACTED:{label}]".encode() in red and b"before " in red and b" after" in red
    assert not m.find(red, {})


def test_prose_naming_the_shapes_is_not_a_hit(monkeypatch, tmp_path):
    m = load(monkeypatch, tmp_path)
    prose = ("zero GitHub gh" "p_ / github" "_pat_ tokens found; a -----BEGIN " "OPENSSH PRIVATE" " KEY----- header "
             "named in a doc, sk-" "ant- and ts" "key- prefixes, t" "k_ alone")
    assert m.find(prose.encode(), {}) == {}


# ── the public-tree scrub uses the same sources ───────────────────────────────

def test_public_tree_scrub_loads_the_other_credential_files(monkeypatch, tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    pg_pw, tok = rnd(32), rnd(40)
    (etc / "pgsql-demo-secrets.env").write_text(f"POSTGRES_PASSWORD={pg_pw}\n")
    (etc / "cf-honeypot.token").write_text(tok + "\n")
    monkeypatch.setenv("LEAK_GUARD_SOURCES", f"{etc}/*secrets*.env:{etc}/*.token")
    monkeypatch.setenv("LEAK_GUARD_EXTRA_FILES", str(tmp_path / "none"))
    spec = importlib.util.spec_from_file_location("scrub_cov", REPO / "scripts/scrub-public-tree.py")
    s = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s)
    monkeypatch.setattr(s, "_SECRETS_ENV_PATH", str(tmp_path / "no-dispatch-secrets.env"))
    vals = s._load_live_secret_values()
    assert vals["pgsql-demo-secrets.env:POSTGRES_PASSWORD"] == pg_pw.encode()
    assert vals["file:cf-honeypot.token"] == tok.encode()
