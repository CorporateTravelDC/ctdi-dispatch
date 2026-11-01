#!/usr/bin/env python3
"""Read-only capacity observations, persisted every 15 days; no limit changes.

MemoryPeak is the latest retained invocation's peak, NOT a 15-day maximum.
Only aggregate semantic metadata and safe unit properties enter the report.
An unhealthy review is saved, alerts through OnFailure and retries tomorrow;
it never advances the last healthy review timestamp.
"""
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/corporatetraveldc/reports/brain-capacity')
UNIT = 'corporatetraveldc-semantic-compile-daily.service'
SOFT = 2 * 1024**3
HARD = 2560 * 1024**2
MAX_SWAP = 4*1024**3
SWAP_STEP = 256 * 1024**2
INTERVAL = timedelta(days=15)
PROPS = ('ActiveState', 'SubState', 'Result', 'ExecMainStatus',
         'ExecMainStartTimestamp', 'ExecMainExitTimestamp', 'MemoryPeak',
         'MemoryHigh', 'MemoryMax', 'EffectiveMemoryMax', 'MemorySwapMax', 'MemorySwapCurrent',
         'ExecMainStartTimestampMonotonic')
COUNTS = ('assignments', 'geometry_edges', 'causal_edges', 'cluster_edges')


def command(args, timeout=30, input=None):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=input)
    if r.returncode:
        raise RuntimeError('capacity observation unavailable; inspect operator logs')
    return r.stdout.strip()


def stamp(value):
    value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError('review timestamps must have a timezone')
    return value


def due(previous, now):
    if not previous:
        return True
    elapsed = now - stamp(previous['checked_at'])
    # A clock moving backwards cannot suppress a review indefinitely.
    return elapsed < timedelta(0) or elapsed >= INTERVAL


def integer(value):
    return int(value) if str(value).isdigit() else None


