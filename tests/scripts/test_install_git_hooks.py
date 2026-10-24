"""2026-10-22 (operator): install-git-hooks.sh installs every hook the repo uses (pre-push,
pre-commit, post-commit), never the auto-pushing post-rewrite, works from a worktree, and is
idempotent. Runs against a throwaway repo; the website repos are overridden to none."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCES = ("scripts/install-git-hooks.sh", "scripts/pre-push", "scripts/pre-commit",
           "scripts/post-commit-doc-verify.sh", "scripts/post-rewrite", "scripts/git-hooks/site-pre-push")


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    for rel in SOURCES:
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, r / rel)
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


def _run(r: Path) -> str:
    env = {**os.environ, "INSTALL_HOOKS_SITE_REPOS": ""}
    p = subprocess.run(["bash", str(r / "scripts/install-git-hooks.sh")], env=env,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_installs_every_used_hook_and_is_idempotent(tmp_path):
    r = _repo(tmp_path)
    out = _run(r)
    hooks = r / ".git/hooks"
    for name, src in (("pre-push", "scripts/pre-push"), ("pre-commit", "scripts/pre-commit"),
                      ("post-commit", "scripts/post-commit-doc-verify.sh")):
        assert (hooks / name).read_bytes() == (r / src).read_bytes()
        assert os.access(hooks / name, os.X_OK)
        assert f"installed: {hooks / name}" in out
    assert not (hooks / "post-rewrite").exists()            # auto-push is never installed
    again = _run(r)
    assert "installed:" not in again and again.count("unchanged:") == 3


def test_works_from_a_worktree(tmp_path):
    r = _repo(tmp_path)
    g = ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-q", "-m", "x"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(g + ["worktree", "add", "-q", str(wt)], check=True)
    _run(wt)
    assert (r / ".git/hooks/pre-commit").is_file()           # the shared hooks dir, not wt/.git
