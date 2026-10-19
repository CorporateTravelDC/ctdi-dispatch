"""2026-10-19 (operator, Gate-1b): scripts/populate-secrets.sh rewrite. Temp HOME and temp
target only; synthetic values only; the real /etc file is never touched."""
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FA = "fakeAeroApiKey0123456789"
JS = "fakeJumpseatTok0123456789"
BOARD = "fakeBoardKey0123456789"


@pytest.fixture
def ps(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".secrets").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    etc = tmp_path / "etc"
    etc.mkdir()
    target = etc / "dispatch-secrets.env"
    target.write_bytes(b"# header comment\nKEEP_ME=untouched value\nBOARD_KEY=old\nFLIGHTAWARE_API_KEY=\n")
    target.chmod(0o600)
    spec = importlib.util.spec_from_file_location("populate_secrets", REPO / "scripts/lib/populate_secrets.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m, home / ".secrets", target


def _put(d: Path, name: str, value: bytes):
    (d / name).write_bytes(value)
    (d / name).chmod(0o600)


def test_writes_both_aeroapi_and_both_jumpseat_names_and_keeps_other_lines(ps, capsys):
    m, sec, target = ps
    _put(sec, "flightaware.key", FA.encode() + b"\n")
    _put(sec, "jumpseat.token", JS.encode() + b"\n")
    _put(sec, "board.key", BOARD.encode())
    assert m.main(["--target", str(target)]) == 0
    out = capsys.readouterr().out
    lines = target.read_text().splitlines()
    assert lines[:2] == ["# header comment", "KEEP_ME=untouched value"]
    assert f"BOARD_KEY={BOARD}" == lines[2] and f"FLIGHTAWARE_API_KEY={FA}" == lines[3]   # replaced in place
    for want in (f"FLIGHTAWARE_AEROAPI_KEY={FA}", f"JUMPSEAT_API_KEY={JS}", f"ACARSDRAMA_JUMPSEAT_TOKEN={JS}"):
        assert want in lines                                                            # appended
    for v in (FA, JS, BOARD):
        assert v not in out                                                             # never printed
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_dry_run_prints_names_and_lengths_only_and_writes_nothing(ps, capsys):
    m, sec, target = ps
    _put(sec, "flightaware.key", FA.encode())
    before = target.read_bytes()
    assert m.main(["--dry-run", "--target", str(target)]) == 0
    out = capsys.readouterr().out
    assert f"FLIGHTAWARE_API_KEY <{len(FA)} chars>" in out and f"FLIGHTAWARE_AEROAPI_KEY <{len(FA)} chars>" in out
    assert FA not in out and target.read_bytes() == before


@pytest.mark.parametrize("bad", [b"\x1b[31m", b"\r", b"\t", b"\x00", b"\x7f", b"\n"])
def test_control_or_escape_character_is_refused_naming_the_file(ps, capsys, bad):
    m, sec, target = ps
    _put(sec, "flightaware.key", b"fakeKey" + bad + b"Tail0123")
    _put(sec, "board.key", BOARD.encode())
    before = target.read_bytes()
    assert m.main(["--target", str(target)]) == 2
    out = capsys.readouterr().out
    assert "flightaware.key" in out and "control or escape character" in out
    assert "fakeKey" not in out and "Tail0123" not in out
    assert target.read_bytes() == before                                                # nothing written


def test_unwritable_directory_rewrites_in_place_keeping_inode_and_mode(ps, capsys, tmp_path):
    m, sec, target = ps
    _put(sec, "board.key", BOARD.encode())
    ino = target.stat().st_ino
    target.parent.chmod(0o550)                       # like root:corporatetraveldc 0750 for this user
    try:
        assert m.main(["--target", str(target)]) == 0
    finally:
        target.parent.chmod(0o750)
    assert "rewritten in place" in capsys.readouterr().out
    assert target.stat().st_ino == ino and stat.S_IMODE(target.stat().st_mode) == 0o600
    assert f"BOARD_KEY={BOARD}\n" in target.read_text()
    assert not [p for p in target.parent.iterdir() if p.name.startswith(".")]          # no temp left


def test_writable_directory_uses_temp_and_rename(ps, capsys):
    m, sec, target = ps
    _put(sec, "board.key", BOARD.encode())
    ino = target.stat().st_ino
    assert m.main(["--target", str(target)]) == 0
    assert "temp file + rename" in capsys.readouterr().out
    assert target.stat().st_ino != ino and stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not [p for p in target.parent.iterdir() if p.name.startswith(".")]


def test_shorter_content_in_place_truncates(ps):
    m, sec, target = ps
    target.write_bytes(b"BOARD_KEY=" + b"x" * 200 + b"\n")
    _put(sec, "board.key", b"short-fake-value")
    target.parent.chmod(0o550)
    try:
        assert m.main(["--target", str(target)]) == 0
    finally:
        target.parent.chmod(0o750)
    assert target.read_bytes() == b"BOARD_KEY=short-fake-value\n"


def test_symlink_target_is_refused(ps, tmp_path, capsys):
    m, sec, target = ps
    _put(sec, "board.key", BOARD.encode())
    link = tmp_path / "link.env"
    link.symlink_to(target)
    assert m.main(["--target", str(link)]) == 3


def test_shell_wrapper_has_no_sudo_sed_or_value_on_a_command_line():
    sh = (REPO / "scripts/populate-secrets.sh").read_text()
    code = [l for l in sh.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert not any("sudo" in l or "sed " in l or "tee" in l for l in code)
    assert any("populate_secrets.py" in l and l.lstrip().startswith("exec python3") for l in code)
    py = (REPO / "scripts/lib/populate_secrets.py").read_text()
    assert "import subprocess" not in py and "os.system" not in py                  # no child process at all


def test_shell_wrapper_end_to_end_dry_run(ps):
    m, sec, target = ps
    _put(sec, "jumpseat.token", JS.encode())
    env = dict(os.environ, HOME=os.environ["HOME"])
    r = subprocess.run(["bash", str(REPO / "scripts/populate-secrets.sh"), "--dry-run", "--target", str(target)],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and "JUMPSEAT_API_KEY <" in r.stdout and JS not in r.stdout + r.stderr
