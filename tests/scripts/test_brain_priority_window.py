"""Priority leases must restore on partial failure and never stop SWIM."""
import importlib.util
import json
from pathlib import Path
import time

import pytest


@pytest.fixture(autouse=True)
def headroom(monkeypatch):
    monkeypatch.setattr('os.getloadavg', lambda: (1.0, 1.0, 1.0))


def load():
    path = Path(__file__).resolve().parents[2] / 'scripts/brain-priority-window.py'
    spec = importlib.util.spec_from_file_location('brain_priority_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_active_compile_cannot_be_restarted(monkeypatch):
    m = load()
    monkeypatch.setattr(m, 'props', lambda name: {'LoadState':'loaded','ActiveState':'activating'})
    with pytest.raises(m.Refused, match='busy'):
        m.plan('semantic-compile-daily')


def test_core_ingest_weight_is_included_in_priority_lease(monkeypatch):
    m = load()
    def props(unit):
        return {'LoadState':'loaded','ActiveState':'active' if 'ingest-core.' in unit else 'inactive',
                'CPUWeight':'7500'}
    monkeypatch.setattr(m,'props',props)
    changes = m.plan('semantic-compile-daily')
    assert {'unit':'corporatetraveldc-ingest-core.service','before':'7500','applied':'10'} in changes


def test_other_compiler_must_finish_before_recovery(monkeypatch):
    m=load()
    def props(unit):
        return {'LoadState':'loaded','ActiveState':'activating' if 'knowledge-graph' in unit else 'inactive','CPUWeight':'100'}
    monkeypatch.setattr(m,'props',props)
    with pytest.raises(m.Refused,match='another brain job'):
        m.plan('semantic-compile-daily')


def test_agent_cannot_run_priority_changes(monkeypatch,capsys):
    m=load()
    from types import SimpleNamespace
    monkeypatch.setattr(m.argparse.ArgumentParser,'parse_args',lambda self:SimpleNamespace(action='run'))
    monkeypatch.setattr(m.pwd,'getpwuid',lambda uid:SimpleNamespace(pw_name='ctdc-agent-openai-codex'))
    monkeypatch.setattr(m.os,'getuid',lambda:1003)
    monkeypatch.setattr(m,'command',lambda *a,**kw:pytest.fail('command executed as agent'))
    assert m.main()==77


@pytest.mark.parametrize('value', ['0', '10001', '-1', 'PRIVATE', None])
def test_invalid_weights_fail_closed(value):
    m = load()
    with pytest.raises(m.Refused):
        m.weight(value)


def fake_plan(m):
    return [dict(unit='corporatetraveldc-semantic-compile-daily.service',before='100',applied='1000'),
            dict(unit='corporatetraveldc-ingest-fdps.service',before='9000',applied='10')]


def test_watchdog_must_exist_before_any_mutation(monkeypatch,tmp_path):
    m = load()
    calls=[]
    monkeypatch.setattr(m,'plan',lambda target:fake_plan(m))
    def command(args,**kw):
        calls.append(args)
        if args[0]=='systemd-run':
            raise m.Refused('watchdog failed')
        return ''
    monkeypatch.setattr(m,'command',command)
    with pytest.raises(m.Refused):
        m.prioritize('semantic-compile-daily',900,tmp_path)
    assert not any('set-property' in c or 'start' in c for c in calls)


def test_inactive_watchdog_refuses_changes(monkeypatch,tmp_path):
    m = load()
    calls=[]
    monkeypatch.setattr(m,'plan',lambda target:fake_plan(m))
    monkeypatch.setattr(m,'command',lambda args,**kw: calls.append(args) or 'inactive')
    with pytest.raises(m.Refused,match='not active'):
        m.prioritize('semantic-compile-daily',900,tmp_path)
    assert not any('set-property' in c for c in calls)


def test_partial_mutation_failure_restores_every_original_weight(monkeypatch,tmp_path):
    m = load()
    calls=[]
    monkeypatch.setattr(m,'plan',lambda target:fake_plan(m))
    def command(args,**kw):
        calls.append(args)
        if args[-1]=='CPUWeight=10':
            raise m.Refused('mutation failed')
        return 'active'
    monkeypatch.setattr(m,'command',command)
    with pytest.raises(m.Refused):
        m.prioritize('semantic-compile-daily',900,tmp_path)
    assert any(c[-1]=='CPUWeight=100' for c in calls)
    assert any(c[-1]=='CPUWeight=9000' for c in calls)
    assert json.loads((tmp_path/'weights.json').read_text())['restored']
    assert not any('stop' in c or 'restart' in c for c in calls)


def test_stale_success_rejected_and_weights_restored(monkeypatch,tmp_path):
    m=load()
    monkeypatch.setattr(m,'plan',lambda target:fake_plan(m))
    monkeypatch.setattr(m,'command',lambda args,**kw:'active')
    monkeypatch.setattr(m,'props',lambda unit:{'ActiveState':'inactive','SubState':'dead','Result':'success',
                                             'ExecMainStatus':'0','ExecMainStartTimestampMonotonic':'1'})
    with pytest.raises(m.Refused,match='fresh'):
        m.prioritize('semantic-compile-daily',900,tmp_path)
    assert json.loads((tmp_path/'weights.json').read_text())['restored']


def test_failed_job_does_not_leave_ingest_suppressed(monkeypatch,tmp_path):
    m=load()
    calls=[]
    monkeypatch.setattr(m,'plan',lambda target:fake_plan(m))
    monkeypatch.setattr(m,'props',lambda unit:{'ActiveState':'inactive'})
    def command(args,**kw):
        calls.append(args)
        if 'start' in args:
            raise m.Refused('job failed')
        return 'active'
    monkeypatch.setattr(m,'command',command)
    with pytest.raises(m.Refused):
        m.prioritize('semantic-compile-daily',900,tmp_path)
    assert json.loads((tmp_path/'weights.json').read_text())['restored']
    assert any(c[-1]=='CPUWeight=9000' for c in calls)


def test_load_rise_during_setup_restores_without_starting(monkeypatch,tmp_path):
    m=load()
    calls=[]
    monkeypatch.setattr(m,'plan',lambda target:fake_plan(m))
    monkeypatch.setattr(m,'props',lambda unit:{'ActiveState':'inactive'})
    monkeypatch.setattr(m.os,'getloadavg',lambda:(30.0,25.0,20.0))
    monkeypatch.setattr(m,'command',lambda args,**kw:calls.append(args) or 'active')
    with pytest.raises(m.Refused,match='load rose'):
        m.prioritize('semantic-compile-daily',900,tmp_path)
    assert not any('start' in c for c in calls)
    assert json.loads((tmp_path/'weights.json').read_text())['restored']


def test_completed_job_still_requires_output_acceptance(monkeypatch,tmp_path,capsys):
    m=load()
    calls=[]
    monkeypatch.setattr(m,'plan',lambda target:fake_plan(m))
    monkeypatch.setattr(m,'command',lambda args,**kw:calls.append(args) or 'active')
    def props(unit):
        return {'ActiveState':'inactive','SubState':'dead','Result':'success','ExecMainStatus':'0',
                'ExecMainStartTimestampMonotonic':str(int((time.monotonic()+1)*1_000_000))}
    monkeypatch.setattr(m,'props',props)
    m.prioritize('semantic-compile-daily',900,tmp_path)
    assert json.loads(capsys.readouterr().out)['outputs_verified'] is False
    assert json.loads((tmp_path/'weights.json').read_text())['restored']
    assert calls[0][0]=='systemd-run'
    assert not any('stop' in c or 'restart' in c for c in calls)


def test_unset_weight_restores_native_reset(monkeypatch,tmp_path):
    m=load()
    changes=fake_plan(m)
    changes[0]['before']=m.weight('[not set]')
    m.save(tmp_path/'weights.json',{'changes':changes,'restored':False})
    calls=[]
    monkeypatch.setattr(m,'command',lambda args,**kw:calls.append(args) or '')
    m.restore(tmp_path)
    assert calls[0][-1]=='CPUWeight='
    m.restore(tmp_path)
    assert len(calls)==2


def test_tampered_unit_name_is_rejected_without_commands(monkeypatch,tmp_path):
    m=load()
    changes=fake_plan(m)
    changes[0]['unit']='unrelated.service'
    m.save(tmp_path/'weights.json',{'changes':changes,'restored':False})
    calls=[]
    monkeypatch.setattr(m,'command',lambda args,**kw:calls.append(args) or '')
    with pytest.raises(m.Refused):
        m.restore(tmp_path)
    assert not calls


def test_watchdog_restoration_before_mutation_cannot_be_undone(monkeypatch,tmp_path):
    m=load()
    calls=[]
    monkeypatch.setattr(m,'plan',lambda target:fake_plan(m))
    def command(args,**kw):
        calls.append(args)
        if args[0]=='systemctl' and args[-1]=='--value':
            m.restore(tmp_path)
        return 'active'
    monkeypatch.setattr(m,'command',command)
    with pytest.raises(m.Refused,match='already restored'):
        m.prioritize('semantic-compile-daily',900,tmp_path)
    assert not any(c[-1] in ('CPUWeight=1000','CPUWeight=10') for c in calls)
