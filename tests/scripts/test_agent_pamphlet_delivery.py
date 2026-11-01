"""Local delivery filesystem/receipt policy, isolated from real homes and services."""
from pathlib import Path
import importlib.util
import json
import os
from types import SimpleNamespace
import subprocess
import pytest

REPO=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('pamphlet_delivery_fixture',REPO/'scripts/agent-pamphlet-install.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def user(home):
    return SimpleNamespace(pw_name='ctdc-agent-fixture',pw_uid=os.getuid(),pw_gid=os.getgid(),
                           pw_dir=str(home),pw_shell='/bin/sh')


def test_eligible_accounts_include_future_vendor_and_skip_cloud_preloaded_humans(tmp_path):
    text='''ctdc-agent-vendor-new agent ssh 0
ctdc-agent-fixture-codex agent codex 0
ctdc-agent-fixture-cloud service none 0
ctdc-agent-fixture-future agent ssh 1
human-fixture human ssh 0
'''
    rows=m.local_accounts(text,lambda name:user(tmp_path))
    assert [r[:2] for r in rows] == [('ctdc-agent-vendor-new','ssh'),('ctdc-agent-fixture-codex','codex')]


def test_future_local_service_uses_explicit_location_policy_even_with_nologin(tmp_path):
    account='ctdc-agent-future-local'
    u=user(tmp_path);u.pw_shell='/usr/sbin/nologin'
    registry=account+' service none 0\nctdc-agent-future-cloud service none 0\n'
    policy=m.declared_local_services(account+' # explicitly deployed here\n')
    assert [row[0] for row in m.local_accounts(registry,lambda name:u,policy)]==[account]
    assert not m.local_accounts(registry,lambda name:u)
    assert not m.local_accounts(account+' service none 1\n',lambda name:u,policy)


def test_service_policy_does_not_invent_accounts_or_grants(tmp_path):
    with pytest.raises(ValueError):m.declared_local_services('ctdc-agent-fixture\nctdc-agent-fixture')
    with pytest.raises(ValueError):m.local_accounts('',lambda name:user(tmp_path),{'ctdc-agent-missing'})
    with pytest.raises(ValueError):m.local_accounts('ctdc-agent-fixture agent ssh 0',lambda name:user(tmp_path),{'ctdc-agent-fixture'})


@pytest.mark.parametrize('text',[
    'ctdc-agent-fixture agent codex 0\nctdc-agent-fixture agent codex 0',
    'ctdc-agent-fixture agent codex', 'ctdc-agent-fixture agent codex invalid',
])
def test_bad_registry_fails_closed(text,tmp_path):
    with pytest.raises(ValueError):m.local_accounts(text,lambda name:user(tmp_path))


def test_directory_walk_refuses_symlink_ancestors(tmp_path):
    actual=tmp_path/'actual';actual.mkdir();(actual/'home').mkdir()
    link=tmp_path/'alias';link.symlink_to(actual,target_is_directory=True)
    with pytest.raises(OSError):m.open_directory(link/'home',os.getuid())


def test_native_targets_do_not_create_or_replace_client_config(tmp_path):
    u=user(tmp_path);opened=m.targets(u)
    try:assert [names for _,names in opened] == [['PAMPHLET.md','CLAUDE.md','AGENTS.md']]
    finally:
        for fd,_ in opened:os.close(fd)
    assert not (tmp_path/'.codex').exists()
    (tmp_path/'.codex').mkdir();opened=m.targets(u)
    try:assert opened[-1][1] == ['AGENTS.md']
    finally:
        for fd,_ in opened:os.close(fd)


def test_symlinked_client_directory_is_not_followed(tmp_path):
    outside=tmp_path/'outside';outside.mkdir();(tmp_path/'.codex').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError):m.targets(user(tmp_path))
    assert list(outside.iterdir()) == []


def test_atomic_pamphlet_readback_and_refusal_of_unrelated_files(tmp_path):
    fd=m.open_directory(tmp_path,os.getuid());data=b'# ctdc-agent-fixture -- onboarding pamphlet\n'
    try:
        m.atomic_markdown(fd,'PAMPHLET.md',data,os.getgid(),'ctdc-agent-fixture',owner_uid=os.getuid())
        assert (tmp_path/'PAMPHLET.md').read_bytes() == data
        assert (tmp_path/'PAMPHLET.md').stat().st_mode & 0o777 == 0o444
        (tmp_path/'AGENTS.md').write_text('unrelated operator instructions');(tmp_path/'AGENTS.md').chmod(0o444)
        with pytest.raises(ValueError,match='unrelated'):
            m.atomic_markdown(fd,'AGENTS.md',data,os.getgid(),'ctdc-agent-fixture',owner_uid=os.getuid())
        assert (tmp_path/'AGENTS.md').read_text() == 'unrelated operator instructions'
    finally:os.close(fd)


