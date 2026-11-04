"""tests/scripts/test_handoff_mirror.py -- the bilateral /srv <-> vault handoff mirror (2026-11-03).
Synthetic files only; the vault is a dict, WebDAV is never reached."""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "handoff-mirror.py"
CLOUD = "ctdc-agent-anthropic-cowork"
REG = "N" + "12QZ"   # built at runtime: no registration shape in this file (public scrub rule)


@pytest.fixture()
def mirror(tmp_path, monkeypatch):
    srv = tmp_path / "srv"
    (srv / "research-intake").mkdir(parents=True)
    monkeypatch.setenv("HANDOFF_MIRROR_SRV", str(srv))
    monkeypatch.setenv("HANDOFF_MIRROR_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("HANDOFF_MIRROR_SINCE", "2026-11-03T00:00:00Z")
    spec = importlib.util.spec_from_file_location("handoff_mirror_t", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    vault: dict[str, bytes] = {}

    def put_create_only(path, content, content_type="text/markdown"):
        if path in vault:
            raise m.webdav_client.AlreadyExists(path)
        vault[path] = content.encode() if isinstance(content, str) else content

    def put(path, content, content_type="text/markdown"):
        vault[path] = content.encode() if isinstance(content, str) else content

    monkeypatch.setattr(m.webdav_client, "put_create_only", put_create_only)
    monkeypatch.setattr(m.webdav_client, "put", put)
    monkeypatch.setattr(m.webdav_client, "get", lambda p: vault.get(p))
    monkeypatch.setattr(m, "gate", lambda text, source="": (_ for _ in ()).throw(
        m.ScrubGateBlocked(["blocked"])) if "BLOCKME" in text else text)
    m._test_vault = vault
    m._test_srv = srv
    return m


def _drop(srv: Path, name: str, files: dict[str, bytes], old: bool = False) -> None:
    d = srv / name
    d.mkdir()
    for fn, b in files.items():
        (d / fn).write_bytes(b)
        if old:
            t = time.mktime((2026, 10, 1, 0, 0, 0, 0, 0, -1))
            os.utime(d / fn, (t, t))


def test_out_mirrors_new_text_drafts_and_indexes_the_rest(mirror):
    srv = mirror._test_srv
    _drop(srv, "plan-20261103-from-ctdc-agent-anthropic-claude-ea",
          {"README.md": f"# plan\nsee tail {REG} here\n".encode(), "fix.patch": b"diff --git a b\n"})
    _drop(srv, "old-20261001-from-ctdc-agent-openai-codex", {"NOTES.md": b"# old\n"}, old=True)
    rows = mirror.mirror_out(mirror._load_state(), "2026-11-03")
    by = {Path(r["source"]).name: r for r in rows}
    assert set(by) == {"README.md", "fix.patch"}                      # the old drop is before SINCE
    assert by["fix.patch"]["mirrored"] == "on the box only"
    dest = by["README.md"]["mirrored"]
    assert dest.startswith(mirror.OUT_ROOT + "/2026-11-03/plan-20261103-from-ctdc-agent-anthropic-claude-ea/")
    doc = mirror._test_vault[f"{mirror.webdav_client.BUSINESS_ROOT}/{dest}"].decode()
    assert REG not in doc and "[tail withheld]" in doc and "Withheld: 1" in doc
    assert "ctdc-agent-anthropic-claude-ea" in doc and by["README.md"]["sha256"] in doc


def test_out_is_idempotent_and_writes_current(mirror):
    srv = mirror._test_srv
    _drop(srv, "x-20261103-from-ctdc-agent-anthropic-claude-ea", {"a.md": b"# a\n"})
    st = mirror._load_state()
    rows = mirror.mirror_out(st, "2026-11-03")
    mirror.write_out_index(rows, "2026-11-03")
    mirror._save_state(st)
    cur = f"{mirror.webdav_client.BUSINESS_ROOT}/{mirror.OUT_ROOT}/CURRENT.md"
    assert b"Newest folder" in mirror._test_vault[cur]
    assert mirror.mirror_out(mirror._load_state(), "2026-11-03") == []


def test_out_skips_the_in_folder_and_scrub_blocked_files(mirror):
    srv = mirror._test_srv
    (srv / "research-intake" / "from-vault" / CLOUD).mkdir(parents=True)
    (srv / "research-intake" / "from-vault" / CLOUD / "x.md").write_text("# loop\n")
    (srv / "research-intake" / "note.md").write_text("# BLOCKME\n")
    rows = mirror.mirror_out(mirror._load_state(), "2026-11-03")
    assert [Path(r["source"]).name for r in rows] == ["note.md"]
    assert rows[0]["mirrored"] == "withheld (scrub gate)"


def test_in_copies_only_new_cloud_contributions(mirror, monkeypatch):
    root = mirror.CONTRIB_ROOT
    tree = {root: [("task-a", True)],
            f"{root}/task-a": [(CLOUD, True), ("ctdc-agent-anthropic-claude-ea", True)],
            f"{root}/task-a/{CLOUD}": [("20261103T101500Z-brief.md", False), ("20261020T000000Z-old.md", False)]}
    monkeypatch.setattr(mirror, "_propfind", lambda rel: tree.get(rel.strip("/"), []))
    br = mirror.webdav_client.BUSINESS_ROOT
    mirror._test_vault[f"{br}/{root}/task-a/{CLOUD}/20261103T101500Z-brief.md"] = b"# brief\n"
    st = mirror._load_state()
    rows = mirror.mirror_in(st)
    mirror.write_in_index(rows)
    assert len(rows) == 1
    out = mirror.IN_DIR / CLOUD / "task-a" / "20261103T101500Z-brief.md"
    assert out.read_bytes() == b"# brief\n"
    assert "20261103T101500Z-brief.md" in (mirror.IN_DIR / "CURRENT.md").read_text()
    assert mirror.mirror_in(st) == []


def test_skips_under_load(mirror, monkeypatch):
    monkeypatch.setattr(mirror.os, "getloadavg", lambda: (30.0, 0, 0))
    monkeypatch.setattr(mirror, "mirror_out", lambda *a: pytest.fail("ran under load"))
    assert mirror.main() == 0
