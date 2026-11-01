"""Exercise the real per-image load gate with a clock and temporary load file."""
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def gate(tmp_path, load, remaining):
    source = (ROOT / 'scripts/stack-refresh.sh').read_text()
    function = re.search(r'^wait_image_headroom\(\) \{.*?^\}', source, re.M | re.S).group()
    bindir = tmp_path / 'bin';bindir.mkdir()
    clock = bindir / 'date';clock.write_text('#!/bin/sh\necho 1000\n');clock.chmod(0o755)
    pause = bindir / 'sleep';pause.write_text('#!/bin/sh\nexit 0\n');pause.chmod(0o755)
    loadfile = tmp_path / 'load';loadfile.write_text(f'{load} 0 0 1/1 1\n')
    env = dict(os.environ, PATH=str(bindir)+':'+os.environ['PATH'], LOADAVG_FILE=str(loadfile),
               REPO_ROOT=str(ROOT), DEADLINE=str(1000+remaining), RELAY_LOAD_POLL_S='1')
    return subprocess.run(['bash','-c',function+'\nwait_image_headroom'],env=env,
                          text=True,capture_output=True,timeout=10)


def test_build_can_enter_with_headroom(tmp_path):
    assert gate(tmp_path, 10, 2).returncode == 0


def test_sustained_load_defers_before_deadline(tmp_path):
    result = gate(tmp_path, 30, 2)
    assert result.returncode == 1
    assert 'after 2s' in result.stderr


def test_expired_refresh_cannot_start_another_image(tmp_path):
    result = gate(tmp_path, 1, -1)
    assert result.returncode == 1
    assert 'continuing' not in result.stdout