def test_target_symlink_never_overwrites_outside_file(tmp_path):
    outside=tmp_path/'outside';outside.write_text('preserve')
    (tmp_path/'AGENTS.md').symlink_to(outside)
    fd=m.open_directory(tmp_path,os.getuid())
    try:
        with pytest.raises(OSError):m.atomic_markdown(fd,'AGENTS.md',b'x',os.getgid(),'ctdc-agent-fixture',owner_uid=os.getuid())
    finally:os.close(fd)
    assert outside.read_text() == 'preserve'


def test_existing_request_marker_is_read_only_to_avoid_a_service_trigger_loop(tmp_path,monkeypatch):
    fd=m.open_directory(tmp_path,os.getuid())
    try:
        m.ensure_request_marker(fd,os.getgid(),owner_uid=os.getuid())
        marker=tmp_path/'refresh-request';before=marker.stat().st_mtime_ns
        real_open=m.os.open;flags=[]
        def record(path,fl,*a,**kw):
            if path=='refresh-request':flags.append(fl)
            return real_open(path,fl,*a,**kw)
        monkeypatch.setattr(m.os,'open',record)
        m.ensure_request_marker(fd,os.getgid(),owner_uid=os.getuid())
        assert flags and all(fl & os.O_ACCMODE == os.O_RDONLY for fl in flags)
        assert marker.stat().st_mtime_ns==before
    finally:os.close(fd)


def test_request_marker_cannot_be_redirected(tmp_path):
    outside=tmp_path/'outside';outside.write_text('preserve')
    (tmp_path/'refresh-request').symlink_to(outside)
    fd=m.open_directory(tmp_path,os.getuid())
    try:
        with pytest.raises(OSError):m.ensure_request_marker(fd,os.getgid(),owner_uid=os.getuid())
    finally:os.close(fd)
    assert outside.read_text()=='preserve'


@pytest.fixture
def delivery(tmp_path,monkeypatch):
    root=tmp_path/'mirrors';root.mkdir();home=tmp_path/'home';home.mkdir()
    u=user(home);op=user(tmp_path);calls=[]
    monkeypatch.setattr(m,'ROOT',root)
    original_open=m.open_directory;original_check=m.check_target;original_atomic=m.atomic_markdown
    original_matches=m.output_matches;original_text=m.trusted_text
    monkeypatch.setattr(m,'open_directory',lambda p,uid:original_open(p,os.getuid()))
    monkeypatch.setattr(m,'check_target',lambda fd,name,account,owner_uid=0:original_check(fd,name,account,os.getuid()))
    monkeypatch.setattr(m,'atomic_markdown',lambda fd,name,data,gid,account:original_atomic(fd,name,data,gid,account,os.getuid()))
    monkeypatch.setattr(m,'output_matches',lambda fd,name,digest:original_matches(fd,name,digest,os.getuid()))
    monkeypatch.setattr(m,'trusted_text',lambda p:original_text(p,os.getuid()))
    monkeypatch.setattr(m.os,'chown',lambda *a:None)
    monkeypatch.setattr(m.os,'fchown',lambda *a:None)
    def render(account,operator):
        calls.append(account)
        return ('# '+account+' -- onboarding pamphlet\nfixture generation '+str(len(calls))+'\n').encode()
    monkeypatch.setattr(m,'render',render)
    return root,home,u,op,calls


def test_delivery_updates_native_and_operator_readable_mirror_idempotently(delivery):
    root,home,u,op,calls=delivery;inputs={'manifest_sha256':'fixture-one'}
    m.deliver(u.pw_name,'codex',u,op,inputs)
    doc=(home/'PAMPHLET.md').read_bytes();first=(home/'PAMPHLET.md').stat().st_mtime_ns
    assert all((home/n).read_bytes()==doc for n in ['AGENTS.md','CLAUDE.md'])
    assert (root/u.pw_name/'PAMPHLET.md').read_bytes()==doc
    m.deliver(u.pw_name,'codex',u,op,inputs)
    assert len(calls)==1 and (home/'PAMPHLET.md').stat().st_mtime_ns==first
    receipt=json.loads((root/u.pw_name/'receipt.json').read_text())
    assert receipt['checked_at']>=receipt['delivered_at'] and receipt['inputs']['home']==str(home)


