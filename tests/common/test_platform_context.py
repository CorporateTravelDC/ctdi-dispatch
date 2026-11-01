"""Shared instructions are immutable input, bound to the installed revision."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest
from common import platform_context as pc


@pytest.fixture
def context(tmp_path):
    root = tmp_path / 'repo'
    source = root / pc.SOURCE_REL
    source.parent.mkdir(parents=True)
    content = b'# Synthetic shared platform instructions\n'
    source.write_bytes(content)
    (root / 'MANIFEST.sha256').write_text(hashlib.sha256(content).hexdigest() + '  ' + pc.SOURCE_REL + '\n')
    installed = tmp_path / 'shared/PLATFORM_CONTEXT.md'
    installed.parent.mkdir()
    installed.write_bytes(content)
    installed.chmod(0o640)
    return root, source, installed, content


def read(context):
    root, _, installed, _ = context
    return pc.read_context(installed, root=root, owners={os.getuid()})


def test_all_readers_get_exact_source_bytes_and_hash(context):
    out = read(context)
    assert out['content'].encode() == context[3]
    assert out['sha256'] == hashlib.sha256(context[3]).hexdigest()
    assert out['resource'] == 'platform-context' and out['source_kind'] == 'shared_file'


def test_web_image_needs_only_the_signed_manifest_and_shared_install(context):
    root, source, installed, content = context
    source.unlink()
    out = pc.read_context(installed, root=root, owners={os.getuid()}, require_source=False)
    assert out['content'].encode() == content
    with pytest.raises(pc.ContextUnavailable):
        pc.read_context(installed, root=root, owners={os.getuid()})


@pytest.mark.parametrize('target', ['source', 'installed', 'manifest'])
def test_tampering_is_refused(context, target):
    root, source, installed, _ = context
    path = {'source': source, 'installed': installed, 'manifest': root / 'MANIFEST.sha256'}[target]
    path.write_text('modified')
    with pytest.raises(pc.ContextUnavailable):
        read(context)


def test_missing_install_is_refused(context):
    context[2].unlink()
    with pytest.raises(pc.ContextUnavailable, match='unavailable'):
        read(context)


def test_group_writable_install_is_refused(context):
    context[2].chmod(0o660)
    with pytest.raises(pc.ContextUnavailable, match='writable'):
        read(context)


def test_symlink_install_is_refused(context):
    _, source, installed, _ = context
    installed.unlink()
    installed.symlink_to(source)
    with pytest.raises(pc.ContextUnavailable):
        read(context)


def test_directory_symlink_is_refused(context):
    _, _, installed, _ = context
    real = installed.parent.with_name('actual')
    installed.parent.rename(real)
    installed.parent.symlink_to(real)
    with pytest.raises(pc.ContextUnavailable, match='directory'):
        read(context)


def test_wrong_owner_is_refused(context):
    root, _, installed, _ = context
    with pytest.raises(pc.ContextUnavailable, match='owner'):
        pc.read_context(installed, root=root, owners={-1})


def test_native_claude_hook_delivers_the_same_context(context, monkeypatch, capsys):
    script = Path(__file__).resolve().parents[2] / 'scripts/platform-context.py'
    spec = importlib.util.spec_from_file_location('context_cli', script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, 'read_context', lambda **kw: read(context))
    monkeypatch.setattr(cli.sys, 'argv', ['platform-context.py', 'hook'])
    assert cli.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out['hookSpecificOutput'] == {'hookEventName': 'SessionStart',
                                         'additionalContext': context[3].decode()}


def test_native_hook_fails_closed_without_context(context, monkeypatch, capsys):
    script = Path(__file__).resolve().parents[2] / 'scripts/platform-context.py'
    spec = importlib.util.spec_from_file_location('context_cli_missing', script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    def missing(**kwargs):
        raise pc.ContextUnavailable('shared context file unavailable')
    monkeypatch.setattr(cli, 'read_context', missing)
    monkeypatch.setattr(cli.sys, 'argv', ['platform-context.py', 'hook'])
    assert cli.main() == 2
    assert not capsys.readouterr().out


def test_instruction_file_retired_and_weekly_job_cannot_reset_shared_source():
    root = Path(__file__).resolve().parents[2]
    assert not (root / 'CLAUDE.md').exists()
    assert '/srv/ctdc-context/PLATFORM_CONTEXT.md' in (root / 'AGENTS.md').read_text()
    hook = json.loads((root / '.claude/settings.json').read_text())['hooks']['SessionStart'][0]['hooks'][0]
    assert 'platform-context.py' in hook['command'] and hook['command'].endswith(' hook')
    dump = (root / 'scripts/second-brain-weekly-dump.sh').read_text()
    assert 'Never reset, rewrite or create repo instruction files' in dump
    assert 'reset CLAUDE.md' not in dump and 'python3 -c' not in dump
    assert 'ONLY after that confirmed write' in dump
