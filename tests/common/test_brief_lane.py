"""2026-10-20 (operator): an operational brief takes the long-runner unit lock, or on
contention the single-occupant hot-lane lock (second llama slot); neither within the
budget = exit 75, the old clean skip. Never kills, never starts a process."""
from __future__ import annotations

import ast
import fcntl
import os
from pathlib import Path

import pytest

from common import brief_lane as bl

SRC = Path(bl.__file__)


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _hold(lock_dir, name):
    fd = os.open(os.path.join(lock_dir, name), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_free_long_lock_takes_long_lane(tmp_path):
    c = Clock()
    lane, fd, waited = bl.acquire(str(tmp_path), 60, 600, 2, c.now, c.sleep)
    assert lane == "long" and waited == 0
    os.close(fd)


def test_contention_promotes_to_hot_after_long_first(tmp_path):
    held = _hold(str(tmp_path), bl.LONG_LOCK)
    c = Clock()
    lane, fd, waited = bl.acquire(str(tmp_path), 60, 600, 2, c.now, c.sleep)
    assert lane == "hot" and 60 <= waited < 64
    with pytest.raises(BlockingIOError):         # single occupant: a second brief cannot take it
        fcntl.flock(os.open(str(tmp_path / bl.HOT_LOCK), os.O_RDWR), fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.close(fd)
    os.close(held)


def test_both_busy_gives_up_at_budget(tmp_path):
    h1, h2 = _hold(str(tmp_path), bl.LONG_LOCK), _hold(str(tmp_path), bl.HOT_LOCK)
    c = Clock()
    lane, fd, waited = bl.acquire(str(tmp_path), 60, 600, 2, c.now, c.sleep)
    assert lane is None and fd is None and waited >= 600
    os.close(h1)
    os.close(h2)


def test_main_exits_75_and_logs_skip(tmp_path, capsys):
    h1, h2 = _hold(str(tmp_path), bl.LONG_LOCK), _hold(str(tmp_path), bl.HOT_LOCK)
    rc = bl.main(["--long-first", "0", "--budget", "0", "--lock-dir", str(tmp_path), "--", "x.py"])
    assert rc == 75 and "lane=none" in capsys.readouterr().out
    os.close(h1)
    os.close(h2)


def test_promoted_exec_sets_hot_lane_env(tmp_path, monkeypatch, capsys):
    held = _hold(str(tmp_path), bl.LONG_LOCK)
    seen = {}
    monkeypatch.setattr(bl.os, "execvpe", lambda f, args, env: seen.update(args=args, env=env))
    bl.main(["--long-first", "0", "--budget", "5", "--lock-dir", str(tmp_path), "--", "s.py"])
    assert seen["env"]["CTDC_LLM_LANE"] == "hot" and seen["args"][-1] == "s.py"
    assert "lane=hot" in capsys.readouterr().out
    os.close(held)


def test_long_lane_clears_any_inherited_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CTDC_LLM_LANE", "hot")
    seen = {}
    monkeypatch.setattr(bl.os, "execvpe", lambda f, args, env: seen.update(env=env))
    bl.main(["--lock-dir", str(tmp_path), "--", "s.py"])
    assert "CTDC_LLM_LANE" not in seen["env"]


def test_never_kills_or_spawns():
    """Standing rule: hot takes the second slot, never a third core, never a kill."""
    tree = ast.parse(SRC.read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imports = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
               for a in n.names} | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not names & {"kill", "killpg", "Popen", "system", "spawnv", "fork"}
    assert not imports & {"signal", "subprocess", "common"}     # stdlib only, runs before platform imports
