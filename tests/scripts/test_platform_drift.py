"""Run the real neutral checker against isolated units and files, never live services."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checker(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'scripts').mkdir(parents=True)
    (repo / 'config').mkdir()
    for name in ['check-platform-drift.sh', 'check-claude-md-drift.sh']:
        (repo / 'scripts' / name).write_bytes((ROOT / 'scripts' / name).read_bytes())
    binary = tmp_path / 'bin'
    binary.mkdir()
    def executable(path, text):
        path.write_text('#!/bin/bash\n' + text + '\n')
        path.chmod(0o755)
    executable(repo / 'scripts/verify-manifest.sh', 'exit 1')
    executable(binary / 'systemctl', 'if [[ "$1" == --user ]]; then printf "%s" "${FIXTURE_FAILED_UNITS:-}"; fi')
    executable(binary / 'journalctl', 'exit 0')
    executable(binary / 'curl', 'printf 200')
    executable(binary / 'git', 'exit 0')
    # Refuse production configuration reads even in the checker subprocess.
    executable(binary / 'grep', 'for arg in "$@"; do [[ "$arg" == /etc/* ]] && exit 1; done\nexec /usr/bin/grep "$@"')
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'], TZ='UTC')
    date = datetime.now(timezone.utc).date().isoformat()
    doc = repo / 'config/platform-context.md'
    doc.write_text('# Shared context\n\n## Known bad (as of ' + date + ')\n')
    def run(failed='', legacy=False):
        return subprocess.run(['bash', 'scripts/check-claude-md-drift.sh' if legacy else 'scripts/check-platform-drift.sh', '--pre-sign'],
                              cwd=repo, env=dict(env, FIXTURE_FAILED_UNITS=failed),
                              capture_output=True, text=True)
    return repo, doc, run


def test_missing_shared_source_fails_closed(checker):
    _, doc, run = checker
    doc.unlink()
    out = run()
    assert out.returncode == 1 and '[FAIL]' in out.stdout


def test_failed_job_must_be_accounted_for_in_shared_context(checker):
    _, doc, run = checker
    failed = 'corporatetraveldc-fixture.service loaded failed failed\n'
    assert run(failed).returncode == 1
    doc.write_text(doc.read_text() + '\n- corporatetraveldc-fixture: synthetic observed failure.\n')
    assert run(failed).returncode == 0
    assert run(failed, legacy=True).returncode == 0


def test_stale_known_bad_section_still_fails(checker):
    _, doc, run = checker
    old = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    doc.write_text('# Shared context\n## Known bad (as of ' + old + ')\n')
    out = run()
    assert out.returncode == 1 and 'days old' in out.stdout


def test_hook_twin_mismatch_still_fails(checker):
    repo, _, run = checker
    (repo / 'scripts/pre-commit').write_text('reviewed source')
    hooks = repo / '.git/hooks'
    hooks.mkdir(parents=True)
    (hooks / 'pre-commit').write_text('different installed source')
    out = run()
    assert out.returncode == 1 and 'does not match scripts/pre-commit' in out.stdout


def test_signing_gate_cannot_skip_a_missing_or_bypassed_checker():
    text = (ROOT / 'scripts/sign-manifest.sh').read_text()
    assert 'Drift bypass requested -- signing BLOCKED' in text
    assert 'missing or not executable -- signing BLOCKED' in text
    assert 'continuing without it' not in text
