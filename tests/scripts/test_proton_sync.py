"""tests/scripts/test_proton_sync.py -- 2026-11-25: the Proton Drive CLI mirror (scripts/backup/proton_sync.py).
A fake `proton-drive` keeps its remote tree in a JSON file and speaks the 0.9.0 JSON shapes seen in the 11-25 test."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("proton_sync", REPO / "scripts/backup/proton_sync.py")
ps = importlib.util.module_from_spec(spec); spec.loader.exec_module(ps)

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
st = os.environ["FAKE_PD_STATE"]
tree = json.load(open(st))
a = sys.argv[1:]
log = tree.setdefault("calls", [])
log.append(a)
def save(): json.dump(tree, open(st, "w"))
def kids(p):
    p = p.rstrip("/")
    return {k: v for k, v in tree["nodes"].items() if os.path.dirname(k) == p}
if a[:2] == ["filesystem", "list"]:
    p = a[3]
    if p.rstrip("/") not in tree["nodes"] and p not in ("/my-files",):
        print("not found", file=sys.stderr); save(); sys.exit(1)
    out = []
    for k, v in kids(p).items():
        n = {"name": {"ok": True, "value": os.path.basename(k)}, "type": v["type"], "state": v.get("state", "active"),
             "contentAuthor": {"ok": True, "value": "someone@example.invalid"}}
        if v["type"] == "file":
            n["activeRevision"] = {"claimedSize": v["size"]} if v.get("size") is not None else {}
        out.append(n)
    print(json.dumps(out)); save(); sys.exit(0)
if a[:2] == ["filesystem", "create-folder"]:
    tree["nodes"][a[2].rstrip("/") + "/" + a[3]] = {"type": "folder"}; save(); sys.exit(0)
if a[:2] == ["filesystem", "upload"]:
    local, parent = a[-2], a[-1]
    name = os.path.basename(local)
    fails = tree.setdefault("fail", {})
    if fails.get(name, 0) > 0:
        fails[name] -= 1; save(); print("upload error", file=sys.stderr); sys.exit(2)
    if parent.rstrip("/") not in tree["nodes"]:
        save(); print("parent missing", file=sys.stderr); sys.exit(1)
    tree["nodes"][parent.rstrip("/") + "/" + name] = {"type": "file", "size": os.path.getsize(local)}
    save(); sys.exit(0)
if a[:2] == ["filesystem", "trash"]:
    tree["nodes"].pop(a[2], None); tree.setdefault("trashed", []).append(a[2]); save(); sys.exit(0)
save(); sys.exit(9)
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    cli = tmp_path / "proton-drive"
    cli.write_text(FAKE); cli.chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"nodes": {"/my-files/ctdi-backup": {"type": "folder"}}}))
    monkeypatch.setenv("FAKE_PD_STATE", str(state))
    repo = tmp_path / "borg"
    for rel, size in {"config": 10, "README": 5, "nonce": 4, "index.12": 30, "hints.12": 8, "integrity.12": 6,
                      "lock.roster": 3, "data/0/1": 100, "data/0/12": 200, "data/0/2": 50}.items():
        p = repo / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x" * size)
    sha = hashlib.sha512(cli.read_bytes()).hexdigest()

    def run(*extra):
        return ps.main(["--repo", str(repo), "--remote", "/my-files/ctdi-backup", "--cli", str(cli),
                        "--cli-sha512", sha, *extra])

    def tree():
        return json.loads(state.read_text())

    def edit(fn):
        t = tree(); fn(t); state.write_text(json.dumps(t))

    return run, tree, edit, repo


def uploads(t):
    return [c[-2].split("/borg/", 1)[1] for c in t["calls"] if c[:2] == ["filesystem", "upload"]]


def test_full_upload_orders_segments_first_and_skips_locks(env, capsys):
    run, tree, _, _ = env
    assert run() == 0
    up = uploads(tree())
    assert up[:3] == ["data/0/1", "data/0/2", "data/0/12"]            # numeric segment order
    assert set(up[3:]) == {"config", "README", "nonce", "index.12", "hints.12", "integrity.12"}
    assert "lock.roster" not in up
    assert "/my-files/ctdi-backup/data/0/12" in tree()["nodes"]
    out = capsys.readouterr().out
    assert "example.invalid" not in out                                # never the account
    assert json.loads(out.strip().splitlines()[-1])["complete"] is True


def test_rerun_uploads_nothing(env):
    run, tree, edit, _ = env
    assert run() == 0
    edit(lambda t: t.__setitem__("calls", []))
    assert run() == 0
    assert uploads(tree()) == []


def test_changed_size_is_reuploaded(env):
    run, tree, edit, repo = env
    assert run() == 0
    (repo / "index.12").write_bytes(b"y" * 31)
    edit(lambda t: t.__setitem__("calls", []))
    assert run() == 0
    assert uploads(tree()) == ["index.12"]


def test_unsized_remote_file(env):
    run, tree, edit, _ = env
    assert run() == 0
    edit(lambda t: (t["nodes"]["/my-files/ctdi-backup/data/0/1"].__setitem__("size", None), t.__setitem__("calls", [])))
    assert run("--unsized", "skip") == 0
    assert uploads(tree()) == []
    assert run() == 0
    assert uploads(tree()) == ["data/0/1"]


def test_draft_node_does_not_count_as_copy(env):
    run, tree, edit, _ = env
    assert run() == 0
    edit(lambda t: (t["nodes"]["/my-files/ctdi-backup/data/0/2"].__setitem__("state", "draft"), t.__setitem__("calls", [])))
    assert run() == 0
    assert uploads(tree()) == ["data/0/2"]


def test_one_retry_then_failure_is_incomplete_and_nothing_trashed(env):
    run, tree, edit, _ = env
    assert run() == 0
    edit(lambda t: t["nodes"].__setitem__("/my-files/ctdi-backup/data/0/99", {"type": "file", "size": 1}))
    (env[3] / "data/0/1").write_bytes(b"z" * 101)
    edit(lambda t: t.__setitem__("fail", {"1": 1}))                    # fails once, the retry succeeds
    assert run() == 0
    assert "/my-files/ctdi-backup/data/0/99" in tree()["trashed"]
    (env[3] / "data/0/1").write_bytes(b"z" * 102)
    edit(lambda t: (t.__setitem__("fail", {"1": 2}),
                    t["nodes"].__setitem__("/my-files/ctdi-backup/data/0/98", {"type": "file", "size": 1})))
    assert run() == 4
    assert "/my-files/ctdi-backup/data/0/98" not in tree()["trashed"]  # incomplete pass never removes


def test_delete_guards(env):
    run, tree, edit, _ = env
    assert run() == 0
    edit(lambda t: [t["nodes"].__setitem__(f"/my-files/ctdi-backup/data/0/{900 + i}", {"type": "file", "size": 1})
                    for i in range(5)])
    assert run("--max-deletes", "3") == 0
    assert not tree().get("trashed")
    assert run("--no-delete") == 0
    assert not tree().get("trashed")
    edit(lambda t: [t["nodes"].__setitem__(f"/my-files/ctdi-backup/data/1/{i}", {"type": "file", "size": 1})
                    for i in range(40)])
    assert run("--max-deletes", "1000") == 0                           # local is under 90% of remote
    assert not tree().get("trashed")


def test_refusals(env, tmp_path):
    run, tree, edit, repo = env
    assert ps.main(["--repo", str(repo), "--remote", "/my-files/ctdi-backup", "--cli", str(tmp_path / "proton-drive"),
                    "--cli-sha512", "0" * 128]) == 3
    assert "calls" not in tree()                                       # a mismatched binary is never run
    edit(lambda t: t["nodes"].pop("/my-files/ctdi-backup"))
    assert run() == 3
    assert not uploads(tree())                                         # never creates the root by accident
    os.remove(repo / "config")
    assert run() == 3


def test_dry_run_changes_nothing(env):
    run, tree, _, _ = env
    assert run("--dry-run") == 0
    t = tree()
    assert not [c for c in t["calls"] if c[:2] != ["filesystem", "list"]]


def test_metered_uplink_stops_with_5(env, monkeypatch):
    run, tree, _, _ = env
    monkeypatch.setattr(ps, "default_iface", lambda: "enu1")
    assert run("--stop-if-iface", "enu1") == 5
    assert uploads(tree()) == []
    monkeypatch.setattr(ps, "default_iface", lambda: "wld0")
    assert run("--stop-if-iface", "enu1") == 0


def test_average_cap_waits_after_a_fast_file(env, monkeypatch):
    run, _, _, _ = env
    waits = []
    monkeypatch.setattr(ps, "_sleep", waits.append)
    assert run("--avg-cap-mb", "0.00001") == 0            # 10 B/s: every tiny file "went too fast"
    assert len(waits) == 9 and all(w > 0 for w in waits)