def test_new_signed_source_and_new_client_directory_are_delivered(delivery):
    root,home,u,op,calls=delivery
    m.deliver(u.pw_name,'codex',u,op,{'manifest_sha256':'one'})
    (home/'.codex').mkdir()
    m.deliver(u.pw_name,'codex',u,op,{'manifest_sha256':'one'})
    assert (home/'.codex/AGENTS.md').read_bytes()==(home/'PAMPHLET.md').read_bytes()
    m.deliver(u.pw_name,'codex',u,op,{'manifest_sha256':'two'})
    assert len(calls)==3


def test_removed_home_copy_is_repaired(delivery):
    root,home,u,op,calls=delivery
    m.deliver(u.pw_name,'ssh',u,op,{'manifest_sha256':'one'})
    (home/'AGENTS.md').unlink()
    m.deliver(u.pw_name,'ssh',u,op,{'manifest_sha256':'one'})
    assert len(calls)==2 and (home/'AGENTS.md').exists()


def test_unmanaged_native_file_prevents_partial_replacement(delivery):
    root,home,u,op,calls=delivery
    (home/'AGENTS.md').write_text('user notes');(home/'AGENTS.md').chmod(0o444)
    with pytest.raises(ValueError):m.deliver(u.pw_name,'ssh',u,op,{'manifest_sha256':'one'})
    assert not (home/'CLAUDE.md').exists() and not calls


def test_periodic_path_and_deploy_hooks_are_wired_to_signed_root_copy():
    installer=(REPO/'scripts/install-root-copies.sh').read_text()
    for name in ['agent-pamphlet-install.py','corporatetraveldc-agent-pamphlet-install.path',
                 'corporatetraveldc-agent-pamphlet-install.timer']:
        assert name in installer
    path=(REPO/'systemd/system/corporatetraveldc-agent-pamphlet-install.path').read_text()
    unit=(REPO/'systemd/system/corporatetraveldc-agent-pamphlet-install.service').read_text()
    assert 'ExecStart=/usr/local/libexec/ctdc/agent-pamphlet-install.py' in unit
    assert 'ExecStartPre=/usr/local/libexec/ctdc/installed-check.sh' in unit
    assert 'MANIFEST.sha256.asc' in path and '/etc/ctdc-accounts.conf' in path
    assert '/var/lib/ctdc-agent-pamphlets/refresh-request' in path
    rollout=(REPO/'scripts/serialized-rollout.sh').read_text()
    assert 'if ! "$REPO/scripts/agent-pamphlet-refresh.sh"' in rollout
    weekly=(REPO/'.config/systemd/user/corporatetraveldc-agent-pamphlet-weekly-refresh.service').read_text()
    assert 'scripts/agent-pamphlet-refresh.sh' in weekly


@pytest.mark.parametrize('failed', ['verify','vault','local','none'])
def test_deployment_delivery_does_not_claim_partial_success(tmp_path,failed):
    scripts=tmp_path/'scripts';scripts.mkdir();calls=tmp_path/'calls'
    source=(REPO/'scripts/agent-pamphlet-refresh.sh').read_text()
    (scripts/'agent-pamphlet-refresh.sh').write_text(source)
    (scripts/'verify-manifest.sh').write_text('#!/bin/sh\necho verify >>"$CALLS"\n[ "$FAIL_STEP" != verify ]\n')
    (scripts/'agent-pamphlet-weekly-refresh.sh').write_text('#!/bin/sh\necho vault >>"$CALLS"\n[ "$FAIL_STEP" != vault ]\n')
    (scripts/'agent-pamphlet-request.py').write_text('import os\nwith open(os.environ["CALLS"],"a") as f:f.write("request\\n")\n')
    (scripts/'agent-pamphlet-install.py').write_text('import os,sys\nsys.exit(1 if os.environ["FAIL_STEP"]=="local" else 0)\n')
    bindir=tmp_path/'bin';bindir.mkdir();(bindir/'sleep').write_text('#!/bin/sh\nexit 0\n');(bindir/'sleep').chmod(0o755)
    env=dict(os.environ,CALLS=str(calls),FAIL_STEP=failed,PATH=str(bindir)+os.pathsep+os.environ['PATH'])
    result=subprocess.run(['bash',str(scripts/'agent-pamphlet-refresh.sh')],env=env,capture_output=True,text=True,timeout=30)
    assert (result.returncode==0)==(failed=='none')
    if failed=='verify':assert calls.read_text().splitlines()==['verify']
    if failed!='none':assert 'receipts verified' not in result.stdout
