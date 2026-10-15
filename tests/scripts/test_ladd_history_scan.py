"""2026-10-14: LADD identifiers in a repository's whole history. Synthetic identifiers only (ZZ0001..)."""
from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("lhs", REPO / "scripts/leak-guard/ladd_history_scan.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(d)})


def test_finds_identifiers_removed_from_the_tip_and_in_messages(tmp_path):
    m = load()
    git(tmp_path, "init", "-q")
    (tmp_path / "a.md").write_text("see ZZ0001 here\n")
    git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "add doc")
    (tmp_path / "a.md").write_text("see the list\n")
    git(tmp_path, "commit", "-qam", "drop ZZ0002-B mention")
    found, counts = m.scan(tmp_path, {"ZZ0001", "ZZ0002", "ZZ0009"}, set())
    assert found == {"ZZ0001", "ZZ0002"}                 # gone from the tip, still in history
    assert counts["blob"] == 1 and counts["commit"] == 1


def test_replacement_rules_are_whole_token():
    m = load()
    (rule,) = m.replacement_lines({"ZZ0001"})
    pat, repl = rule[len("regex:"):].split("==>")
    assert repl == "LADD-REMOVED"
    assert re.sub(pat, repl, "x ZZ0001 y ZZ00012 AZZ0001 ZZ0001-B") == "x LADD-REMOVED y ZZ00012 AZZ0001 LADD-REMOVED-B"


def test_fail_closed_without_identifiers(tmp_path, monkeypatch, capsys):
    m = load()
    import io, sys
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert m.main(["--repo", str(tmp_path)]) == 2


def test_office_blobs_are_read_like_the_tripwire(tmp_path):
    # swarm audit NB-06: zip-based Office blobs used to be skipped silently
    import zipfile
    m = load()
    git(tmp_path, "init", "-q")
    with zipfile.ZipFile(tmp_path / "deck.pptx", "w") as z:
        z.writestr("ppt/slides/slide1.xml", "<a:t>tail ZZ0003 here</a:t>")
    git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "deck")
    found, counts = m.scan(tmp_path, {"ZZ0003"}, set())
    assert found == {"ZZ0003"} and counts["office"] == 1


def test_replacement_file_is_0600_even_if_it_existed(tmp_path, monkeypatch):
    import io, os, sys
    m = load()
    git(tmp_path, "init", "-q")
    (tmp_path / "a.md").write_text("ZZ0001\n"); git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "x")
    out = tmp_path / "repl.txt"; out.write_text(""); os.chmod(out, 0o644)
    monkeypatch.setattr(sys, "stdin", io.StringIO("ZZ0001\n"))
    assert m.main(["--repo", str(tmp_path), "--min-identifiers", "1", "--write-replacements", str(out)]) == 1
    assert (os.stat(out).st_mode & 0o777) == 0o600