def observations():
    text = command(['systemctl', '--user', 'show', UNIT, '-p', ','.join(PROPS)])
    unit = {k:v for line in text.splitlines() for k,sep,v in [line.partition('=')]
            if sep and k in PROPS}
    # Expand the existing database container's own credentials privately.
    # SELECT-only session, no secret value in argv, logs or receipts.
    sql = """SELECT json_build_object(
      'meta',(SELECT json_object_agg(key,value) FROM semantic_meta WHERE key IN
        ('compile_seq','compiled_at','assignments','geometry_edges','causal_edges','cluster_edges')),
      'ledger',(SELECT row_to_json(t) FROM (SELECT compile_seq,ok,finished_at
        FROM semantic_compile_runs WHERE ok IS TRUE ORDER BY compile_seq DESC LIMIT 1) t));"""
    text = command(['podman', 'exec', '-i', 'corporatetraveldc-pgsql', 'sh', '-c',
        'PGPASSWORD="$POSTGRES_PASSWORD" PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=10000" '
        'psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], input=sql+'\n')
    db = json.loads(text)
    # Project even database output back to a fixed aggregate schema.
    raw = db.get('meta') or {}
    meta = {key:integer(raw.get(key)) for key in ('compile_seq', *COUNTS)}
    meta['compiled_at'] = raw.get('compiled_at')
    raw = db.get('ledger') or {}
    ledger = {'compile_seq':integer(raw.get('compile_seq')), 'ok':raw.get('ok') is True,
              'finished_at':raw.get('finished_at')}
    sentinel = json.loads(Path('/var/lib/corporatetraveldc/second-brain-triggers/semantic-compiled').read_text())
    guard=None
    try:
        path=Path('/var/lib/corporatetraveldc/reports/heavy-jobs')/UNIT/'latest.json'
        if path.stat().st_size<=65536:
            raw=json.loads(path.read_text())
            guard={key:raw.get(key) for key in ('ok','finished_at','invocation_start_monotonic',
                   'initial_swap_bytes','same_limit_update_verified','final_swap_allowance_bytes',
                   'peak_observed_ram_bytes','peak_observed_swap_bytes',
                   'initial_grant_bytes','step_bytes','max_swap_bytes')}
    except (OSError,ValueError,AttributeError):pass
    root=Path(__file__).resolve().parents[1]
    pool=json.loads(command(['/usr/bin/python3','-B',str(root/'scripts/heavy-job-policy.py'),'status'],timeout=60))
    config=json.loads((root/'config/heavy-job-policy.json').read_text())
    members={}
    for name,entry in config['members'].items():
        receipt=Path('/var/lib/corporatetraveldc/reports/heavy-jobs')/name/'latest.json'
        item={'held_timer':entry['held_timer'],'history_available':False}
        try:
            if receipt.stat().st_size<=65536:
                raw=json.loads(receipt.read_text())
                item.update(history_available=True,**{k:raw.get(k) for k in
                    ('ok','job_result','job_exit_status','work_completed','finished_at',
                     'peak_observed_ram_bytes','peak_observed_swap_bytes','final_swap_allowance_bytes')})
        except (OSError,ValueError,TypeError,AttributeError):pass
        members[name]=item
    return {'unit':unit, 'semantic':meta, 'ledger':ledger, 'swap_guard':guard,
            'heavy_pool':pool,'members':members,
            'sentinel_seq':integer(sentinel.get('compile_seq'))}


def assess(data, now, previous):
    unit, meta, ledger = data['unit'], data['semantic'], data['ledger']
    issues = []
    warnings = []
    pool=data.get('heavy_pool') or {}
    if len(pool.get('active_jobs',[]))>2:issues.append('heavy_pool_concurrency_exceeds_two')
    if pool.get('unmanaged_jobs'):issues.append('untracked_running_pool_members')
    for name,outcome in pool.get('unit_outcomes',{}).items():
        if outcome.get('ActiveState')=='failed':issues.append('pool_member_unit_failed:'+name)
    used=pool.get('host_swap_used_bytes');total=pool.get('host_swap_total_bytes')
    if isinstance(used,int) and isinstance(total,int) and used*100>total*60:
        issues.append('whole_host_swap_above_60_percent')
    for name,member in data.get('members',{}).items():
        if member.get('history_available') and (member.get('ok') is not True or member.get('job_result')!='success'):
            issues.append('pool_member_needs_review:'+name)
        elif member.get('history_available') and member.get('work_completed') is not True:
            warnings.append('pool_member_workload_skipped_or_unproven:'+name)
        elif not member.get('history_available') and not member.get('held_timer'):
            warnings.append('pool_member_not_yet_observed:'+name)
    busy = unit.get('ActiveState') in ('active', 'activating', 'deactivating')
    peak = integer(unit.get('MemoryPeak'))
    if busy:
        issues.append('compiler_busy_retry_after_completion')
    if unit.get('Result') != 'success' or unit.get('ExecMainStatus') != '0':
        issues.append('latest_invocation_unsuccessful')
    if peak is None or peak == 0:
        issues.append('latest_invocation_peak_unavailable')
    elif peak >= SOFT:
        warnings.append('latest_invocation_reached_soft_threshold')
    if integer(unit.get('MemoryHigh')) != SOFT or integer(unit.get('MemoryMax')) != HARD:
        issues.append('configured_service_limits_differ_from_policy')
    effective = integer(unit.get('EffectiveMemoryMax'))
    if effective is None or effective < HARD:
        issues.append('effective_parent_ceiling_below_policy_or_unavailable')
    live_allowance=integer(unit.get('MemorySwapMax'))
    if live_allowance!=0 and live_allowance not in range(1024**3,MAX_SWAP+SWAP_STEP,SWAP_STEP):
        issues.append('service_swap_limit_differs_from_policy')
    guard=data.get('swap_guard') or {}
    if (guard.get('ok') is not True or not guard.get('same_limit_update_verified')
            or guard.get('initial_swap_bytes')!=0 or not guard.get('finished_at')
            or guard.get('invocation_start_monotonic')!=unit.get('ExecMainStartTimestampMonotonic')):
        issues.append('fresh_successful_swap_guard_receipt_unavailable')
    if (guard.get('initial_grant_bytes')!=1024**3 or guard.get('step_bytes')!=SWAP_STEP
            or guard.get('max_swap_bytes')!=MAX_SWAP):
        issues.append('swap_guard_policy_differs_from_operator_decision')
    allowance=integer(guard.get('final_swap_allowance_bytes'))
    if not busy and allowance!=live_allowance:
        issues.append('retained_swap_allowance_and_receipt_disagree')
    if allowance!=0 and allowance not in range(1024**3,MAX_SWAP+SWAP_STEP,SWAP_STEP):
        issues.append('swap_allowance_outside_reviewed_steps')
    elif allowance==MAX_SWAP:
        issues.append('maximum_swap_allowance_reached_review_capacity')
    elif allowance:
        warnings.append('swap_allowance_was_needed')
    seq = meta.get('compile_seq')
    if not seq or seq != data['sentinel_seq'] or seq != ledger.get('compile_seq') or not ledger.get('ok'):
        issues.append('committed_revision_inconsistent')
    try:
        age = (now - stamp(meta['compiled_at'])).total_seconds()
        if age < -300 or age > 12*3600:
            issues.append('semantic_compile_stale_or_clock_skewed')
    except (ValueError, TypeError, KeyError):
        issues.append('semantic_compile_time_unavailable')
    old = (previous or {}).get('semantic', {})
    growth = {key:meta[key]-old[key] for key in COUNTS
              if isinstance(meta.get(key), int) and isinstance(old.get(key), int)}
    return {'checked_at':now.isoformat(), 'ok':not issues, 'issues':issues,'warnings':warnings,
            'policy':{'soft_bytes':SOFT, 'hard_bytes':HARD, 'review_days':15,
                      'initial_swap_grant_bytes':1024**3,'max_swap_bytes':MAX_SWAP,
                      'swap_step_bytes':SWAP_STEP,'combined_hard_bytes':HARD+MAX_SWAP},
            'memory_peak_scope':'latest retained invocation; not a 15-day maximum',
            'growth_since_last_healthy_review':growth, **data}


def save(path, data):
    fd, temp = tempfile.mkstemp(prefix='.review-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(data, f, indent=2)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def review(state, now):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(state/'review.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        latest = state/'last-healthy.json'
        previous = json.loads(latest.read_text()) if latest.exists() else None
        if not due(previous, now):
            print(json.dumps({'review_due':False, 'next_due':(stamp(previous['checked_at'])+INTERVAL).isoformat()}))
            return 0
        data = assess(observations(), now, previous)
        save(state/(now.strftime('%Y%m%dT%H%M%S%fZ')+'.json'), data)
        save(state/'latest.json', data)
        if data['ok']:
            save(latest, data)
        print(json.dumps({'review_due':True, 'ok':data['ok'], 'issues':data['issues'],
                          'warnings':data['warnings'],
                          'report':str(state/'latest.json')}))
        return 0 if data['ok'] else 1
    finally:
        os.close(fd)


def main():
    if os.getuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'corporatetraveldc':
        print('Run from the corporatetraveldc operator account; no observations made.')
        return 77
    try:
        command(['bash', str(ROOT/'scripts/verify-manifest.sh'),
                 'scripts/brain-capacity-review.py',
                 '.config/containers/systemd/corporatetraveldc-semantic-compile-daily.container',
                 '.config/systemd/user/corporatetraveldc-brain-capacity-review.service',
                 '.config/systemd/user/corporatetraveldc-brain-capacity-review.timer'], timeout=120)
        return review(STATE, datetime.now(timezone.utc))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError):
        print(json.dumps({'ok':False, 'reason':'capacity review unavailable; inspect operator logs; retry next day'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
