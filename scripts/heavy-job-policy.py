#!/usr/bin/env python3
"""Shared two-job queue and adaptive swap for signed heavy-job profiles.

ExecCondition admits or durably queues a request; it never spends a job's
run timeout waiting. A separate manager dispatches and observes at most two
admitted jobs. RAM remains 2.5 GiB/job, swap starts at zero, first grant is
1 GiB at 90% RAM, subsequent grants are 256 MiB, at most 4 GiB swap/job.
Reservations include the host's other swap consumers and stay within 60%
at admission/grant decisions. Other services can independently exceed that
host threshold; this controller never kills them or evicts their pages.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'config/heavy-job-policy.json'
STATE=Path('/var/lib/corporatetraveldc/heavy-job-policy')
REPORTS=Path('/var/lib/corporatetraveldc/reports/heavy-jobs')
CGROUP=Path('/sys/fs/cgroup')
MANAGER='corporatetraveldc-heavy-job-manager.service'
SLICE='production-brain.slice'
BUSY={'active','activating','deactivating'}
PROPS=('Id','ActiveState','SubState','MainPID','InvocationID','ControlGroup',
       'MemoryHigh','MemoryMax','MemorySwapMax','Slice','Result',
       'ExecMainStatus','ExecMainStartTimestampMonotonic','UnitFileState',
       'MemoryCurrent','MemoryPeak','MemorySwapCurrent')
RESULTS={'success','exit-code','signal','core-dump','watchdog','timeout',
         'oom-kill','resources','exec-condition','start-limit-hit','protocol'}


def stamp():return datetime.now(timezone.utc).isoformat()


def command(args,timeout=20,input=None):
    r=subprocess.run(args,capture_output=True,text=True,timeout=timeout,input=input)
    if r.returncode:raise RuntimeError('policy command unavailable; inspect operator logs')
    return r.stdout.strip()


def number(value):
    return int(value) if str(value).isdigit() else None


def policy():
    p=json.loads(CONFIG.read_text())
    required={'version':1,'max_parallel':2,'host_swap_percent':60,
              'soft_bytes':2*1024**3,'ram_bytes':2560*1024**2,
              'initial_swap_bytes':1024**3,'swap_step_bytes':256*1024**2,
              'max_swap_bytes':4*1024**3,'free_swap_reserve_bytes':512*1024**2}
    if any(p.get(k)!=v for k,v in required.items()):raise RuntimeError('unreviewed policy constants')
    if not p.get('members') or any(not re.fullmatch(r'corporatetraveldc-[a-z0-9-]+\.service',u)
                                   for u in p['members']):raise RuntimeError('invalid pool members')
    return p


def fingerprint():
    return hashlib.sha256(Path(__file__).read_bytes()+CONFIG.read_bytes()).hexdigest()


def boot_id():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def properties(names):
    if not names:return {}
    raw=command(['systemctl','--user','show',*sorted(names),'-p',','.join(PROPS)])
    out={}
    for block in raw.split('\n\n'):
        row={k:v for line in block.splitlines() for k,sep,v in [line.partition('=')]
             if sep and k in PROPS}
        if row.get('Id') in names:out[row['Id']]=row
    if set(out)!=set(names):raise RuntimeError('unit observations incomplete')
    return out


def memory():
    fields={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key,_,value=line.partition(':')
        if key in ('SwapTotal','SwapFree'):fields[key]=int(value.split()[0])*1024
    if len(fields)!=2 or fields['SwapFree']>fields['SwapTotal']:
        raise RuntimeError('host swap observations unavailable')
    return fields['SwapTotal'],fields['SwapTotal']-fields['SwapFree']


def save(path,data,mode=0o600):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temp=tempfile.mkstemp(prefix='.policy-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),mode);json.dump(data,f,indent=2);f.write('\n')
            f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


@contextmanager
def locked(p):
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    if STATE.is_symlink() or STATE.stat().st_uid!=os.getuid():raise RuntimeError('unsafe state directory')
    fd=os.open(STATE/'policy.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        path=STATE/'state.json'
        if path.is_symlink():raise RuntimeError('unsafe state path')
        state=json.loads(path.read_text()) if path.exists() else {
            'fingerprint':fingerprint(),'boot_id':boot_id(),'queue':{},'leases':{},'ticket':0,'manager':{}}
        if state.get('fingerprint')!=fingerprint():raise RuntimeError('drain old policy before replacing it')
        if not isinstance(state.get('queue'),dict) or not isinstance(state.get('leases'),dict):
            raise RuntimeError('invalid pool state')
        if (set(state['queue'])|set(state['leases']))-set(p['members']):raise RuntimeError('unknown state member')
        if len(state['leases'])>p['max_parallel'] or set(state['queue'])&set(state['leases']):
            raise RuntimeError('invalid admission state')
        for lease in state['leases'].values():
            if (not re.fullmatch('[a-f0-9]{32}',lease.get('invocation',''))
                    or not valid_budget(lease.get('budget'),p)
                    or lease.get('reserve') not in range(p['initial_swap_bytes'],p['max_swap_bytes']+1,p['swap_step_bytes'])
                    or lease['reserve']<lease['budget']):raise RuntimeError('invalid swap reservation')
        if state.get('boot_id')!=boot_id():
            # Queued, never-started work survives reboot. Interrupted work
            # needs review: do not repeat potentially non-idempotent writes.
            for unit,lease in state['leases'].items():finish(unit,lease,'interrupted-reboot',False)
            for queued in state['queue'].values():queued.pop('dispatch_until',None)
            state.update(boot_id=boot_id(),leases={},manager={})
        yield state
        save(path,state)
    finally:os.close(fd)


def valid_budget(value,p):
    return value==0 or value in range(p['initial_swap_bytes'],p['max_swap_bytes']+1,p['swap_step_bytes'])


def cgroup(unit,row,p):
    name=row.get('ControlGroup','')
    if (not name.startswith('/') or '..' in Path(name).parts or Path(name).name!=unit
            or row.get('Slice')!=SLICE):raise RuntimeError('unit is outside the heavy-job slice')
    path=CGROUP/name.lstrip('/')
    if (number((path/'memory.max').read_text().strip())!=p['ram_bytes']
            or number((path/'memory.high').read_text().strip())!=p['soft_bytes']):
        raise RuntimeError('effective RAM limits differ from policy')
    return path


def counter(path,key):
    value=number((path/key).read_text().strip())
    if value is None:raise RuntimeError('cgroup accounting unavailable')
    return value


def write_limit(path,value,p):
    if not valid_budget(value,p):raise RuntimeError('unreviewed swap allowance')
    unit=path.name
    if unit not in p['members']:raise RuntimeError('unreviewed resource target')
    # Update systemd's resource context AND the kernel. Writing just the
    # cgroup file could be overwritten when systemd starts the next phase.
    command(['systemctl','--user','set-property','--runtime',unit,'MemorySwapMax='+str(value)])
    if counter(path,'memory.swap.max')!=value:raise RuntimeError('swap limit update unverified')


def parent_ceiling(path,p):
    limits=[p['max_swap_bytes']];parent=path.parent
    while parent!=CGROUP:
        field=parent/'memory.swap.max'
        if field.exists():
            raw=field.read_text().strip()
            if raw!='max':
                value=number(raw)
                if value is None:raise RuntimeError('parent swap ceiling unavailable')
                limits.append(value)
        parent=parent.parent
    return min(limits)


def budget_room(state,rows,p,extra=0):
    total,used_before=memory();own_used=0
    for unit,lease in state['leases'].items():
        row=rows.get(unit)
        if not row or row.get('InvocationID')!=lease['invocation']:
            raise RuntimeError('lease accounting incomplete')
        path=cgroup(unit,row,p)
        own_used+=min(counter(path,'memory.swap.current'),lease['reserve'])
    total_after,used_after=memory()
    if total!=total_after:raise RuntimeError('host swap changed during observation')
    outside=max(0,max(used_before,used_after)-own_used)
    reserved=sum(lease['reserve'] for lease in state['leases'].values())
    ceiling=total*p['host_swap_percent']//100
    permitted=(total>0 and outside+reserved+extra<=ceiling
               and total-max(used_before,used_after)>=extra+p['free_swap_reserve_bytes'])
    return permitted,{'host_swap_total_bytes':total,'host_swap_used_bytes':max(used_before,used_after),
                      'outside_pool_swap_bytes':outside,'reserved_swap_bytes':reserved,
                      'host_swap_budget_bytes':ceiling}


def finish(unit,lease,result,ok=True):
    report=lease['report'];report.update(finished_at=stamp(),ok=ok,
        job_result=result,final_swap_allowance_bytes=lease['budget'])
    report['work_completed']=result=='success' and report.get('job_exit_status')==0
    directory=REPORTS/unit
    save(directory/'latest.json',report)
    save(directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json'),report)


def reap(state,rows):
    for unit,lease in list(state['leases'].items()):
        row=rows[unit]
        if row.get('InvocationID')!=lease['invocation'] or row.get('ActiveState') not in BUSY:
            result=lease.get('completion') or (row.get('Result') if row.get('InvocationID')==lease['invocation'] else 'invocation-replaced')
            if 'completion' not in lease:lease['report']['job_exit_status']=number(row.get('ExecMainStatus'))
            finish(unit,lease,result,not lease['fault'] and result in RESULTS)
            del state['leases'][unit]


def enqueue(state,unit):
    if unit not in state['queue']:
        state['ticket']+=1;state['queue'][unit]={'ticket':state['ticket'],'requested_at':stamp()}


def healthy_manager(state):
    m=state['manager']
    return m.get('boot_id')==boot_id() and 0<=time.monotonic()-m.get('heartbeat',-100)<=5


def unmanaged(state,rows):
    return sorted(u for u,r in rows.items() if u not in state['leases']
                  and number(r.get('MainPID')) not in (0,None) and r.get('ActiveState') in BUSY)


def admit(unit,p):
    with locked(p) as state:
        rows=properties(set(p['members']))
        reap(state,rows);row=rows[unit]
        token=row.get('InvocationID','')
        if not re.fullmatch('[a-f0-9]{32}',token):raise RuntimeError('fresh invocation identity unavailable')
        existing=state['leases'].get(unit)
        if existing:
            if existing['invocation']==token:return 0
            raise RuntimeError('a different invocation owns the slot')
        path=cgroup(unit,row,p)
        write_limit(path,0,p)  # Also protect queued pre-start processes.
        if counter(path,'memory.swap.current')!=0:raise RuntimeError('initial swap usage has not cleared')
        enqueue(state,unit)
        # Unknown already-running work blocks admission after lost state.
        escaped=[u for u in unmanaged(state,rows) if u!=unit]
        eligible=eligible_queue(state,p)
        head=min(eligible,key=lambda u:state['queue'][u]['ticket']) if eligible else None
        room,observation=budget_room(state,rows,p,p['initial_swap_bytes'])
        if (not healthy_manager(state) or escaped or len(state['leases'])>=p['max_parallel']
                or head!=unit or not room):
            state['queue'][unit]['reason']='slots-or-host-budget-or-manager'
            return 1  # ExecCondition skip: durable request, not a completed workload.
        state['queue'].pop(unit)
        state['leases'][unit]={'invocation':token,'reserve':p['initial_swap_bytes'],'budget':0,
            'last_change':0,'fault':False,'report':{'unit':unit,'admitted_at':stamp(),'events':[],
            'admitted_monotonic':time.monotonic(),'concurrent_jobs_on_admission':len(state['leases'])+1,
            'initial_swap_bytes':0,'initial_grant_bytes':p['initial_swap_bytes'],
            'step_bytes':p['swap_step_bytes'],'max_swap_bytes':p['max_swap_bytes'],
            'same_limit_update_verified':True,'invocation_id':token,
            'invocation_start_monotonic':None,'peak_observed_ram_bytes':0,
            'peak_observed_swap_bytes':0,'admission_budget':observation}}
        return 0


def release(unit,p,token):
    with locked(p) as state:
        lease=state['leases'].get(unit)
        if not lease or lease['invocation']!=token:return 0
        row=properties({unit})[unit]
        if row.get('ActiveState') not in ('inactive','failed','deactivating'):
            raise RuntimeError('cannot release a running workload')
        result=os.environ.get('SERVICE_RESULT',row.get('Result','unknown'))
        result=result if result in RESULTS else 'unknown'
        lease['report']['job_exit_status']=number(os.environ.get('EXIT_STATUS',row.get('ExecMainStatus')))
        lease['report']['invocation_start_monotonic']=row.get('ExecMainStartTimestampMonotonic')
        try:
            path=cgroup(unit,row,p)
            for field,label in [('memory.peak','peak_observed_ram_bytes'),('memory.swap.peak','peak_observed_swap_bytes')]:
                if (path/field).exists():lease['report'][label]=max(lease['report'][label],counter(path,field))
        except (OSError,ValueError,RuntimeError):pass
        # Quadlet may append container removal after this ExecStopPost.
        # Retain slot and reservation until ALL cleanup has finished and
        # the manager observes inactive/failed (or a fresh invocation).
        lease['completion']=result
    return 0


def proposed_budget(ram,used,budget,elapsed,p):
    if not valid_budget(budget,p):raise RuntimeError('unreviewed current swap budget')
    if budget==0:
        return p['initial_swap_bytes'] if ram*10>=p['ram_bytes']*9 else 0
    if (budget<p['max_swap_bytes'] and used*5>=budget*4 and ram>=p['soft_bytes'] and elapsed>=5):
        return budget+p['swap_step_bytes']
    return budget


def observe(state,rows,p):
    for unit,lease in state['leases'].items():
        if lease['fault'] or 'completion' in lease:continue
        try:
            row=rows[unit];path=cgroup(unit,row,p)
            ram=counter(path,'memory.current');used=counter(path,'memory.swap.current')
            actual=counter(path,'memory.swap.max')
            if actual!=lease['budget']:
                # A prior manager may have died after committing reservation
                # but before acknowledging the kernel write.
                if actual!=lease.get('pending') or not valid_budget(actual,p):
                    raise RuntimeError('unexpected kernel allowance')
                lease['budget']=actual;lease.pop('pending',None)
                lease['last_change']=time.monotonic()
                if lease['report']['events']:
                    lease['report']['events'][-1].update(update_verified=True,recovered_from_kernel=True)
            r=lease['report'];r['peak_observed_ram_bytes']=max(r['peak_observed_ram_bytes'],ram)
            r['peak_observed_swap_bytes']=max(r['peak_observed_swap_bytes'],used)
            if number(row.get('ExecMainStartTimestampMonotonic')):
                r['invocation_start_monotonic']=row['ExecMainStartTimestampMonotonic']
            proposed=proposed_budget(ram,used,lease['budget'],time.monotonic()-lease['last_change'],p)
            if proposed==lease['budget']:continue
            extra=max(0,proposed-lease['reserve'])
            room,observation=budget_room(state,rows,p,extra)
            if not room or parent_ceiling(path,p)<proposed:
                r['growth_blocked']='host-60-percent-budget-or-parent-ceiling';continue
            lease['reserve']=max(lease['reserve'],proposed);lease['pending']=proposed
            event={'at':stamp(),'requested_swap_allowance_bytes':proposed,'update_verified':False,
                   'ram_bytes':ram,'swap_used_bytes':used,'host_budget':observation}
            r['events'].append(event)
            save(STATE/'state.json',state)  # Reserve before changing the kernel.
            write_limit(path,proposed,p)
            lease['budget']=proposed;lease.pop('pending',None);lease['last_change']=time.monotonic()
            event['update_verified']=True
        except (OSError,ValueError,RuntimeError):
            lease['fault']=True;lease['report']['reason']='accounting or grant unverified; no further increases'


def maintenance_open(mode='fixed'):
    args=['bash',str(ROOT/'scripts/maintenance-window-guard.sh'),'--check']
    if mode=='rolling':args.append('--rolling')
    r=subprocess.run(args,
                     capture_output=True,timeout=30)
    return r.returncode==0


def eligible_queue(state,p):
    modes={p['members'][u].get('maintenance_mode','fixed') for u in state['queue'] if p['members'][u]['maintenance']}
    windows={mode:maintenance_open(mode) for mode in modes}
    return [u for u in state['queue'] if not p['members'][u]['maintenance']
            or windows[p['members'][u].get('maintenance_mode','fixed')]]


def tick(p):
    dispatch=None;new_fault=False
    with locked(p) as state:
        rows=properties(set(p['members']))
        reap(state,rows)
        state['unmanaged_jobs']=unmanaged(state,rows)
        prior_faults={u for u,l in state['leases'].items() if l['fault']}
        if not state['unmanaged_jobs']:observe(state,rows,p)
        new_fault=bool({u for u,l in state['leases'].items() if l['fault']}-prior_faults)
        total,used=memory();over=used*100>total*p['host_swap_percent']
        new_fault=new_fault or (over and not state.get('host_swap_over_budget',False))
        state['host_swap_over_budget']=over
        state['manager']={'heartbeat':time.monotonic(),'boot_id':boot_id(),'pid':os.getpid()}
        if state['queue'] and len(state['leases'])<p['max_parallel'] and not state['unmanaged_jobs'] and not new_fault:
            room,_=budget_room(state,rows,p,p['initial_swap_bytes'])
            eligible=eligible_queue(state,p)
            if room and eligible:
                unit=min(eligible,key=lambda u:state['queue'][u]['ticket'])
                q=state['queue'][unit]
                if time.monotonic()>=q.get('dispatch_until',0):
                    q['dispatch_until']=time.monotonic()+15;dispatch=unit
    if new_fault:raise RuntimeError('new controller fault or whole-host swap threshold crossing; alert and restart observer')
    if dispatch:
        try:command(['systemctl','--user','start','--no-block',dispatch])
        except (RuntimeError,subprocess.SubprocessError):pass  # Durable queue retries.


def verify():
    command(['bash',str(ROOT/'scripts/verify-manifest.sh'),'scripts/heavy-job-policy.py',
             'config/heavy-job-policy.json','.config/systemd/user/production-brain.slice',
             '.config/systemd/user/corporatetraveldc-heavy-job-manager.service'],timeout=120)


def pilot(units,p,output):
    if not units or len(set(units))!=len(units) or len(units)>2:
        raise RuntimeError('select one or two distinct pilot jobs')
    if any(u not in p['members'] or p['members'][u]['held_timer'] for u in units):
        raise RuntimeError('pilot excludes unreviewed or held jobs')
    before_rows=properties(set(units))
    if any(r.get('ActiveState') in BUSY for r in before_rows.values()):
        raise RuntimeError('pilot job already busy; wait for completion')
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640);os.close(fd)
    start=time.monotonic();report={'started_at':stamp(),'ok':False,'jobs':{},'samples':[]}
    semantic='corporatetraveldc-semantic-compile-daily.service'
    sentinel=Path('/var/lib/corporatetraveldc/second-brain-triggers/semantic-compiled')
    before=json.loads(sentinel.read_text()) if semantic in units else None
    try:
        command(['systemctl','--user','start','--no-block',*units])
        deadline=start+max(p['members'][u]['timeout_s'] for u in units)+7200
        while time.monotonic()<deadline:
            rows=properties(set(units))
            with locked(p) as state:
                total,used=memory()
                sample={'at':stamp(),'active_jobs':len(state['leases']),'queued_jobs':len(state['queue']),
                        'host_swap_used_bytes':used,'host_swap_total_bytes':total,
                        'units':{u:{k:rows[u].get(k) for k in ('ActiveState','MemoryCurrent','MemorySwapCurrent','MemoryPeak')} for u in units}}
                rejected=[u for u in units if rows[u].get('ActiveState')=='failed'
                          and rows[u].get('InvocationID')!=before_rows[u].get('InvocationID')
                          and u not in state['leases'] and u not in state['queue']]
            if rejected:raise RuntimeError('fresh pilot admission or startup failed')
            report['samples'].append(sample);print(json.dumps(sample),flush=True)
            for unit in units:
                path=REPORTS/unit/'latest.json'
                if path.exists():
                    receipt=json.loads(path.read_text())
                    if receipt.get('admitted_monotonic',-1)>=start and receipt.get('finished_at'):
                        report['jobs'][unit]=receipt
            save(output,report,0o640)
            if len(report['jobs'])==len(units):break
            time.sleep(15)
        else:raise RuntimeError('pilot deadline reached; queued/running work remains under its normal policy')
        if any(r.get('ok') is not True or r.get('work_completed') is not True for r in report['jobs'].values()):
            raise RuntimeError('pilot job or controller did not complete successfully')
        if before is not None:
            after=json.loads(sentinel.read_text())
            sql="""SELECT json_build_object(
              'meta',(SELECT json_object_agg(key,value) FROM semantic_meta WHERE key IN ('compile_seq','compiled_at')),
              'ledger',(SELECT row_to_json(t) FROM (SELECT compile_seq,ok FROM semantic_compile_runs WHERE ok IS TRUE ORDER BY compile_seq DESC LIMIT 1) t));"""
            raw=command(['podman','exec','-i','corporatetraveldc-pgsql','sh','-c',
                'PGPASSWORD="$POSTGRES_PASSWORD" PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=10000" '
                'psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],input=sql+'\n')
            db=json.loads(raw);seq=number(after.get('compile_seq'))
            if (seq is None or seq<=int(before['compile_seq']) or seq!=int((db.get('meta') or {}).get('compile_seq',0))
                    or seq!=int((db.get('ledger') or {}).get('compile_seq',0))
                    or (db.get('ledger') or {}).get('ok') is not True
                    or after.get('compiled_at')!=(db.get('meta') or {}).get('compiled_at')):
                raise RuntimeError('semantic commit evidence inconsistent')
            report['semantic']={'before':before,'after':after,'database':db}
        report['ok']=True;return 0
    finally:
        report['finished_at']=stamp();save(output,report,0o640)
        print(json.dumps({'receipt':str(output),'ok':report['ok']}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('admit','release','manage','status','run'))
    parser.add_argument('unit',nargs='?')
    parser.add_argument('--also',action='append',default=[])
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    if os.getuid()==0 or pwd.getpwuid(os.getuid()).pw_name!='corporatetraveldc':
        print('Run from the operator account; no pool changes performed.');return 77
    try:
        p=policy()
        if args.action in ('admit','release') and args.unit not in p['members']:
            raise RuntimeError('unit is outside the reviewed pool')
        if args.action!='release':verify()
        if args.action=='admit':return admit(args.unit,p)
        if args.action=='release':return release(args.unit,p,os.environ.get('INVOCATION_ID'))
        if args.action=='run':
            if args.receipt is None:raise RuntimeError('pilot receipt path required')
            return pilot([args.unit,*args.also],p,args.receipt)
        if args.action=='manage':
            while True:tick(p);time.sleep(1)
        with locked(p) as state:
            rows=properties(set(p['members']));room,host=budget_room(state,rows,p)
            print(json.dumps({'active_jobs':sorted(state['leases']),'queued_jobs':sorted(state['queue']),
                              'max_parallel':2,'current_reservations_within_budget':room,
                              'unmanaged_jobs':unmanaged(state,rows),
                              'unit_outcomes':{u:{k:r.get(k) for k in ('ActiveState','SubState','Result','ExecMainStatus')} for u,r in rows.items()},**host}))
        return 0
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError):
        print(json.dumps({'ok':False,'reason':'pool policy unavailable; inspect operator logs'}))
        return 255 if args.action=='admit' else 1


if __name__=='__main__':raise SystemExit(main())
