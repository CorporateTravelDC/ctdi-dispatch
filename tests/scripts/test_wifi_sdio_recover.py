"""Bounded SDIO recovery policy; all hardware, routes and probes are synthetic."""
from pathlib import Path
import importlib.util
import json
import os
from types import SimpleNamespace
import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('sdio_recover_fixture', REPO/'scripts/wifi-sdio-recover.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
C = dict(enabled=True, primary='wifi0', backup='backup0', cooldown=1800, maximum=2)


class Fake:
    def __init__(self):
        self.primary = False; self.backup = True; self.default = 'backup0'
        self.module = 'brcmfmac'; self.halted = True; self.calls = 0; self.fail = False
        self.single = True
    def driver(self, name): return self.module
    def only_primary_uses_module(self, name): return self.single
    def fault(self): return self.halted
    def route(self): return self.default
    def probe(self, name): return self.primary if name == 'wifi0' else self.backup
    def reload(self):
        self.calls += 1
        if self.fail: raise RuntimeError('synthetic failure')


def test_attempt_is_reserved_before_reload_and_success_is_not_link_confirmation():
    f=Fake(); saved=[]
    def save(s): saved.append(dict(s))
    result=m.recover(C,{},'boot',10000,f,save)
    assert f.calls == 1 and saved[0]['result'] == 'attempting'
    assert saved[-1]['attempts'] == 1 and 'not yet verified' in result


@pytest.mark.parametrize('change,reason', [
    ({'primary':True},'primary is healthy'), ({'backup':False},'healthy backup'),
    ({'default':'wifi0'},'healthy backup'), ({'module':'other'},'not brcmfmac'),
    ({'halted':False},'no recent'),
    ({'single':False},'shares the Wi-Fi module'),
])
def test_withholds_disruption_without_all_required_evidence(change,reason):
    f=Fake()
    for k,v in change.items():setattr(f,k,v)
    assert reason in m.recover(C,{},'boot',10000,f,lambda s:pytest.fail('unexpected state write'))
    assert f.calls == 0


@pytest.mark.parametrize('state,expected', [
    ({'boot_id':'boot','attempts':2,'last_attempt':1},'boot attempt limit'),
    ({'boot_id':'boot','attempts':1,'last_attempt':9900},'cooldown'),
])
def test_limits_prevent_reset_loops(state,expected):
    f=Fake()
    assert m.recover(C,state,'boot',10000,f,lambda s:None) == expected
    assert f.calls == 0


def test_new_boot_has_fresh_bounded_attempt_budget():
    f=Fake();saved=[]
    m.recover(C,dict(boot_id='old',attempts=2,last_attempt=10000),'new',10001,f,lambda s:saved.append(dict(s)))
    assert saved[0]['attempts'] == 1 and f.calls == 1


def test_backup_is_rechecked_before_module_removal():
    f=Fake(); answers=iter(['backup0','wifi0']);f.route=lambda:next(answers)
    assert 'withheld' in m.recover(C,{},'boot',10000,f,lambda s:pytest.fail('unexpected write'))
    assert f.calls == 0


def test_failed_reload_still_consumes_cooldown_and_attempt():
    f=Fake();f.fail=True;saved=[]
    with pytest.raises(RuntimeError):m.recover(C,{},'boot',10000,f,lambda s:saved.append(dict(s)))
    assert saved[-1]['result'] == 'failed' and saved[-1]['attempts'] == 1


def test_missing_or_disabled_policy_never_recovers():
    f=Fake()
    assert m.decide(dict(C,enabled=False),{},'boot',10000,f) == 'disabled'


def test_unload_failure_never_claims_a_reload(monkeypatch):
    f=m.System();calls=[]
    def run(argv,timeout=15):calls.append(argv);return SimpleNamespace(returncode=1)
    monkeypatch.setattr(f,'run',run)
    with pytest.raises(RuntimeError,match='unload failed'):f.reload()
    assert calls == [['modprobe','-r','brcmfmac']]


def test_config_checks_owner_permissions_keys_and_symlinks(tmp_path):
    p=tmp_path/'config';p.write_bytes((REPO/'config/network/wifi-sdio-recovery.conf').read_bytes());p.chmod(0o600)
    assert m.read_config(p,owner_uid=os.getuid())['maximum'] == 2
    p.chmod(0o666)
    with pytest.raises(ValueError):m.read_config(p,owner_uid=os.getuid())
    p.chmod(0o600);link=tmp_path/'link';link.symlink_to(p)
    with pytest.raises(OSError):m.read_config(link,owner_uid=os.getuid())
    p.write_text(p.read_text().replace('cooldown_seconds=1800','cooldown_seconds=1'))
    with pytest.raises(ValueError):m.read_config(p,owner_uid=os.getuid())


def test_state_receipt_is_atomic_and_has_no_unfinished_temporary(tmp_path):
    p=tmp_path/'attempt.json';m.save_state(p,{'attempts':1})
    assert json.loads(p.read_text()) == {'attempts':1}
    assert not list(tmp_path.glob(".attempt-*"))


def test_root_unit_uses_only_installed_helper_and_fixed_module_policy():
    installer=(REPO/'scripts/install-root-copies.sh').read_text()
    assert 'scripts/wifi-sdio-recover.py' in installer
    assert 'corporatetraveldc-wifi-sdio-recovery.timer' in installer
    unit=(REPO/'systemd/system/corporatetraveldc-wifi-sdio-recovery.service').read_text()
    assert 'ExecStart=/usr/local/libexec/ctdc/wifi-sdio-recover.py' in unit
    assert 'ExecStartPre=/usr/local/libexec/ctdc/installed-check.sh' in unit
    source=(REPO/'scripts/wifi-sdio-recover.py').read_text()
    assert "'-k', '-b', '0'" in source and "'--since', '-5 min'" in source
    assert "['modprobe', '-r', 'brcmfmac']" in source
