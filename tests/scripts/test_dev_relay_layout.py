"""Non-privileged, no-commit fixtures for the development relay addition."""
from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[2]
LAYOUT = ROOT / 'scripts/agent-segmentation/srv-layout.sh'
ACCESS = ROOT / 'scripts/agent-segmentation/srv-access.sh'


def fixture(tmp_path):
    srv = tmp_path / 'srv'
    srv.mkdir()
    registry = tmp_path / 'accounts'
    registry.write_text('')
    env = dict(os.environ, SRV_ROOT=str(srv), CTDC_REGISTRY=str(registry))
    return srv, registry, env


def run(env, *args):
    return subprocess.run(['bash', str(LAYOUT), *args], env=env,
                          text=True, capture_output=True, timeout=10)


def test_plan_has_operator_checkout_and_sticky_submission_permissions(tmp_path):
    srv, _, env = fixture(tmp_path)
    r = run(env)
    assert r.returncode == 0, r.stderr
    for suffix, owner, mode in [('ctdc-dev', 'root', '2750'),
                              ('ctdc-dev/submissions', 'root', '3770'),
                              ('ctdc-dev/relays', 'root', '3770'),
                              ('ctdc-dev/checkouts', 'corporatetraveldc', '2750')]:
        assert f'install -d -m {mode} -o {owner} -g ctdc-dev {srv / suffix}' in r.stdout
    assert '(dry run: nothing changed)' in r.stdout
    assert not list(srv.iterdir())


def test_refuses_symlink_root_before_other_layout_actions(tmp_path):
    srv, _, env = fixture(tmp_path)
    alternate = tmp_path / 'elsewhere'
    alternate.mkdir()
    (srv / 'ctdc-dev').symlink_to(alternate, target_is_directory=True)
    r = run(env)
    assert r.returncode != 0
    assert 'unsafe development path' in r.stderr
    assert 'install -d' not in r.stdout
    assert not list(alternate.iterdir())


def test_refuses_symlink_srv_parent(tmp_path):
    srv, _, env = fixture(tmp_path)
    link = tmp_path / 'srv-link'
    link.symlink_to(srv, target_is_directory=True)
    r = run(dict(env, SRV_ROOT=str(link)))
    assert r.returncode != 0
    assert 'unsafe SRV root' in r.stderr
    assert not list(srv.iterdir())


def test_refuses_file_collision_before_any_layout_action(tmp_path):
    srv, _, env = fixture(tmp_path)
    (srv / 'ctdc-dev').write_text('retain me')
    r = run(env)
    assert r.returncode != 0
    assert 'unsafe development path' in r.stderr
    assert 'install -d' not in r.stdout
    assert (srv / 'ctdc-dev').read_text() == 'retain me'


def test_refuses_unexpected_owner_without_reassigning_existing_folder(tmp_path):
    srv, _, env = fixture(tmp_path)
    owned = srv / 'ctdc-dev'
    owned.mkdir()
    # This fixture deliberately runs under an unprivileged agent account.
    assert os.getuid() != 0
    original = owned.stat().st_uid
    r = run(env)
    assert r.returncode != 0
    assert 'unexpected owner' in r.stderr
    assert 'install -d' not in r.stdout
    assert owned.stat().st_uid == original


def test_reserved_dev_account_cannot_get_shared_folder_as_private(tmp_path):
    srv, registry, env = fixture(tmp_path)
    registry.write_text('ctdc-agent-dev agent\n')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    getent = bin_dir / 'getent'
    getent.write_text('#!/bin/sh\nprintf "%s\\n" "ctdc-dev:x:1002:ctdc-agent-dev"\n')
    getent.chmod(0o700)
    r = run(dict(env, PATH=f'{bin_dir}:{env["PATH"]}'))
    assert r.returncode != 0
    assert "folder name 'dev' is reserved" in r.stderr
    assert f'-o ctdc-agent-dev' not in r.stdout
    assert not list(srv.iterdir())


def test_private_acl_tool_refuses_development_tree_before_any_acl_mutation(tmp_path):
    srv, registry, env = fixture(tmp_path)
    registry.write_text('ctdc-agent-test agent\n')
    log = tmp_path / 'access.log'
    r = subprocess.run(['bash', str(ACCESS), 'grant', 'ctdc-agent-test',
                        'ctdc-dev', '--operator', 'isolated fixture'],
                       env=dict(env, SRV_ACCESS_LOG=str(log)),
                       text=True, capture_output=True, timeout=10)
    assert r.returncode != 0
    assert 'private-folder ACL grants are refused' in r.stderr
    assert not log.exists()
    assert not list(srv.iterdir())


def test_refuses_child_symlink_before_mutation(tmp_path):
    srv, _, env = fixture(tmp_path)
    (srv / 'ctdc-dev').mkdir()
    target = tmp_path / 'target'
    target.mkdir()
    (srv / 'ctdc-dev/submissions').symlink_to(target, target_is_directory=True)
    # Fixture ownership metadata only: no account switching or privileged writes.
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    stat = bin_dir / 'stat'
    stat.write_text('#!/bin/sh\nprintf "%s\\n" root\n')
    stat.chmod(0o700)
    r = run(dict(env, PATH=f'{bin_dir}:{env["PATH"]}'))
    assert r.returncode != 0
    assert 'unsafe development path' in r.stderr
    assert 'install -d' not in r.stdout
    assert not list(target.iterdir())
