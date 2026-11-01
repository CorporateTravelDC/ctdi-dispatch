"""Runtime proof plumbing uses native Quadlet merging and rejects stale invocations."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from common.brain_recovery import ProofFailed


def load():
    path = Path(__file__).resolve().parents[2] / 'scripts/brain-recovery-proof.py'
    spec = importlib.util.spec_from_file_location('brain_runtime_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_quadlet_uses_validation_image_dropin(tmp_path):
    generator = Path('/usr/libexec/podman/quadlet')
    if not generator.exists():
        pytest.skip('native Quadlet generator unavailable')
    src = tmp_path / 'units'
    src.mkdir()
    (src / 'fixture.container').write_text('[Container]\nImage=localhost/fixture:old\n')
    drop = src / 'fixture.container.d'
    drop.mkdir()
    (drop / '90-brain-validation.conf').write_text('[Container]\nImage=localhost/fixture:reviewed\n')
    env = dict(os.environ, QUADLET_UNIT_DIRS=str(src))
    out = subprocess.run([str(generator), '-user', '-dryrun', '-no-kmsg-log'],
                         env=env, capture_output=True, text=True)
    assert out.returncode == 0, 'native Quadlet rejected the synthetic fixture'
    commands = '\n'.join(line for line in out.stdout.splitlines() if line.startswith('ExecStart='))
    assert 'localhost/fixture:reviewed' in commands
    assert 'localhost/fixture:old' not in commands


def test_runtime_does_not_accept_old_success_as_a_new_job(monkeypatch):
    module = load()
    monkeypatch.setattr(module, 'unit', lambda name: {'ActiveState': 'inactive',
                         'ExecMainStartTimestampMonotonic': '1', 'Result': 'success', 'ExecMainStatus': '0'})
    def fake(args, **kw):
        if 'ExecStart' in args:
            return 'podman run localhost/fixture:reviewed'
        if args[0] == 'podman':
            return 'fixture-image-id'
        return ''
    monkeypatch.setattr(module, 'command', fake)
    with pytest.raises(ProofFailed, match='new invocation'):
        module.run_unit(module.SEM, 'localhost/fixture:reviewed', 'fixture-image-id')


def test_graph_trigger_follows_committed_semantic_sentinel():
    root = Path(__file__).resolve().parents[2]
    path = (root / '.config/systemd/user/corporatetraveldc-knowledge-graph-compile.path').read_text()
    assert 'PathModified=/var/lib/corporatetraveldc/second-brain-triggers/semantic-compiled' in path
    assert 'PathModified=/var/lib/corporatetraveldc/second-brain-triggers/manual-note-written' not in path
    unit = (root / '.config/containers/systemd/corporatetraveldc-knowledge-graph-compile.container').read_text()
    assert 'After=corporatetraveldc-semantic-compile-daily.service' in unit
