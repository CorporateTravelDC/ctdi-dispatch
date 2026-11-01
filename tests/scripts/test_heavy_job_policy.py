"""Real file-lock races, durable queues, resource grants and whole-host budget."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def load():
    path=Path(__file__).resolve().parents[2]/'scripts/heavy-job-policy.py'
    spec=importlib.util.spec_from_file_location('heavy_pool_fixture',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


@pytest.fixture
def pool(monkeypatch,tmp_path):
    m=load();p=m.policy();clock=[1000.];outside=[3*1024**3]
    monkeypatch.setattr(m,'STATE',tmp_path/'state');monkeypatch.setattr(m,'REPORTS',tmp_path/'reports')
    monkeypatch.setattr(m,'CGROUP',tmp_path/'cgroup');monkeypatch.setattr(m,'boot_id',lambda:'boot-fixture')
    monkeypatch.setattr(m,'fingerprint',lambda:'policy-fixture')
    monkeypatch.setattr(m.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(m,'maintenance_open',lambda *args:True)
    rows={};paths={}
    for i,unit in enumerate(p['members']):
        group=m.CGROUP/'production.slice'/'production-brain.slice'/unit;group.mkdir(parents=True)
        for name,value in {'memory.max':p['ram_bytes'],'memory.high':p['soft_bytes'],
                           'memory.swap.max':p['max_swap_bytes'],'memory.swap.current':0,'memory.current':0}.items():
            (group/name).write_text(str(value))
        rows[unit]={'Id':unit,'ActiveState':'inactive','MainPID':'0','InvocationID':f'{i+1:032x}',
                    'ControlGroup':'/'+str(group.relative_to(m.CGROUP)),'Slice':m.SLICE,
                    'Result':'success','ExecMainStatus':'0','ExecMainStartTimestampMonotonic':'12345'}
        paths[unit]=group
    (m.CGROUP/'production.slice'/'production-brain.slice'/'memory.swap.max').write_text(str(8*1024**3))
    monkeypatch.setattr(m,'properties',lambda names:{u:dict(rows[u]) for u in names})
    def command(args,**kwargs):
        assert args[:4]==['systemctl','--user','set-property','--runtime']
        assert args[4] in paths and args[5].startswith('MemorySwapMax=')
        (paths[args[4]]/'memory.swap.max').write_text(args[5].partition('=')[2])
        return ''
    monkeypatch.setattr(m,'command',command)
    monkeypatch.setattr(m,'memory',lambda:(16*1024**3,outside[0]+sum(int((g/'memory.swap.current').read_text()) for g in paths.values())))
    with m.locked(p) as state:state['manager']={'heartbeat':clock[0],'boot_id':'boot-fixture'}
    return SimpleNamespace(m=m,p=p,clock=clock,outside=outside,rows=rows,paths=paths,
                           units=[u for u,e in p['members'].items() if not e['maintenance']])


def read(pool):return json.loads((pool.m.STATE/'state.json').read_text())


def admit(pool,unit):
    pool.rows[unit]['ActiveState']='activating'
    return pool.m.admit(unit,pool.p)


def test_two_jobs_admitted_third_is_durably_queued(pool):
    a,b,c=pool.units[:3]
    assert [admit(pool,u) for u in (a,b,c)]==[0,0,1]
    state=read(pool)
    assert len(state['leases'])==2 and c in state['queue']
    assert all(lease['reserve']==pool.p['initial_swap_bytes'] and lease['budget']==0 for lease in state['leases'].values())
    assert all((pool.paths[u]/'memory.swap.max').read_text()=='0' for u in (a,b,c))


def test_slot_limit_survives_simultaneous_admissions_in_separate_processes(pool):
    children=[]
    for unit in pool.units[:3]:pool.rows[unit]['ActiveState']='activating'
    for unit in pool.units[:3]:
        pid=os.fork()
        if pid==0:
            try:os._exit(admit(pool,unit))
            except BaseException:os._exit(99)
        children.append(pid)
    codes=[os.waitstatus_to_exitcode(os.waitpid(pid,0)[1]) for pid in children]
    assert sorted(codes)==[0,0,1]
    assert len(read(pool)['leases'])==2


def test_release_records_completion_and_next_request_gets_slot(pool,monkeypatch):
    a,b,c=pool.units[:3]
    for u in (a,b,c):admit(pool,u)
    pool.rows[a]['ActiveState']='deactivating';monkeypatch.setenv('SERVICE_RESULT','success')
    (pool.paths[a]/'memory.peak').write_text('1742340096')
    assert pool.m.release(a,pool.p,pool.rows[a]['InvocationID'])==0
    assert admit(pool,c)==1  # Slot remains held through the entire cleanup phase.
    pool.rows[a]['ActiveState']='inactive'
    with pool.m.locked(pool.p) as state:pool.m.reap(state,pool.rows)
    report=json.loads((pool.m.REPORTS/a/'latest.json').read_text())
    assert report['peak_observed_ram_bytes']==1742340096 and report['invocation_start_monotonic']=='12345'
    assert admit(pool,c)==0


def test_running_job_cannot_be_released_by_manual_cleanup(pool):
    unit=pool.units[0];admit(pool,unit)
    with pytest.raises(RuntimeError,match='running'):pool.m.release(unit,pool.p,pool.rows[unit]['InvocationID'])
    assert unit in read(pool)['leases']


def test_host_60_percent_budget_accounts_for_other_services(pool):
    pool.outside[0]=int(9.5*1024**3)
    assert admit(pool,pool.units[0])==1
    assert not read(pool)['leases']


def test_first_grant_one_gib_at_225_gib_ram_then_quarter_gib_steps(pool):
    m,p=pool.m,pool.p;unit=pool.units[0];admit(pool,unit);path=pool.paths[unit]
    (path/'memory.current').write_text(str(p['ram_bytes']*9//10-1))
    with m.locked(p) as s:m.observe(s,pool.rows,p)
    assert read(pool)['leases'][unit]['budget']==0
    (path/'memory.current').write_text(str(p['ram_bytes']*9//10))
    with m.locked(p) as s:m.observe(s,pool.rows,p)
    assert read(pool)['leases'][unit]['budget']==1024**3
    pool.clock[0]+=5
    with m.locked(p) as s:m.observe(s,pool.rows,p)
    assert read(pool)['leases'][unit]['budget']==1024**3  # Allowance alone is not pressure.
    (path/'memory.swap.current').write_text(str(1024**3))
    with m.locked(p) as s:m.observe(s,pool.rows,p)
    assert read(pool)['leases'][unit]['budget']==1280*1024**2


def test_two_jobs_cannot_reserve_past_host_budget_in_a_racing_tick(pool):
    m,p=pool.m,pool.p
    for unit in pool.units[:2]:
        admit(pool,unit);(pool.paths[unit]/'memory.current').write_text(str(p['ram_bytes']))
    for _ in range(20):
        state=read(pool)
        for unit,l in state['leases'].items():(pool.paths[unit]/'memory.swap.current').write_text(str(l['budget']))
        pool.clock[0]+=5
        with m.locked(p) as s:m.observe(s,pool.rows,p)
    state=read(pool)
    assert sum(l['reserve'] for l in state['leases'].values())+pool.outside[0]<=16*1024**3*60//100
    assert any(l['report'].get('growth_blocked') for l in state['leases'].values())
    pool.outside[0]=0
    for _ in range(20):
        for unit,l in read(pool)['leases'].items():(pool.paths[unit]/'memory.swap.current').write_text(str(l['budget']))
        pool.clock[0]+=5
        with m.locked(p) as s:m.observe(s,pool.rows,p)
    assert all(l['budget']==4*1024**3 for l in read(pool)['leases'].values())
    assert p['ram_bytes']+p['max_swap_bytes']==int(6.5*1024**3)


def test_parent_swap_limit_blocks_growth(pool):
    m,p=pool.m,pool.p;unit=pool.units[0];admit(pool,unit)
    (pool.paths[unit]/'memory.current').write_text(str(p['ram_bytes']))
    (pool.paths[unit].parent/'memory.swap.max').write_text('0')
    with m.locked(p) as s:m.observe(s,pool.rows,p)
    assert read(pool)['leases'][unit]['budget']==0


def test_reservation_is_committed_before_uncertain_kernel_reply(pool,monkeypatch):
    m,p=pool.m,pool.p;unit=pool.units[0];admit(pool,unit)
    (pool.paths[unit]/'memory.current').write_text(str(p['ram_bytes']))
    real=m.write_limit
    def interrupted(path,value,policy):
        real(path,value,policy)
        raise SystemExit('simulate manager death after kernel write')
    with monkeypatch.context() as mp:
        mp.setattr(m,'write_limit',interrupted)
        with pytest.raises(SystemExit):
            with m.locked(p) as s:m.observe(s,pool.rows,p)
    old=read(pool)['leases'][unit]
    assert old['budget']==0 and old['pending']==old['reserve']==p['initial_swap_bytes']
    with m.locked(p) as s:m.observe(s,pool.rows,p)
    fixed=read(pool)['leases'][unit]
    assert fixed['budget']==p['initial_swap_bytes'] and fixed['report']['events'][-1]['update_verified']


def test_queue_survives_reboot_but_interrupted_writes_are_not_auto_retried(pool,monkeypatch):
    for u in pool.units[:3]:admit(pool,u)
    with pool.m.locked(pool.p) as s:s['queue'][pool.units[2]]['dispatch_until']=99999
    monkeypatch.setattr(pool.m,'boot_id',lambda:'new-boot')
    with pool.m.locked(pool.p) as s:
        assert list(s['queue'])==[pool.units[2]] and not s['leases']
        assert 'dispatch_until' not in s['queue'][pool.units[2]]


def test_changed_policy_or_corrupted_reservations_fail_closed(pool):
    u=pool.units[0];admit(pool,u)
    s=read(pool);s['leases'][u]['reserve']=-1
    (pool.m.STATE/'state.json').write_text(json.dumps(s))
    with pytest.raises(RuntimeError,match='reservation'):
        with pool.m.locked(pool.p):pass


def test_untracked_running_worker_prevents_another_admission(pool):
    pool.rows[pool.units[0]].update(ActiveState='activating',MainPID='111')
    assert admit(pool,pool.units[1])==1
    assert not read(pool)['leases']


def test_closed_maintenance_window_does_not_block_other_queue_members(pool,monkeypatch):
    m,p=pool.m,pool.p;closed=next(u for u,e in p['members'].items() if e['maintenance'])
    with m.locked(p) as s:m.enqueue(s,closed)
    monkeypatch.setattr(m,'maintenance_open',lambda *args:False)
    assert admit(pool,pool.units[0])==0


def test_rolling_and_fixed_windows_are_checked_independently(pool,monkeypatch):
    m,p=pool.m,pool.p
    fixed=next(u for u,e in p['members'].items() if e['maintenance_mode']=='fixed')
    rolling=next(u for u,e in p['members'].items() if e['maintenance_mode']=='rolling')
    with m.locked(p) as s:m.enqueue(s,fixed);m.enqueue(s,rolling)
    calls=[]
    def window(mode):calls.append(mode);return mode=='rolling'
    monkeypatch.setattr(m,'maintenance_open',window)
    assert admit(pool,rolling)==0
    assert set(calls)=={'fixed','rolling'}


def test_success_exit_75_is_recorded_as_skipped_work(pool,monkeypatch):
    u=pool.units[0];admit(pool,u);pool.rows[u]['ActiveState']='deactivating'
    monkeypatch.setenv('SERVICE_RESULT','success');monkeypatch.setenv('EXIT_STATUS','75')
    pool.m.release(u,pool.p,pool.rows[u]['InvocationID'])
    pool.rows[u]['ActiveState']='inactive'
    with pool.m.locked(pool.p) as state:pool.m.reap(state,pool.rows)
    report=json.loads((pool.m.REPORTS/u/'latest.json').read_text())
    assert report['ok'] and not report['work_completed'] and report['job_exit_status']==75


def test_failed_grant_is_retained_as_controller_failure_when_reaped(pool,monkeypatch):
    m,p=pool.m,pool.p;u=pool.units[0];admit(pool,u)
    with m.locked(p) as s:s['leases'][u]['fault']=True
    pool.rows[u]['ActiveState']='inactive'
    with m.locked(p) as s:m.reap(s,pool.rows)
    assert not json.loads((m.REPORTS/u/'latest.json').read_text())['ok']


def test_manager_disconnect_queues_instead_of_starting_unobserved_work(pool):
    with pool.m.locked(pool.p) as s:s['manager']['heartbeat']=0
    assert admit(pool,pool.units[0])==1


def test_profiles_cover_each_registered_job_and_preserve_existing_timeouts():
    m=load();p=m.policy()
    root=Path(__file__).resolve().parents[2]
    for unit,entry in p['members'].items():
        stem=unit.removesuffix('.service')
        path=(root/'.config/containers/systemd'/(stem+'.container.d')/'90-heavy-job-policy.conf'
              if entry['kind']=='container' else root/'.config/systemd/user'/(unit+'.d')/'90-heavy-job-policy.conf')
        body=path.read_text()
        assert 'ExecCondition=' in body and 'TimeoutStartSec=' not in body
        assert 'MemoryMax=2560M' in body and 'MemorySwapMax=4G' in body
    for unit in ['corporatetraveldc-ops-brief.service','corporatetraveldc-ep-advance.service']:
        assert unit not in p['members']


def test_agent_actor_cannot_manage_pool(monkeypatch):
    m=load();monkeypatch.setattr(m.os,'getuid',lambda:1003)
    monkeypatch.setattr(m.pwd,'getpwuid',lambda uid:SimpleNamespace(pw_name='ctdc-agent-openai-codex'))
    monkeypatch.setattr(m.argparse.ArgumentParser,'parse_args',lambda self:SimpleNamespace(action='manage'))
    monkeypatch.setattr(m,'verify',lambda:pytest.fail('agent verified mutable operator control'))
    assert m.main()==77
