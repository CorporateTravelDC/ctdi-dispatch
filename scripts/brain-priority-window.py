#!/usr/bin/env python3
"""Operator-only, bounded CPU priority for one deterministic brain job.

No ingest service is stopped or restarted. Memory limits, thermal guards,
calendar grids and maintenance conditions are unchanged. A private copy of
this restorer is scheduled before changing any weight; it also restores on
normal completion, error or interrupt. This is a scheduling aid, not proof
of recovery. Run only from the reviewed, signed source after diagnosis.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ('semantic-compile-daily', 'knowledge-graph-compile', 'second-brain-index-scan')
INGEST = ('core', 'fdps', 'stdds', 'tfms', 'tbfm', 'itws', 'notam')
UNITS = {'corporatetraveldc-' + name + '.service' for name in TARGETS}
UNITS.update('corporatetraveldc-ingest-' + name + '.service' for name in INGEST)


class Refused(RuntimeError):
    pass


def command(args, timeout=30):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused('command unavailable or timed out; inspect operator logs') from exc
    if result.returncode:
        raise Refused('command failed; inspect operator logs')
    return result.stdout.strip()


def props(unit):
    text = command(['systemctl', '--user', 'show', unit, '-p',
                    'LoadState,ActiveState,SubState,CPUWeight,Result,ExecMainStatus,ExecMainStartTimestampMonotonic'])
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def weight(value):
    if value in ('[not set]', '18446744073709551615'):
        return ''  # Native systemd reset to the configured default.
    if not re.fullmatch(r'[0-9]{1,5}', value or '') or not 1 <= int(value) <= 10000:
        raise Refused('unrecognized CPU weight; no priority changes made')
    return value


def save(path, data):
    fd, tmp = tempfile.mkstemp(prefix='.lease-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def locked_state(path):
    if path.is_symlink() or not path.is_dir():
        raise Refused('lease directory unavailable')
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Refused('lease directory must be private and operator-owned')
    fd = os.open(path / 'lease.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def restore(path):
    fd = locked_state(path)
    try:
        receipt = path / 'weights.json'
        if receipt.is_symlink():
            raise Refused('lease receipt must not be a symlink')
        data = json.loads(receipt.read_text())
        if data.get('restored'):
            return
        changes = data.get('changes')
        if not isinstance(changes, list) or not changes or len(changes) > 9:
            raise Refused('invalid priority receipt')
        for row in changes:
            if row.get('unit') not in UNITS or row.get('applied') not in ('10', '1000'):
                raise Refused('invalid priority receipt')
            if row.get('before') != '':
                weight(row.get('before'))
        failed = False
        for row in changes:
            try:
                # Restore ALL captured weights, including a mutation whose
                # reply was lost. Never infer restoration from a CLI timeout.
                command(['systemctl', '--user', 'set-property', '--runtime', row['unit'],
                         'CPUWeight=' + row['before']])
                row['restored'] = True
            except Refused:
                failed = True
        data['restored'] = not failed
        save(receipt, data)
        if failed:
            raise Refused('some CPU weights could not be restored; retry restore with this lease')
    finally:
        os.close(fd)


def plan(target):
    target_unit = 'corporatetraveldc-' + target + '.service'
    current = props(target_unit)
    if current.get('LoadState') != 'loaded' or current.get('ActiveState') not in ('inactive', 'failed'):
        raise Refused('target is missing or already busy; no duplicate job started')
    for other in TARGETS:
        if other != target:
            p = props('corporatetraveldc-' + other + '.service')
            if p.get('ActiveState') in ('active', 'activating', 'deactivating'):
                raise Refused('another brain job is busy; wait before serial recovery')
    changes = [dict(unit=target_unit, before=weight(current.get('CPUWeight')), applied='1000')]
    for name in INGEST:
        unit = 'corporatetraveldc-ingest-' + name + '.service'
        p = props(unit)
        if p.get('LoadState') == 'loaded' and p.get('ActiveState') == 'active':
            changes.append(dict(unit=unit, before=weight(p.get('CPUWeight')), applied='10'))
    return changes


def prioritize(target, seconds, state):
    changes = plan(target)
    save(state / 'weights.json', {'restored': False, 'changes': changes, 'seconds': seconds})
    # This copy survives a checkout switch and is readable only by the operator.
    restorer = state / 'restore.py'
    shutil.copyfile(Path(__file__), restorer)
    restorer.chmod(0o700)
    watchdog = 'ctdi-brain-priority-' + state.name.rsplit('-', 1)[-1]
    command(['systemd-run', '--user', '--quiet', '--unit=' + watchdog,
             '--on-active=' + str(seconds) + 's', '--timer-property=AccuracySec=1s',
             '/usr/bin/python3', '-B', str(restorer), 'restore', '--state', str(state)])
    if command(['systemctl', '--user', 'show', watchdog + '.timer', '-p', 'ActiveState', '--value']) != 'active':
        raise Refused('restoration timer is not active; no CPU weights changed')
    try:
        deadline = time.monotonic() + seconds
        mutation_fd = locked_state(state)
        try:
            if json.loads((state / 'weights.json').read_text()).get('restored'):
                raise Refused('priority lease already restored; no new weights applied')
            for row in changes:
                if time.monotonic() >= deadline:
                    raise Refused('priority setup exceeded its lease; restoring captured weights')
                command(['systemctl', '--user', 'set-property', '--runtime', row['unit'],
                         'CPUWeight=' + row['applied']])
        finally:
            os.close(mutation_fd)
        target_unit = 'corporatetraveldc-' + target + '.service'
        # Recheck after setup; a timer might have started it in the meantime.
        if props(target_unit).get('ActiveState') not in ('inactive', 'failed'):
            raise Refused('target became busy; no duplicate job started')
        if os.getloadavg()[0] >= 24:
            raise Refused('load rose to at least 24 during setup; restoring weights without starting work')
        started = time.monotonic()
        # A command timeout does not kill the systemd job. The lease expires
        # regardless, and the unit's own timeout and thermal policy still apply.
        command(['systemctl', '--user', 'start', target_unit], timeout=1900)
        after = props(target_unit)
        fresh = int(after.get('ExecMainStartTimestampMonotonic') or 0) / 1_000_000 >= started
        if (not fresh or after.get('Result') != 'success' or after.get('ExecMainStatus') != '0'
                or after.get('ActiveState') not in ('inactive', 'active')
                or after.get('SubState') not in ('dead', 'exited')):
            raise Refused('no fresh completed successful invocation; output acceptance is still required')
        print(json.dumps({'service':target_unit, 'unit_completed':True, 'outputs_verified':False}))
    finally:
        restore(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'restore'))
    parser.add_argument('--service', choices=TARGETS)
    parser.add_argument('--seconds', type=int, default=900)
    parser.add_argument('--state', type=Path)
    args = parser.parse_args()
    if os.getuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'corporatetraveldc':
        print('Run from the corporatetraveldc operator terminal; no actions performed.')
        return 77
    try:
        if args.action == 'restore':
            if args.state is None:
                raise Refused('lease directory is required')
            restore(args.state)
            return 0
        if not args.service or not 60 <= args.seconds <= 1800 or args.state is not None:
            raise Refused('choose one service and a 60–1800 second priority window')
        command(['bash', str(ROOT / 'scripts/verify-manifest.sh')], timeout=300)
        if os.getloadavg()[0] >= 24:
            raise Refused('load is at least 24; wait for existing load guard headroom')
        lockpath = Path('/run/user') / str(os.getuid()) / 'ctdi-brain-priority.lock'
        lockfd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = Path(tempfile.mkdtemp(prefix='ctdi-brain-priority-'))
            print('priority_lease=' + str(state), flush=True)
            def interrupted(*unused):
                raise Refused('interrupted; priority restoration requested')
            signal.signal(signal.SIGTERM, interrupted)
            prioritize(args.service, args.seconds, state)
        finally:
            os.close(lockfd)
        return 0
    except (Refused, OSError, ValueError, TypeError, KeyboardInterrupt) as exc:
        print(json.dumps({'ok':False, 'reason':str(exc) if isinstance(exc,Refused)
                          else 'runtime unavailable; inspect private lease and operator logs'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
