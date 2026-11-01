"""Agent observations distinguish completed work, skips and missing schedules."""
import importlib.util
import json
from pathlib import Path
import subprocess
import io


def load():
    path = Path(__file__).resolve().parents[2] / 'scripts/agent-status-snapshot.py'
    spec = importlib.util.spec_from_file_location('snapshot_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_completed_and_skipped_oneshots_remain_distinguishable(monkeypatch):
    m = load()
    names = ['corporatetraveldc-second-brain-daily.service',
             'corporatetraveldc-second-brain-weekly.service']
    calls = []
    def run(args):
        calls.append(args)
        if 'list-units' in args:
            return json.dumps([dict(unit=n, active='inactive', sub='dead') for n in names])
        return ('Id=' + names[0] + '\nResult=success\nConditionResult=yes\nExecMainStatus=0\n'
                'ExecMainStartTimestamp=Thu 2026-10-29 01:00:00 UTC\n'
                'ExecStart=PRIVATE_ARGUMENTS\nEnvironment=PRIVATE_VALUES\n\n'
                'Id=' + names[1] + '\nResult=success\nConditionResult=no\nExecMainStatus=0\n'
                'ExecMainStartTimestamp=\n')
    monkeypatch.setattr(m, '_run', run)
    rows = m.units()
    assert rows[0]['ConditionResult'] == 'yes'
    assert rows[0]['ExecMainStartTimestamp']
    assert rows[1]['ConditionResult'] == 'no'
    assert not rows[1]['ExecMainStartTimestamp']
    assert 'PRIVATE' not in json.dumps(rows)
    assert len(calls) == 2


def test_missing_details_are_unavailable_not_success(monkeypatch):
    m = load()
    def run(args):
        if 'list-units' in args:
            return json.dumps([dict(unit='corporatetraveldc-second-brain-daily.service', active='inactive')])
        raise subprocess.CalledProcessError(1, 'systemctl')
    monkeypatch.setattr(m, '_run', run)
    row = m.units()[0]
    assert row['detail'] == 'unavailable'
    assert 'Result' not in row


def test_never_loaded_masked_and_disabled_triggers_are_visible(monkeypatch):
    m = load()
    monkeypatch.setattr(m, '_run', lambda args: json.dumps([
        dict(unit_file='corporatetraveldc-knowledge-graph-compile.timer', state='disabled', private='PRIVATE'),
        dict(unit_file='corporatetraveldc-knowledge-graph-compile.path', state='masked'),
        dict(unit_file='unrelated.service', state='enabled'),
    ]))
    out = m.unit_files()
    assert len(out) == 2
    assert {r['state'] for r in out} == {'disabled', 'masked'}
    assert 'PRIVATE' not in json.dumps(out)


def test_paths_exclude_descriptions_and_other_fields(monkeypatch):
    m = load()
    monkeypatch.setattr(m, '_run', lambda args: json.dumps([
        dict(unit='corporatetraveldc-knowledge-graph-compile.path', load='loaded', active='failed',
             sub='failed', description='PRIVATE', ExecStart='PRIVATE')]))
    out = m.paths()
    assert out[0]['active'] == 'failed'
    assert 'PRIVATE' not in json.dumps(out)


def test_failed_timer_without_calendar_row_is_visible(monkeypatch):
    m=load()
    name='corporatetraveldc-knowledge-graph-compile.timer'
    def run(args):
        if 'list-timers' in args:
            return '[]'
        return json.dumps([dict(unit=name,load='loaded',active='failed',sub='failed',description='PRIVATE')])
    monkeypatch.setattr(m,'_run',run)
    out=m.timers()
    assert out[0]['unit']==name
    assert out[0]['active']=='failed'
    assert 'PRIVATE' not in json.dumps(out)


def test_missing_inventory_is_an_explicit_snapshot_gap(monkeypatch):
    m = load()
    def unavailable():
        raise OSError('PRIVATE')
    monkeypatch.setattr(m, 'COLLECTORS', {'unit_files':unavailable, 'paths': lambda: []})
    out = m.snapshot()
    assert out['unavailable'] == ['unit_files']
    assert out['paths'] == []
    assert 'PRIVATE' not in json.dumps(out)


def test_heavy_pool_summary_copies_only_safe_counts_and_names(monkeypatch):
    m=load();name='corporatetraveldc-semantic-compile-daily.service'
    raw=json.dumps({'leases':{name:{'budget':1024**3,'reserve':1024**3,'fault':False,
                       'invocation':'PRIVATE','report':{'arbitrary':'PRIVATE'}}},
                    'queue':{'corporatetraveldc-second-brain-rss.service':{'private':'PRIVATE'},'PRIVATE':{}},
                    'manager':{'private':'PRIVATE'}})
    monkeypatch.setattr('builtins.open',lambda *args,**kw:io.StringIO(raw))
    out=m.heavy_jobs()
    assert out['max_parallel']==2 and len(out['active'])==1 and len(out['queued'])==1
    assert 'PRIVATE' not in json.dumps(out)
