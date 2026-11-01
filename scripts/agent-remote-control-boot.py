#!/usr/bin/env python3
"""Keep provisioned local agent remote controls enabled, without reactivation.

Installed root copy only. No credential reads, pairing, account unlocks, home
writes as root, unit installation, unmasking, service stops, or restarts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

REGISTRY = Path('/etc/ctdc-accounts.conf')
POLICY = Path('/etc/ctdc-remote-access.conf')
INERT = Path('/var/lib/corporatetraveldc/team-liveness/inert')
UNITS = {m: 'corporatetraveldc-' + m + '-remote-control.service'
         for m in ('claude', 'codex')}
PROPS = ('LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'Result')
ACCOUNT = re.compile(r'ctdc-agent-[a-z0-9-]+\Z')


def trusted_text(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as f:
        s = os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise ValueError('untrusted metadata')
        data = f.read(65537)
        if len(data) > 65536:
            raise ValueError('oversize metadata')
        return data


def registry(text):
    rows, seen = [], set()
    for raw in text.splitlines():
        fields = raw.partition('#')[0].split()
        if not fields:
            continue
        if len(fields) != 4:
            raise ValueError('invalid registry row')
        name, kind, mode, preloaded = fields
        if name in seen or preloaded not in ('0', '1'):
            raise ValueError('duplicate/invalid registry row')
        seen.add(name)
        if kind == 'agent' and mode in UNITS:
            if not ACCOUNT.fullmatch(name):
                raise ValueError('invalid local agent name')
            rows.append(dict(account=name, mode=mode, preloaded=preloaded == '1'))
    return rows


def holds():
    try:
        p = json.loads(trusted_text(POLICY))
    except FileNotFoundError:
        return False, set()
    if (not isinstance(p, dict) or p.get('schema_version') != 1
            or type(p.get('access_lockdown')) is not bool
            or not isinstance(p.get('held_accounts'), list)
            or any(not isinstance(n, str) for n in p['held_accounts'])):
        raise ValueError('invalid remote-access holds')
    return p['access_lockdown'], set(p['held_accounts'])


class System:
    def run(self, args):
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=20, check=False, stdin=subprocess.DEVNULL)

    def userctl(self, user, args):
        # systemctl and its child daemon execute as the target agent, never root.
        runtime = '/run/user/' + str(user.pw_uid)
        return self.run(['/usr/sbin/runuser', '-u', user.pw_name, '--',
                         '/usr/bin/env', 'XDG_RUNTIME_DIR=' + runtime,
                         'DBUS_SESSION_BUS_ADDRESS=unix:path=' + runtime + '/bus',
                         '/usr/bin/systemctl', '--user'] + args)

    def properties(self, user, unit):
        r = self.userctl(user, ['show', unit] + ['--property=' + p for p in PROPS])
        if r.returncode:
            return None
        return {k: v for line in r.stdout.splitlines()
                for k, sep, v in [line.partition('=')] if sep and k in PROPS}

    def expired(self, name):
        r = self.run(['/usr/bin/env', 'LC_ALL=C', '/usr/bin/chage', '-l', name])
        if r.returncode:
            raise ValueError('expiry metadata unavailable')
        value = next((line.partition(':')[2].strip() for line in r.stdout.splitlines()
                      if line.startswith('Account expires')), None)
        if value == 'never':
            return False
        if value is None:
            raise ValueError('expiry metadata missing')
        return dt.datetime.strptime(value, '%b %d, %Y').date() <= dt.date.today()

    def locked(self, name):
        # Match team-liveness: passwd -S also says locked for healthy key-only
        # accounts. Inspect the prefix in memory only; never log the record.
        r = self.run(['/usr/bin/getent', 'shadow', name])
        fields = r.stdout.split(':')
        if r.returncode or len(fields) < 2 or fields[0] != name:
            raise ValueError('account lock metadata unavailable')
        value = fields[1]
        return value.startswith('!') and value.lstrip('!') not in ('', '*')


def reason(row, user, system, policy_reader=holds):
    lockdown, held = policy_reader()
    if lockdown:
        return 'access lockdown'
    if row['account'] in held:
        return 'explicit access hold'
    if row['preloaded']:
        return 'preloaded'
    if user.pw_uid == 0 or user.pw_name != row['account']:
        return 'invalid account identity'
    if user.pw_shell.endswith(('/nologin', '/false')):
        return 'login disabled'
    if os.path.lexists(INERT / row['account']):
        return 'inert'
    if system.expired(row['account']):
        return 'expired'
    if system.locked(row['account']):
        return 'locked'
    return None


def reconcile(row, system, *, audit=False, lookup=pwd.getpwnam,
              policy_reader=holds):
    user = lookup(row['account'])
    unit = UNITS[row['mode']]
    out = dict(account=row['account'], unit=unit)
    def held():
        return reason(row, user, system, policy_reader)
    block = held()
    if block:
        return dict(out, status='held', reason=block)
    if audit:
        props = system.properties(user, unit)
        r = system.run(['/usr/bin/loginctl', 'show-user', row['account'],
                        '--property=Linger', '--value'])
        return dict(out, status='observed', properties=props,
                    linger=r.stdout.strip() == 'yes' if r.returncode == 0 else None)
    # Check for an existing mask before enabling linger whenever a bus is up.
    props = system.properties(user, unit)
    if props and (props.get('LoadState') == 'masked'
                  or props.get('UnitFileState', '').startswith('masked')):
        return dict(out, status='blocked', reason='unit masked')
    for args in (['/usr/bin/loginctl', 'enable-linger', row['account']],
                 ['/usr/bin/systemctl', 'start', 'user@' + str(user.pw_uid) + '.service']):
        block = held()
        if block:
            return dict(out, status='held', reason=block)
        if system.run(args).returncode:
            return dict(out, status='failed', reason='user manager/linger operation failed')
    # No manager ordering dependency: a lingered manager may precede this boot
    # service. enable --now handles both startup orders and persists the link.
    props = system.properties(user, unit)
    if not props or props.get('LoadState') not in ('loaded', 'masked'):
        return dict(out, status='blocked', reason='unit unavailable; provision it first')
    if props.get('LoadState') == 'masked' or props.get('UnitFileState', '').startswith('masked'):
        return dict(out, status='blocked', reason='unit masked')
    block = held()
    if block:
        return dict(out, status='held', reason=block)
    # start/enable are idempotent and do not interrupt a running remote session.
    r = system.userctl(user, ['enable', '--now', unit])
    if r.returncode:
        return dict(out, status='failed', reason='unit enable/start failed')
    props = system.properties(user, unit)
    ok = bool(props and props.get('UnitFileState') == 'enabled'
              and props.get('ActiveState') == 'active')
    return dict(out, status='ready' if ok else 'failed', properties=props,
                **({} if ok else {'reason': 'enabled/active postcondition failed'}))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--audit', action='store_true', help='read-only inspection')
    a = ap.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('operator must run the installed root helper')
    try:
        rows = registry(trusted_text(REGISTRY))
        holds()  # Invalid holds fail before any mutation.
        lock = None
        if not a.audit:
            fd = os.open('/run/ctdc-agent-remote-control-boot.lock',
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            lock = os.fdopen(fd, 'w')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                return 0
        try:
            results = []
            for row in rows:
                try:
                    results.append(reconcile(row, System(), audit=a.audit))
                except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                    # Other accounts can still recover; no raw subprocess output.
                    results.append(dict(account=row['account'], status='failed',
                                        reason='metadata or command unavailable'))
            print(json.dumps({'agents': results}, sort_keys=True))
            return int(any(r['status'] in ('blocked', 'failed') for r in results))
        finally:
            if lock:
                lock.close()
    except (OSError, ValueError):
        print(json.dumps({'error': 'trusted registry/policy unavailable or invalid'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
