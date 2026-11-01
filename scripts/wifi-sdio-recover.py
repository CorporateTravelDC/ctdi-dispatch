#!/usr/bin/env python3
"""Bounded recovery of a proven brcmfmac SDIO halt, after verified failover.

Root runs only the installed, signed copy. No arguments or environment-driven
commands. No reboot, connection teardown, credential access or sudo rule.
"""
from __future__ import annotations

import configparser
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time

CONFIG = Path('/etc/ctdc-wifi-sdio-recovery.conf')
STATE = Path('/var/lib/ctdc-wifi-recovery')
FAULT = 'failed backplane access over SDIO, halting operation'
PROBES = ('1.1.1.1', '8.8.8.8', '9.9.9.9')


def read_config(path=CONFIG, owner_uid=0):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as f:
        meta = os.fstat(f.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != owner_uid or meta.st_mode & 0o022:
            raise ValueError('untrusted recovery configuration')
        text = f.read(8193)
    if len(text) > 8192:
        raise ValueError('recovery configuration too large')
    p = configparser.ConfigParser(interpolation=None)
    p.read_string(text)
    if p.sections() != ['recovery']:
        raise ValueError('invalid recovery section')
    c = p['recovery']
    if set(c) != {'enabled', 'primary_interface', 'backup_interface', 'cooldown_seconds', 'max_attempts_per_boot'}:
        raise ValueError('invalid recovery keys')
    primary, backup = c['primary_interface'], c['backup_interface']
    if primary == backup or not all(re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', x) for x in (primary, backup)):
        raise ValueError('invalid recovery interfaces')
    cooldown, maximum = c.getint('cooldown_seconds'), c.getint('max_attempts_per_boot')
    if not 300 <= cooldown <= 86400 or not 1 <= maximum <= 3:
        raise ValueError('invalid recovery bounds')
    return dict(enabled=c.getboolean('enabled'), primary=primary, backup=backup,
                cooldown=cooldown, maximum=maximum)


class System:
    def run(self, argv, timeout=15):
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)

    def probe(self, interface):
        for address in PROBES:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(2)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b'\0')
                    s.connect((address, 443))
                return True
            except OSError:
                pass
        return False

    def route(self):
        r = self.run(['ip', '-j', '-4', 'route', 'get', PROBES[0]])
        if r.returncode:
            return None
        rows = json.loads(r.stdout)
        return rows[0].get('dev') if rows else None

    def driver(self, interface):
        try:
            return (Path('/sys/class/net') / interface / 'device/driver/module').resolve(strict=True).name
        except FileNotFoundError:
            return None

    def only_primary_uses_module(self, primary):
        return {p.name for p in Path('/sys/class/net').iterdir()
                if self.driver(p.name) == 'brcmfmac'} == {primary}

    def fault(self):
        r = self.run(['journalctl', '-k', '-b', '0', '--since', '-5 min',
                      '--grep', 'brcmfmac:.*' + FAULT, '-n', '1', '-o', 'cat', '--no-pager'])
        return r.returncode == 0 and FAULT in r.stdout

    def reload(self):
        removed = self.run(['modprobe', '-r', 'brcmfmac'], timeout=20)
        if removed.returncode:
            raise RuntimeError('brcmfmac unload failed; backup retained')
        loaded = self.run(['modprobe', 'brcmfmac'], timeout=20)
        if loaded.returncode:
            raise RuntimeError('brcmfmac load failed; backup retained')


def decide(config, state, boot, now, system):
    if not config['enabled']:
        return 'disabled'
    if state.get('boot_id') == boot:
        if int(state.get('attempts', 0)) >= config['maximum']:
            return 'boot attempt limit'
        if now - float(state.get('last_attempt', 0)) < config['cooldown']:
            return 'cooldown'
    if system.driver(config['primary']) != 'brcmfmac':
        return 'primary driver is not brcmfmac'
    if not system.only_primary_uses_module(config['primary']):
        return 'another interface shares the Wi-Fi module'
    if not system.fault():
        return 'no recent SDIO halt'
    if system.probe(config['primary']):
        return 'primary is healthy'
    if system.route() != config['backup'] or not system.probe(config['backup']):
        return 'healthy backup route not verified'
    return 'recover'


def recover(config, state, boot, now, system, save):
    decision = decide(config, state, boot, now, system)
    if decision != 'recover':
        return decision
    # Recheck immediately before disruption; do not trust saved watchdog state.
    if system.route() != config['backup'] or not system.probe(config['backup']):
        return 'backup changed; recovery withheld'
    attempt = dict(boot_id=boot, attempts=(int(state.get('attempts', 0)) if state.get('boot_id') == boot else 0) + 1,
                   last_attempt=now, result='attempting')
    save(attempt)  # Reserve the attempt before invoking modprobe, even if it fails.
    try:
        system.reload()
    except Exception:
        attempt['result'] = 'failed'
        save(attempt)
        raise
    attempt['result'] = 'driver reloaded; connectivity not yet verified'
    save(attempt)
    return attempt['result']


def save_state(path, state):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.attempt-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    if os.geteuid() != 0:
        raise SystemExit('run only as the root-managed service')
    config = read_config()
    STATE.mkdir(mode=0o700, exist_ok=True)
    meta = STATE.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
        raise ValueError('untrusted recovery state directory')
    fd = os.open(STATE / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        path = STATE / 'attempt.json'
        state = {}
        if path.exists() or path.is_symlink():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd) as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    raise ValueError('invalid recovery state')
                state = json.load(f)
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        result = recover(config, state, boot, time.time(), System(), lambda s: save_state(path, s))
        print('wifi-sdio-recovery:', result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
