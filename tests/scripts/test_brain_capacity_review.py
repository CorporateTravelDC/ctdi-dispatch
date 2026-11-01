"""Elapsed-day scheduling, failed-review retries and bounded observations."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest


def load():
    path = Path(__file__).resolve().parents[2]/'scripts/brain-capacity-review.py'
    spec = importlib.util.spec_from_file_location('capacity_fixture', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


NOW = datetime(2026, 10, 31, 11, tzinfo=timezone.utc)


def healthy(m):
    return {'unit':{'ActiveState':'inactive', 'SubState':'dead', 'Result':'success',
                    'ExecMainStatus':'0', 'MemoryPeak':str(1742340096),
                    'MemoryHigh':str(m.SOFT), 'MemoryMax':str(m.HARD),
                    'EffectiveMemoryMax':str(m.HARD), 'MemorySwapMax':'0',
                    'ExecMainStartTimestampMonotonic':'123'},
            'swap_guard':{'ok':True,'same_limit_update_verified':True,'initial_swap_bytes':0,
                          'finished_at':NOW.isoformat(),'invocation_start_monotonic':'123',
                          'final_swap_allowance_bytes':0,'initial_grant_bytes':1024**3,
                          'step_bytes':m.SWAP_STEP,'max_swap_bytes':m.MAX_SWAP},
            'semantic':{'compile_seq':48, 'compiled_at':NOW.isoformat(),
                        **{k:100 for k in m.COUNTS}},
            'ledger':{'ok':True, 'compile_seq':48}, 'sentinel_seq':48}


def test_15_elapsed_days_catches_up_across_reboot():
    m = load()
    previous = {'checked_at':NOW.isoformat()}
    assert not m.due(previous, NOW+timedelta(days=15)-timedelta(seconds=1))
    assert m.due(previous, NOW+timedelta(days=15))
    assert m.due(previous, NOW+timedelta(days=18))
    assert m.due(previous, NOW-timedelta(days=2))
    assert m.due(None, NOW)


def test_not_due_skips_database_and_unit_queries(monkeypatch, tmp_path):
    m = load()
    (tmp_path/'last-healthy.json').write_text(json.dumps({'checked_at':NOW.isoformat()}))
    monkeypatch.setattr(m, 'observations', lambda:pytest.fail('unnecessary live query'))
    assert m.review(tmp_path, NOW+timedelta(days=14)) == 0


def test_healthy_baseline_and_growth_are_saved(monkeypatch, tmp_path):
    m = load()
    old = {'checked_at':(NOW-timedelta(days=16)).isoformat(),
           'semantic':{k:70 for k in m.COUNTS}}
    (tmp_path/'last-healthy.json').write_text(json.dumps(old))
    monkeypatch.setattr(m, 'observations', lambda:healthy(m))
    assert m.review(tmp_path, NOW) == 0
    data = json.loads((tmp_path/'last-healthy.json').read_text())
    assert data['growth_since_last_healthy_review'] == {k:30 for k in m.COUNTS}
    assert data['policy']['hard_bytes'] == 2684354560
    assert 'not a 15-day maximum' in data['memory_peak_scope']


@pytest.mark.parametrize('field,value,issue', [
    ('MemoryPeak','[not set]','latest_invocation_peak_unavailable'),
    ('MemoryMax',str(1536*1024**2),'configured_service_limits_differ_from_policy'),
    ('MemoryHigh','infinity','configured_service_limits_differ_from_policy'),
    ('MemorySwapMax','infinity','service_swap_limit_differs_from_policy'),
    ('EffectiveMemoryMax',str(2*1024**3),'effective_parent_ceiling_below_policy_or_unavailable'),
    ('Result','oom-kill','latest_invocation_unsuccessful'),
    ('ActiveState','activating','compiler_busy_retry_after_completion'),
])
def test_pressure_or_policy_drift_alerts_without_advancing_due_date(monkeypatch,tmp_path,field,value,issue):
    m = load()
    old = {'checked_at':(NOW-timedelta(days=16)).isoformat()}
    (tmp_path/'last-healthy.json').write_text(json.dumps(old))
    data = healthy(m); data['unit'][field] = value
    monkeypatch.setattr(m, 'observations', lambda:data)
    assert m.review(tmp_path, NOW) == 1
    assert issue in json.loads((tmp_path/'latest.json').read_text())['issues']
    assert json.loads((tmp_path/'last-healthy.json').read_text()) == old
    assert m.due(old, NOW+timedelta(days=1))


def test_stale_or_inconsistent_compile_not_accepted():
    m = load(); data = healthy(m)
    data['sentinel_seq'] = 47
    data['semantic']['compiled_at'] = (NOW-timedelta(hours=13)).isoformat()
    result = m.assess(data, NOW, None)
    assert 'committed_revision_inconsistent' in result['issues']
    assert 'semantic_compile_stale_or_clock_skewed' in result['issues']


def test_initial_gigabyte_is_permitted_but_four_gigabytes_prompts_capacity_review():
    m=load();data=healthy(m)
    data['swap_guard']['final_swap_allowance_bytes']=1024**3
    data['unit']['MemorySwapMax']=str(1024**3)
    assert m.assess(data,NOW,None)['ok']
    data['swap_guard']['final_swap_allowance_bytes']=m.MAX_SWAP
    data['unit']['MemorySwapMax']=str(m.MAX_SWAP)
    assert 'maximum_swap_allowance_reached_review_capacity' in m.assess(data,NOW,None)['issues']


def test_expected_soft_pressure_and_swap_are_warnings_in_a_healthy_review():
    m=load();data=healthy(m)
    data['unit']['MemoryPeak']=str(m.SOFT+1)
    data['swap_guard']['final_swap_allowance_bytes']=1024**3
    data['unit']['MemorySwapMax']=str(1024**3)
    result=m.assess(data,NOW,None)
    assert result['ok']
    assert 'latest_invocation_reached_soft_threshold' in result['warnings']
    assert 'swap_allowance_was_needed' in result['warnings']


def test_stale_swap_receipt_cannot_prove_latest_guard():
    m=load();data=healthy(m)
    data['swap_guard']['invocation_start_monotonic']='122'
    assert 'fresh_successful_swap_guard_receipt_unavailable' in m.assess(data,NOW,None)['issues']


def test_pool_review_flags_excess_concurrency_host_usage_and_failed_members():
    m=load();data=healthy(m)
    data['heavy_pool']={'active_jobs':['a','b','c'],'host_swap_total_bytes':16*1024**3,
                        'host_swap_used_bytes':10*1024**3,'unit_outcomes':{
                        'corporatetraveldc-knowledge-graph-compile.service':{'ActiveState':'failed'}}}
    result=m.assess(data,NOW,None)
    assert 'heavy_pool_concurrency_exceeds_two' in result['issues']
    assert 'whole_host_swap_above_60_percent' in result['issues']
    assert any('knowledge-graph-compile' in issue for issue in result['issues'])


def test_collector_select_only_and_safe_property_projection(monkeypatch):
    m = load(); calls = []
    def command(args, **kw):
        calls.append((args, kw))
        if args[0] == 'systemctl':
            return 'MemoryPeak=123\nResult=success\nEnvironment=untrusted-value\n'
        if args[-1]=='status':return '{"active_jobs":[],"queued_jobs":[]}'
        return json.dumps({'meta':{'compile_seq':'48','assignments':'100','unexpected':'discard'},
                           'ledger':{'ok':True,'compile_seq':48,'unexpected':'discard'}})
    monkeypatch.setattr(m,'command',command)
    monkeypatch.setattr(m.Path,'read_text',lambda self:'{"members":{}}' if self.name=='heavy-job-policy.json' else '{"compile_seq":48}')
    data = m.observations()
    assert 'Environment' not in data['unit']
    assert 'unexpected' not in json.dumps(data)
    args, kw = calls[1]
    assert 'default_transaction_read_only=on' in args[-1]
    assert kw['input'].lstrip().startswith('SELECT')
    assert 'stop' not in str(calls) and 'set-property' not in str(calls)


def test_actor_guard_blocks_agent(monkeypatch):
    from types import SimpleNamespace
    m = load()
    monkeypatch.setattr(m.os,'getuid',lambda:1003)
    monkeypatch.setattr(m.pwd,'getpwuid',lambda uid:SimpleNamespace(pw_name='ctdc-agent-openai-codex'))
    monkeypatch.setattr(m,'command',lambda *a,**kw:pytest.fail('agent execution'))
    assert m.main() == 77


def test_persistent_timer_only_checks_due_and_does_not_trigger_compile():
    root = Path(__file__).resolve().parents[2]
    timer = (root/'.config/systemd/user/corporatetraveldc-brain-capacity-review.timer').read_text()
    assert 'Persistent=true' in timer and 'OnCalendar=' in timer
    assert 'OnUnitActiveSec=' not in timer
    service = (root/'.config/systemd/user/corporatetraveldc-brain-capacity-review.service').read_text()
    assert 'OnFailure=corporatetraveldc-unit-failure-notify@%n.service' in service
    assert 'systemctl start' not in service
