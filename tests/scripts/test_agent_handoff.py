"""
tests/scripts/test_agent_handoff.py -- the EA migration batch (operator 2026-10-26):
hand-off, read-only visibility, the shared /srv folders and the operator-clone settings.

- handoff-put.sh writes one patch with a header from an agent's clone without
  touching its index; scripts/lib/handoff_check.sh refuses a wrong pin,
  tampering, a wrong header or base, binaries, symlinks and protected paths;
  handoff-apply.sh keeps its own account / folder / owner / symlink rules (no
  test bypass: its success path needs a real second account and is proven on
  the first live hand-off);
- srv-layout.sh gives every ctdc-dev agent/service account its own private
  folder; srv-migrate copies, verifies, leaves a pointer and freezes the old
  folders; srv-access.sh grants only with a directive, never follows a
  symlink, logs every change;
- agent-status-snapshot.py publishes states and counts only;
- pg-ro-role.py: SCRAM verifier against RFC 7677, deny list, no password in SQL;
- pg-ro-query.py accepts one read statement only;
- clone-settings.py and plan.sh --operator-clone; the pamphlet names the
  account's private folder and the must-read notes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import os
import pwd
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUT = ROOT / "scripts" / "handoff-put.sh"
APPLY = ROOT / "scripts" / "handoff-apply.sh"
CHECK = ROOT / "scripts" / "lib" / "handoff_check.sh"
SEG = ROOT / "scripts" / "agent-segmentation"
ME = pwd.getpwuid(os.getuid()).pw_name


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _account_exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def _git_env(tmp: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=str(tmp / "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x.invalid")
    return env


def _git(env, *args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture()
def world(tmp_path):
    env = _git_env(tmp_path)
    (tmp_path / "gitconfig").write_text("[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n")
    op = tmp_path / "op" / "repo"
    op.mkdir(parents=True)
    _git(env, "init", "-q", cwd=op)
    (op / "README.md").write_text("hello\n")
    (op / "MANIFEST.sha256").write_text("x\n")
    (op / "security").mkdir()
    (op / "security" / "signing.env").write_text("pin\n")
    _git(env, "add", "-A", cwd=op)
    _git(env, "-c", "commit.gpgsign=false", "commit", "-qm", "base", cwd=op)
    clone = tmp_path / "agent-clone"
    _git(env, "clone", "-q", str(op), str(clone))
    drop = tmp_path / "handoff"
    drop.mkdir()
    env.update(HANDOFF_DROP=str(drop), HANDOFF_REPO=str(op), HANDOFF_WORKTREE_PARENT=str(tmp_path / "op"))
    base = _git(env, "rev-parse", "HEAD", cwd=op).strip()
    return {"env": env, "op": op, "clone": clone, "drop": drop, "tmp": tmp_path, "base": base}


def _put(w, name="fix-one"):
    return subprocess.run(["bash", str(PUT), name, "--subject", "a fix", "--repo", str(w["clone"])],
                          env=w["env"], capture_output=True, text=True)


def _patch(w, name="fix-one") -> Path:
    return w["drop"] / f"{name}-from-{ME}" / f"{name}.patch"


def _check(w, patch: Path, pin: str, account: str = ME):
    return subprocess.run(["bash", str(CHECK), str(patch), pin, account, str(w["op"])],
                          env=w["env"], capture_output=True, text=True)


# ---------------------------------------------------------------- handoff-put

def test_put_writes_one_patch_and_leaves_the_clone_index_alone(world):
    w = world
    (w["clone"] / "README.md").write_text("hello\nworld\n")
    (w["clone"] / "new.txt").write_text("new\n")
    (w["clone"] / "gone.txt").write_text("")  # untracked then removed: never in the patch
    (w["clone"] / "gone.txt").unlink()
    _git(w["env"], "add", "README.md", cwd=w["clone"])
    staged_before = _git(w["env"], "diff", "--cached", "--name-only", cwd=w["clone"])
    r = _put(w)
    assert r.returncode == 0, r.stderr
    p = _patch(w)
    assert stat.S_IMODE(p.stat().st_mode) == 0o644
    assert stat.S_IMODE(p.parent.stat().st_mode) & 0o022 == 0  # nobody else can add files to the folder
    assert _sha(p) in r.stdout and "handoff-apply.sh" in r.stdout
    head = p.read_text().split("\n\n", 1)[0]
    assert "ctdc-handoff: 1" in head and f"account: {ME}" in head and f"base-commit: {w['base']}" in head
    assert "diff --git a/new.txt b/new.txt" in p.read_text()
    assert _git(w["env"], "diff", "--cached", "--name-only", cwd=w["clone"]) == staged_before
    ok = _check(w, p, _sha(p))
    assert ok.returncode == 0, ok.stderr
    assert f"base {w['base']}" in ok.stdout and "subject a fix" in ok.stdout


def test_put_never_overwrites(world):
    (world["clone"] / "README.md").write_text("x\n")
    assert _put(world).returncode == 0
    r = _put(world)
    assert r.returncode != 0 and "already exists" in r.stderr


def test_put_refuses_binary_and_empty(world):
    r = _put(world)
    assert r.returncode != 0 and "nothing to hand off" in r.stderr
    (world["clone"] / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    r = _put(world)
    assert r.returncode != 0 and "binary" in r.stderr


# ---------------------------------------------------------------- handoff_check

def _forge(w, body: str, name="forged", account=ME, base=None) -> Path:
    base = base or w["base"]
    d = w["drop"] / f"{name}-from-{ME}"
    d.mkdir(exist_ok=True)
    p = d / f"{name}.patch"
    p.write_text(f"ctdc-handoff: 1\naccount: {account}\nbase-commit: {base}\nsubject: s\n\n{body}")
    return p


def _modify(path: str, old: str = "x", new: str = "y") -> str:
    return (f"diff --git a/{path} b/{path}\nindex 587be6b..0000000 100644\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1 +1 @@\n-{old}\n+{new}\n")


def _create(path: str, mode: str = "100644") -> str:
    return (f"diff --git a/{path} b/{path}\nnew file mode {mode}\n--- /dev/null\n+++ b/{path}\n"
            "@@ -0,0 +1 @@\n+x\n")


@pytest.mark.parametrize("body,why", [
    (_modify("MANIFEST.sha256"), "may not change"),
    (_modify("MANIFEST.sha256.asc"), "may not change"),
    (_modify("security/signing.env", "pin"), "may not change"),
    (_create("config/claude-code/managed-settings.json"), "may not change"),
    (_create("scripts/ctdc-claude-no-agent-hook.py"), "may not change"),
    (_create(".git/hooks/pre-commit"), "may not change"),
    (_create("sub/.git/config"), "may not change"),
    (_create("link", "120000"), "symlink"),
    (_create("vendored", "160000"), "submodule"),
    ("diff --git a/b.bin b/b.bin\nnew file mode 100644\nBinary files /dev/null and b/b.bin differ\n", "binary"),
    ('diff --git "a/x\\ty" "b/x\\ty"\nnew file mode 100644\n', "quoted path"),
    ("just text, no diff\n", "no file changes"),
])
def test_check_refuses_protected_or_unsafe_content(world, body, why):
    p = _forge(world, body)
    r = _check(world, p, _sha(p))
    assert r.returncode != 0 and why in r.stderr, r.stderr


def test_check_refuses_a_wrong_pin_and_tampering(world):
    p = _forge(world, _modify("README.md", "hello", "bye"))
    assert "sha256 mismatch" in _check(world, p, "0" * 64).stderr
    pin = _sha(p)
    p.write_text(p.read_text().replace("+bye", "+evil"))
    r = _check(world, p, pin)
    assert r.returncode != 0 and "never re-pin" in r.stderr


def test_check_refuses_a_wrong_header(world):
    p = _forge(world, _modify("README.md", "hello", "bye"), name="other", account="ctdc-someone-else")
    assert "header names account" in _check(world, p, _sha(p)).stderr
    p = _forge(world, _modify("README.md", "hello", "bye"), name="nobase", base="f" * 40)
    assert "is not in" in _check(world, p, _sha(p)).stderr
    p = world["drop"] / "raw.patch"
    p.write_text(_modify("README.md", "hello", "bye"))
    assert "not a ctdc hand-off" in _check(world, p, _sha(p)).stderr


def test_check_flags_privilege_paths_for_careful_review(world):
    p = _forge(world, _create("scripts/agent-segmentation/new-step.sh") + _create("scripts/approve-thing.sh"))
    r = _check(world, p, _sha(p))
    assert r.returncode == 0, r.stderr
    assert "careful scripts/agent-segmentation/new-step.sh" in r.stdout
    assert "careful scripts/approve-thing.sh" in r.stdout


# ---------------------------------------------------------------- handoff-apply's own rules

def _apply(w, account, patch, pin):
    return subprocess.run(["bash", str(APPLY), account, str(patch), pin], env=w["env"], capture_output=True, text=True)


def test_apply_takes_ctdc_team_accounts_only(world):
    (world["clone"] / "README.md").write_text("x\n")
    _put(world)
    p = _patch(world)
    r = _apply(world, ME, p, _sha(p))   # the operator's own account is not a team account
    assert r.returncode != 0 and "unknown account" in r.stderr
    r = _apply(world, "ctdc-no-such-account", p, _sha(p))
    assert r.returncode != 0 and "unknown account" in r.stderr


@pytest.mark.skipif(not _account_exists("ctdc-agent-anthropic-claude"), reason="needs a real team account on the box")
def test_apply_checks_folder_owner_and_symlinks(world):
    a = "ctdc-agent-anthropic-claude"
    d = world["drop"] / f"x-from-{a}"
    d.mkdir()
    p = d / "x.patch"
    p.write_text("ctdc-handoff: 1\n")
    r = _apply(world, a, p, _sha(p))
    assert r.returncode != 0 and f"not {a}" in r.stderr and "owned by" in r.stderr   # we own it, not the account
    link = d / "y.patch"
    link.symlink_to(p)
    r = _apply(world, a, link, _sha(p))
    assert r.returncode != 0 and "symlink" in r.stderr
    outside = world["tmp"] / "x.patch"
    outside.write_text("x")
    r = _apply(world, a, outside, _sha(outside))
    assert r.returncode != 0 and "not directly in a folder" in r.stderr
    r = _apply(world, a, world["drop"] / f"x-from-{ME}" / "x.patch", "0" * 64)
    assert r.returncode != 0 and f"does not end in -from-{a}" in r.stderr
    lst = subprocess.run(["bash", str(APPLY), "--list", a], env=world["env"], capture_output=True, text=True)
    assert lst.returncode == 0 and _sha(p) in lst.stdout   # symlinked entries are not listed
    assert lst.stdout.count(".patch") == 1


# ---------------------------------------------------------------- /srv layout, migration, access

def _dev_members() -> list[str]:
    out = subprocess.run(["getent", "group", "ctdc-dev"], capture_output=True, text=True).stdout.strip()
    return [m for m in out.split(":")[-1].split(",") if m] if out else []


@pytest.mark.skipif(not _dev_members(), reason="needs the ctdc-dev group")
def test_layout_gives_every_dev_member_account_a_private_folder(tmp_path):
    members = [m for m in _dev_members() if m.startswith("ctdc-")]
    reg = tmp_path / "accounts.conf"
    reg.write_text("".join(f"{m} agent claude 0\n" for m in members) + "ctdc-agent-not-in-group agent claude 0\n"
                   "ctdc-human-x human ssh 0\n")
    r = subprocess.run(["bash", str(SEG / "srv-layout.sh")], capture_output=True, text=True,
                       env={**os.environ, "SRV_ROOT": str(tmp_path / "srv"), "CTDC_REGISTRY": str(reg)})
    assert r.returncode == 0, r.stderr
    for m in members:
        assert f"{m} -> {tmp_path}/srv/ctdc-{m.rsplit('-', 1)[-1]}" in r.stdout
        assert f"install -d -m 2770 -o {m} -g corporatetraveldc {tmp_path}/srv/ctdc-{m.rsplit('-', 1)[-1]}" in r.stdout
    assert "skip ctdc-agent-not-in-group" in r.stdout and "ctdc-human-x" not in r.stdout
    assert f"install -d -m 3775 -o root -g ctdc-dev {tmp_path}/srv/ctdc-handoff" in r.stdout
    assert f"install -d -m 2770 -o corporatetraveldc -g corporatetraveldc {tmp_path}/srv/ctdc-blog" in r.stdout
    assert "d:u:corporatetraveldc:rwx" in r.stdout and "dry run" in r.stdout
    assert not (tmp_path / "srv").exists()


def _migrate(srv: Path, execute: bool):
    return subprocess.run(["bash", str(SEG / "srv-migrate-20261026.sh"), *(["--execute"] if execute else [])],
                          capture_output=True, text=True, env={**os.environ, "SRV_ROOT": str(srv)})


def test_migration_copies_verifies_points_and_freezes(tmp_path):
    srv = tmp_path / "srv"
    drop, research = srv / "ctdc-publish-drop", srv / "ctdc-publish-research"
    for d in (drop / "week-2026-10-19", drop / "es-private-feed-step-x", drop / "relays-x",
              research, srv / "ctdc-blog", srv / "ctdc-handoff" / "research-intake"):
        d.mkdir(parents=True, exist_ok=True)
    (drop / "week-2026-10-19" / "a.md").write_text("article\n")
    (drop / "es-private-feed-step-x" / "0001.patch").write_text("patch\n")
    (drop / "relays-x" / "run.sh").write_text("echo hi\n")
    (drop / "NOTE-for-operator.md").write_text("note\n")
    (research / "paper.pdf").write_bytes(b"%PDF")

    dry = _migrate(srv, False)
    assert dry.returncode == 0 and not (srv / "ctdc-blog" / "week-2026-10-19").exists()
    r = _migrate(srv, True)
    assert r.returncode == 0, r.stderr
    assert (srv / "ctdc-blog" / "week-2026-10-19" / "a.md").read_text() == "article\n"
    assert (srv / "ctdc-blog" / "es-private-feed-step-x" / "0001.patch").exists()
    assert (srv / "ctdc-handoff" / "relays-x" / "run.sh").exists()
    assert (srv / "ctdc-handoff" / "NOTE-for-operator.md").exists()
    assert (srv / "ctdc-handoff" / "research-intake" / "paper.pdf").read_bytes() == b"%PDF"
    assert (drop / "week-2026-10-19" / "a.md").exists()            # mirrored, never moved
    ptr = (drop / "MOVED-2026-10-26.md").read_text()
    assert "`week-2026-10-19`" in ptr and str(srv / "ctdc-blog" / "week-2026-10-19") in ptr
    assert "`relays-x`" in ptr and str(srv / "ctdc-handoff" / "relays-x") in ptr
    assert stat.S_IMODE(drop.stat().st_mode) & 0o222 == 0 and stat.S_IMODE(research.stat().st_mode) & 0o222 == 0
    again = _migrate(srv, True)
    assert again.returncode == 0 and "already mirrored" in again.stdout


def test_migration_stops_on_a_conflicting_destination(tmp_path):
    srv = tmp_path / "srv"
    for d in (srv / "ctdc-publish-drop" / "relays-x", srv / "ctdc-blog", srv / "ctdc-handoff" / "research-intake",
              srv / "ctdc-handoff" / "relays-x"):
        d.mkdir(parents=True)
    (srv / "ctdc-publish-drop" / "relays-x" / "run.sh").write_text("old\n")
    (srv / "ctdc-handoff" / "relays-x" / "run.sh").write_text("different\n")
    r = _migrate(srv, True)
    assert r.returncode == 3 and "nothing overwritten" in r.stderr
    assert (srv / "ctdc-handoff" / "relays-x" / "run.sh").read_text() == "different\n"


def _access(srv: Path, reg: Path, log: Path, *args):
    return subprocess.run(["bash", str(SEG / "srv-access.sh"), *args], capture_output=True, text=True,
                          env={**os.environ, "SRV_ROOT": str(srv), "CTDC_REGISTRY": str(reg), "SRV_ACCESS_LOG": str(log)})


@pytest.mark.skipif(not _account_exists("ctdc-agent-llama"), reason="needs a real account to name in an ACL")
def test_access_needs_a_directive_never_follows_symlinks_and_logs(tmp_path):
    srv, reg, log = tmp_path / "srv", tmp_path / "accounts.conf", tmp_path / "access.log"
    folder = srv / "ctdc-ea"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "note.md").write_text("n\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    (folder / "escape").symlink_to(outside)
    (srv / "ctdc-handoff").mkdir()
    reg.write_text("ctdc-agent-llama service none 0\n")
    acct = "ctdc-agent-llama"

    assert "needs --council" in _access(srv, reg, log, "grant", acct, "ctdc-ea").stderr
    assert "shared with every account" in _access(srv, reg, log, "grant", acct, "ctdc-handoff", "--operator", "x").stderr
    assert "not a registered" in _access(srv, reg, log, "grant", "ctdc-nobody", "ctdc-ea", "--operator", "x").stderr
    assert "council-<id>" in _access(srv, reg, log, "grant", acct, "ctdc-ea", "--council", "bogus").stderr

    r = _access(srv, reg, log, "grant", acct, "ctdc-ea", "--council", "council-abc123")
    assert r.returncode == 0, r.stderr
    facl = subprocess.run(["getfacl", "-cp", str(folder / "sub" / "note.md")], capture_output=True, text=True).stdout
    assert f"user:{acct}:r--" in facl
    dfacl = subprocess.run(["getfacl", "-cp", str(folder / "sub")], capture_output=True, text=True).stdout
    assert f"default:user:{acct}:r-x" in dfacl
    assert acct not in subprocess.run(["getfacl", "-cp", str(outside)], capture_output=True, text=True).stdout
    entry = log.read_text().strip().split("\t")
    assert entry[2:] == ["grant", acct, "ctdc-ea", "rX", "council council-abc123"]

    r = _access(srv, reg, log, "revoke", acct, "ctdc-ea")
    assert r.returncode == 0
    assert acct not in subprocess.run(["getfacl", "-Rcp", str(folder)], capture_output=True, text=True).stdout
    assert log.read_text().count("\n") == 2


# ---------------------------------------------------------------- status snapshot

FORBIDDEN_KEYS = {"Command", "Labels", "Mounts", "Ports", "Networks", "Env", "ExecStart", "Id", "Pid", "MESSAGE"}


def test_snapshot_publishes_states_and_counts_only(tmp_path, monkeypatch):
    snap = _load("snap", "scripts/agent-status-snapshot.py")
    assert snap.OUT == "/srv/ctdc-handoff/status/status.json"
    ps = [{"Names": ["c1"], "Image": "localhost/c1:latest", "State": "running", "Status": "Up",
           "StartedAt": 1_800_000_000, "ExitCode": 0, "Restarts": 0, "Command": ["--token=abc"],
           "Labels": {"k": "v"}, "Mounts": ["/etc/corporatetraveldc"], "Id": "deadbeef", "Pid": 1},
          {"Names": ["infra"], "IsInfra": True}]
    units = [{"unit": "corporatetraveldc-x.service", "load": "loaded", "active": "failed", "sub": "failed",
              "description": "d" * 400}]
    timers = [{"unit": "corporatetraveldc-x.timer", "activates": "corporatetraveldc-x.service",
               "next": 1_800_000_000_000_000, "last": 0}]
    journal = "\n".join(json.dumps({"_SYSTEMD_USER_UNIT": "corporatetraveldc-x.service", "MESSAGE": "secret"})
                        for _ in range(2))

    def run(cmd, timeout=30):
        if cmd[0] == "podman":
            return json.dumps(ps)
        if cmd[0] == "journalctl":
            return journal
        if "list-timers" in cmd:
            return json.dumps(timers)
        if "show" in cmd:
            return "Result=exit-code\nNRestarts=3\nExecStart={ path=/bin/x ; argv[]=/bin/x --token=abc }\n"
        if "--failed" in cmd:
            return "[]"
        return json.dumps(units)

    monkeypatch.setattr(snap, "_run", run)
    s = snap.snapshot()
    assert s["unavailable"] == []
    blob = json.dumps(s)
    assert "abc" not in blob and "secret" not in blob and "/etc/corporatetraveldc" not in blob
    for c in s["containers"]:
        assert not FORBIDDEN_KEYS & set(c)
    assert [c["name"] for c in s["containers"]] == ["c1"]
    u = s["units"][0]
    assert u["Result"] == "exit-code" and u["NRestarts"] == "3" and "ExecStart" not in u
    assert len(u["description"]) == snap.DESC_MAX
    assert s["journal_errors_60m"] == {"corporatetraveldc-x.service": 2}
    assert s["timers"][0]["next_utc"].endswith("Z") and s["timers"][0]["last_utc"] is None
    out = tmp_path / "status.json"
    snap.write_atomic(str(out), s)
    assert stat.S_IMODE(out.stat().st_mode) == 0o640 and json.loads(out.read_text())["schema"] == 1


def test_snapshot_records_a_failing_source_by_name_only(monkeypatch):
    snap = _load("snap2", "scripts/agent-status-snapshot.py")

    def boom(*a, **k):
        raise RuntimeError("leaky detail --token=abc")

    monkeypatch.setattr(snap, "_run", boom)
    s = snap.snapshot()
    assert {"units", "timers", "containers", "journal_errors_60m", "failed_system_units"} <= set(s["unavailable"])
    assert "abc" not in json.dumps(s)


def test_snapshot_units_are_wired():
    svc = (ROOT / ".config/systemd/user/corporatetraveldc-agent-status-snapshot.service").read_text()
    tmr = (ROOT / ".config/systemd/user/corporatetraveldc-agent-status-snapshot.timer").read_text()
    assert "scripts/agent-status-snapshot.py" in svc and "EnvironmentFile" not in svc
    assert "OnUnitActiveSec=5min" in tmr and "WantedBy=timers.target" in tmr


# ---------------------------------------------------------------- read-only role

def test_scram_verifier_matches_rfc7677():
    ro = _load("ro", "scripts/pg-ro-role.py")
    v = ro.scram_verifier("pencil", salt=base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ=="))
    stored_b64, server_b64 = v.split("$", 1)[1].split("$", 1)[1].split(":")
    auth = ("n=user,r=rOprNGfwEbeRWgbNEkqO,"
            "r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,"
            "c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0").encode()
    server_sig = hmac.new(base64.b64decode(server_b64), auth, hashlib.sha256).digest()
    assert base64.b64encode(server_sig).decode() == "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="
    proof = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    client_sig = hmac.new(base64.b64decode(stored_b64), auth, hashlib.sha256).digest()
    client_key = bytes(a ^ b for a, b in zip(proof, client_sig))
    assert hashlib.sha256(client_key).digest() == base64.b64decode(stored_b64)


def test_deny_list_covers_the_sensitive_tables():
    ro = _load("ro2", "scripts/pg-ro-role.py")
    sensitive = ["auth_tokens", "board_tokens", "oauth_tokens", "es_sessions", "exec_standard_tokens",
                 "approval_requests", "approval_signers", "faa_ladd_aircraft", "faa_ladd_removals",
                 "watchlist_entries", "runsheet", "chat_messages", "vault_documents", "osint_items",
                 "audit_log", "webhook_events", "pg_stat_statements", "session_grants", "console_sessions"]
    allowed, denied = ro.split_tables(sensitive + ["metar_history", "notams", "tfrs", "feed_state"], ro.load_deny())
    assert set(denied) == set(sensitive)
    assert allowed == ["feed_state", "metar_history", "notams", "tfrs"]


def test_apply_sql_carries_a_verifier_never_a_password(tmp_path):
    ro = _load("ro3", "scripts/pg-ro-role.py")
    pw = "correct-horse-battery-staple"
    sql = ro.apply_sql(ro.scram_verifier(pw), ["metar_history"])
    assert pw not in sql and "SCRAM-SHA-256$4096:" in sql
    assert "NOSUPERUSER" in sql and "NOINHERIT" in sql and 'GRANT SELECT ON TABLE public."metar_history"' in sql
    assert "INSERT" not in sql and "UPDATE ON" not in sql
    with pytest.raises(ValueError):
        ro.apply_sql(ro.scram_verifier(pw), ['x"; DROP TABLE y; --'])
    with pytest.raises(ValueError):
        ro.apply_sql(pw, ["metar_history"])
    empty = tmp_path / "deny.txt"
    empty.write_text("# nothing\n")
    with pytest.raises(ValueError):
        ro.load_deny(empty)


def test_env_keys_append_then_replace_in_place(tmp_path):
    ro = _load("ro4", "scripts/pg-ro-role.py")
    f = tmp_path / "secrets.env"
    f.write_text("A=1\nB=\"two\"")
    f.chmod(0o600)
    ino = f.stat().st_ino
    ro.set_env_keys(f, {"DISPATCH_PG_RO_USER": "dispatch_ro", "DISPATCH_PG_RO_PASSWORD": "p1"})
    assert f.read_text() == "A=1\nB=\"two\"\nDISPATCH_PG_RO_USER=dispatch_ro\nDISPATCH_PG_RO_PASSWORD=p1\n"
    ro.set_env_keys(f, {"DISPATCH_PG_RO_PASSWORD": "p2"})
    assert ro.read_env_keys(f) == {"A": "1", "B": "two", "DISPATCH_PG_RO_USER": "dispatch_ro", "DISPATCH_PG_RO_PASSWORD": "p2"}
    assert f.stat().st_ino == ino and stat.S_IMODE(f.stat().st_mode) == 0o600


@pytest.mark.parametrize("sql,ok", [
    ("SELECT 1", True), ("  with x as (select 1) select * from x;", True), ("EXPLAIN SELECT 1", True),
    ("SHOW server_version", True), ("TABLE notams", True),
    ("DELETE FROM notams", False), ("SELECT 1; DROP TABLE notams", False), ("", False),
    ("COPY notams TO PROGRAM 'id'", False), ("set role dispatch", False),
])
def test_query_helper_accepts_one_read_statement(sql, ok):
    assert _load("q", "scripts/pg-ro-query.py").statement_ok(sql) is ok


# ---------------------------------------------------------------- operator clone, plan, pamphlet

def test_clone_settings_rewrites_only_the_status_line():
    cs = _load("cs", "scripts/agent-segmentation/clone-settings.py")
    base = json.loads((ROOT / "config/claude-code/user-settings.baseline.json").read_text())
    out = cs.clone(base, "/home/ctdc-agent-anthropic-claude-ea")
    assert out["statusLine"]["command"] == "bash /home/ctdc-agent-anthropic-claude-ea/.claude/statusline-command.sh"
    assert {k: v for k, v in out.items() if k != "statusLine"} == {k: v for k, v in base.items() if k != "statusLine"}
    with pytest.raises(ValueError):
        cs.clone({**base, "apiKeyHelper": "/home/corporatetraveldc/bin/key"}, "/home/ctdc-agent-anthropic-claude-ea")
    with pytest.raises(ValueError):
        cs.clone(base, "/home/corporatetraveldc")
    assert "hooks" not in json.loads(cs.comparable({"hooks": {"x": 1}, "model": "opus"}))
    assert (ROOT / "config/claude-code/statusline-command.sh").read_text().startswith("#!/usr/bin/env bash")


def _plan(*args):
    return subprocess.run(["bash", str(SEG / "plan.sh"), "--dry-run", *args], capture_output=True, text=True)


def test_plan_layout_parents_clone_and_read_paths():
    out = _plan("--add-agent", "ctdc-agent-example", "--login-mode", "claude", "--operator-clone").stdout
    line = next(ln for ln in out.splitlines() if "install -d -m 0700 -o ctdc-agent-example" in ln and ".claude" in ln)
    for d in (".config", ".config/systemd", ".config/systemd/user", ".local", ".local/bin"):
        assert f"/home/ctdc-agent-example/{d} " in line + " ", d
    assert out.count("srv-layout.sh --execute") == 2      # shared step + after the account is registered
    assert "clone-settings.py" in out and "statusline-command.sh /home/ctdc-agent-example/.claude/" in out
    assert "safe.directory" in out and "/srv/ctdc-handoff/" in out and "ctdc-status" not in out
    assert "--operator-clone" not in _plan("--add-agent", "ctdc-agent-example").stdout
    bad = _plan("--add-service", "ctdc-agent-example", "--operator-clone")
    assert bad.returncode != 0 and "--operator-clone applies" in bad.stderr
    codex = (SEG / "plan.sh").read_text()
    assert "${H}/.local ${H}/.local/bin ${H}/.codex" in codex and "${H}/.config ${H}/.config/systemd ${H}/.config/systemd/user" in codex


def test_pamphlet_names_the_private_folder_and_the_must_reads():
    tpl = (SEG / "onboarding-template.md").read_text()
    assert "{{SRV_PRIVATE}}" in tpl and "/srv/ctdc-handoff/" in tpl and "/srv/ctdc-blog/" in tpl
    assert "PLATFORM-RULES-CANONICAL.md" in tpl.split("## Working style")[1]
    assert "Retired, read-only, never write there" in tpl
    assert "general two-way handoff" not in tpl
    assert '"/srv/ctdc-" + name.rsplit("-", 1)[-1]' in (SEG / "render-onboarding.sh").read_text()


def test_plan_masks_the_agent_daemons_for_every_agent_account():
    """2026-10-26 (operator: no SSH or GPG agent, ever; mask unless a non-signing use exists)."""
    for args in (("--add-agent", "ctdc-agent-example"), ("--add-agent", "ctdc-agent-example", "--login-mode", "codex")):
        out = _plan(*args).stdout
        line = next(ln for ln in out.splitlines() if "systemctl --user mask --now" in ln)
        for unit in ("gnome-keyring-daemon.socket", "gcr-ssh-agent.socket", "gpg-agent.socket",
                     "gpg-agent-ssh.socket", "keyboxd.socket", "dirmngr.socket"):
            assert unit in line, (args, unit)
    assert "systemctl --user mask" not in _plan("--add-service", "ctdc-agent-example").stdout
    assert '"6l ' in (SEG / "verify.sh").read_text()


def test_root_closure_and_verify_cover_the_batch():
    irc = (ROOT / "scripts/install-root-copies.sh").read_text()
    assert "scripts/agent-segmentation/srv-access.sh" in irc.split("SCRIPTS=(", 1)[1].split(")", 1)[0]
    v = (SEG / "verify.sh").read_text()
    for check in ('"6i ', '"6k ', '"9 ', '"9b ', '"9c ', '"5f ', '"6g ', '"6h '):
        assert check in v, check
