"""2026-11-01: an all-x token placeholder (ghp_xxx in a vendor doc) passes the credential
scan in both hooks; a token-shaped value, even one starting with xxx, is still rejected."""
from pathlib import Path
import subprocess
import pytest

REPO = Path(__file__).resolve().parents[2]
PREFIX = 'gh' + 'p_'  # built at runtime so this file holds no token-shaped literal


def scan(tmp_path, hook, value):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'a.md').write_text(f'  -e GITHUB_TOKEN={PREFIX}{value} \\\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'a.md'], check=True)
    p = subprocess.run(['bash', str(REPO / 'scripts' / hook)], cwd=tmp_path,
                       capture_output=True, text=True, timeout=60)
    return 'credential pattern detected' in p.stdout + p.stderr


@pytest.mark.parametrize('value', ['xxx', 'xxxxxxxx'])
def test_all_x_placeholder_is_not_a_credential(tmp_path, value):
    assert not scan(tmp_path, 'pre-commit', value)


@pytest.mark.parametrize('value', ['xxx' + 'AbCdEf0123456789abcdefABCDEF0123',
                                   'AbCdEf0123456789abcdefABCDEF012345'])
def test_token_shaped_values_are_still_rejected(tmp_path, value):
    assert scan(tmp_path, 'pre-commit', value)


def test_pre_push_carries_the_same_exemption():
    for hook in ('pre-commit', 'pre-push'):
        assert '_x{3,}([^A-Za-z0-9]|$)' in (REPO / 'scripts' / hook).read_text()
