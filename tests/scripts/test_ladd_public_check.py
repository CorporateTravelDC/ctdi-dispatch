"""2026-10-09: push-time LADD tripwire. Synthetic identifiers only."""
from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LADD = ["QSYN1", "QX-ABC", "QSYN2"] + [f"QFILL{i}" for i in range(1000)]


def load():
    spec = importlib.util.spec_from_file_location("lpc", REPO / "scripts/leak-guard/ladd_public_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run(m, monkeypatch, tree, idents=LADD, **kw):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(idents) + "\n"))
    return m.main(["--tree", str(tree), *sum(([f"--{k.replace('_', '-')}", str(v)] for k, v in kw.items()), [])])


def test_hits_report_locations_never_values(monkeypatch, tmp_path, capsys):
    m = load()
    (tmp_path / "doc.md").write_text("fine line\nentries like QSYN1 and QX-ABC\nalso N-QSYN2 here\n")
    (tmp_path / "clean.md").write_text("qsyn1 lower case and QSYN10 longer token\n")
    assert run(m, monkeypatch, tmp_path) == 1
    out = capsys.readouterr().out
    assert "doc.md: line(s) 2, 3" in out and "clean.md" not in out
    assert "QSYN" not in out and "QX-ABC" not in out


def test_clean_tree_passes_and_allowlist_is_by_hash(monkeypatch, tmp_path):
    m = load()
    (tmp_path / "a.txt").write_text("the word QSYN1 is a coincidence here\n")
    allow = tmp_path / "allow.sha256"
    allow.write_text(hashlib.sha256(b"QSYN1").hexdigest() + "  coincidence\n")
    monkeypatch.setattr(m, "ALLOW", allow)
    allow.rename(tmp_path.parent / "allow.sha256")                     # keep it out of the scanned tree
    monkeypatch.setattr(m, "load_allow", lambda: m.__dict__["_orig_load_allow"](tmp_path.parent / "allow.sha256"))
    m._orig_load_allow = load().load_allow
    assert run(m, monkeypatch, tmp_path) == 0


def test_fails_closed_without_identifiers(monkeypatch, tmp_path, capsys):
    m = load()
    (tmp_path / "a.txt").write_text("anything\n")
    assert run(m, monkeypatch, tmp_path, idents=[]) == 2
    assert "FAIL CLOSED" in capsys.readouterr().out


def test_office_files_are_scanned_through_their_xml(monkeypatch, tmp_path, capsys):
    import zipfile
    m = load()
    with zipfile.ZipFile(tmp_path / "deck.pptx", "w") as z:
        z.writestr("ppt/slides/slide1.xml", "<a:t>tail</a:t><a:t>QSYN2</a:t>")
    (tmp_path / "blob.bin").write_bytes(b"\0QSYN1")
    assert run(m, monkeypatch, tmp_path) == 1
    out = capsys.readouterr().out
    assert "deck.pptx!ppt/slides/slide1.xml: line(s) 1" in out and "blob.bin" not in out
