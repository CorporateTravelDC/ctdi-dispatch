#!/usr/bin/env python3
"""Deliver signed-source pamphlets to activated local accounts.

Root handles only directory-safe Markdown installation. Rendering and manifest
verification run as the operator, never as root. Cloud/service identities keep
their existing vault delivery. Account selection never depends on a vendor name.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import time

REPO = Path('/opt/corporatetraveldc/private/ctdi-dispatch-internal')
REGISTRY = Path('/etc/ctdc-accounts.conf')
GRANTS = Path('/etc/ctdc-skill-grants.conf')
LOCAL_SERVICES = Path('/etc/ctdc-pamphlet-local-services.conf')
ROOT = Path('/var/lib/ctdc-agent-pamphlets')
OPERATOR = 'corporatetraveldc'
LIMIT = 1024 * 1024


def trusted_text(path, owner_uid=0, limit=LIMIT):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'r') as f:
        meta = os.fstat(f.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != owner_uid or meta.st_mode & 0o022:
            raise ValueError('untrusted input: ' + str(path))
        text = f.read(limit + 1)
    if len(text.encode()) > limit:
        raise ValueError('oversize input')
    return text


def declared_local_services(text):
    names = [line.split('#', 1)[0].strip() for line in text.splitlines()]
    names = [name for name in names if name]
    if len(set(names)) != len(names) or any(not re.fullmatch(r'ctdc-[a-z0-9-]+', name) for name in names):
        raise ValueError('invalid local-service delivery policy')
    return set(names)


def local_service_policy():
    return trusted_text(LOCAL_SERVICES) if LOCAL_SERVICES.exists() or LOCAL_SERVICES.is_symlink() else ''


def local_accounts(text, lookup=None, explicit_local=()):
    lookup = lookup or pwd.getpwnam
    explicit_local = set(explicit_local)
    seen, result = set(), []
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        row = line.split()
        if len(row) != 4:
            raise ValueError('registry row must have four fields')
        name, kind, login, preloaded = row
        if name in seen:
            raise ValueError('duplicate registry account')
        seen.add(name)
        if preloaded not in ('0', '1'):
            raise ValueError('invalid preload flag')
        # Unknown future interactive clients can use the existing ssh mode;
        # delivery is selected by role/login capability, not vendor/product.
        declared_service = name in explicit_local and kind == 'service' and login == 'none'
        if name in explicit_local and not declared_service:
            raise ValueError('local-service policy must name a registered service identity')
        if preloaded != '0' or not (declared_service or (kind == 'agent' and login != 'none')):
            continue
        if not re.fullmatch(r'ctdc-[a-z0-9-]+', name):
            raise ValueError('invalid local agent name')
        user = lookup(name)
        if (user.pw_uid == 0 or user.pw_dir == '/'
                or (not declared_service and user.pw_shell.endswith(('/nologin', '/false')))):
            raise ValueError('interactive account has an invalid home or shell')
        result.append((name, login, user))
    if explicit_local - seen:
        raise ValueError('local-service policy names an unregistered identity')
    return result


def open_directory(path, expected_uid):
    """Walk all components without following account-created symlinks."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or str(path) == '/':
        raise ValueError('invalid directory')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        if os.fstat(fd).st_uid != expected_uid:
            raise ValueError('unexpected directory owner')
        return fd
    except BaseException:
        os.close(fd)
        raise


def check_target(fd, name, account, owner_uid=0):
    try:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return
    with os.fdopen(child) as f:
        meta = os.fstat(f.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != owner_uid or meta.st_mode & 0o022:
            raise ValueError('refusing to replace an unmanaged target')
        prefix = f.read(8192)
        if account not in prefix or 'onboarding pamphlet' not in prefix:
            raise ValueError('refusing to replace unrelated native instructions')


def atomic_markdown(fd, name, data, gid, account, owner_uid=0):
    check_target(fd, name, account, owner_uid)
    temporary = '.pamphlet-' + os.urandom(12).hex()
    child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600, dir_fd=fd)
    try:
        with os.fdopen(child, 'wb') as f:
            f.write(data)
            os.fchown(f.fileno(), owner_uid, gid)
            os.fchmod(f.fileno(), 0o444)
            f.flush()
            os.fsync(f.fileno())
        # Atomic replacement never follows a target symlink, including a race.
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


def output_matches(fd, name, digest, owner_uid=0):
    try:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, 'rb') as f:
            meta = os.fstat(f.fileno())
            return (stat.S_ISREG(meta.st_mode) and meta.st_uid == owner_uid
                    and stat.S_IMODE(meta.st_mode) == 0o444
                    and hashlib.sha256(f.read(LIMIT + 1)).hexdigest() == digest)
    except (OSError, ValueError):
        return False


def targets(user):
    homefd = open_directory(user.pw_dir, user.pw_uid)
    result = [(homefd, ['PAMPHLET.md', 'CLAUDE.md', 'AGENTS.md'])]
    try:
        # Do not create a client configuration directory or invent a Grok hook.
        try:
            meta = os.stat('.codex', dir_fd=homefd, follow_symlinks=False)
        except FileNotFoundError:
            meta = None
        if meta is not None:
            if not stat.S_ISDIR(meta.st_mode) or meta.st_uid not in (0, user.pw_uid):
                raise ValueError('unsafe client directory')
            result.append((os.open('.codex', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=homefd), ['AGENTS.md']))
        return result
    except BaseException:
        for fd, _ in result:
            os.close(fd)
        raise


def render(account, operator):
    r = subprocess.run(['runuser', '-u', operator.pw_name, '--', 'env',
                        'XDG_RUNTIME_DIR=/run/user/' + str(operator.pw_uid),
                        'bash', str(REPO / 'scripts/agent-segmentation/render-onboarding.sh'), account],
                       capture_output=True, timeout=90, check=False)
    if r.returncode:
        raise RuntimeError('unprivileged pamphlet rendering failed')
    data = r.stdout
    if (not data or len(data) > LIMIT or b'{{' in data
            or account.encode() not in data[:8192] or b'onboarding pamphlet' not in data[:8192]):
        raise ValueError('invalid rendered pamphlet')
    return data


def save_receipt(path, data):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.receipt-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def deliver(account, login, user, operator, inputs):
    mirror = ROOT / account
    mirror.mkdir(mode=0o750, exist_ok=True)
    mfd = open_directory(mirror, 0)
    os.fchown(mfd, 0, operator.pw_gid)
    os.fchmod(mfd, 0o750)
    opened = []
    try:
        opened = targets(user)
        all_targets = opened + [(mfd, ['PAMPHLET.md'])]
        for fd, names in all_targets:
            for name in names:
                check_target(fd, name, account)
        receipt_path = mirror / 'receipt.json'
        key = dict(inputs, uid=user.pw_uid, gid=user.pw_gid, home=user.pw_dir,
                   login=login, targets=[names for _, names in opened])
        receipt = {}
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt = json.loads(trusted_text(receipt_path))
        if receipt.get('inputs') == key and all(
                output_matches(fd, name, receipt.get('document_sha256'))
                for fd, names in all_targets for name in names):
            receipt['checked_at'] = time.time()
            save_receipt(receipt_path, receipt)
            os.chown(receipt_path, 0, operator.pw_gid)
            os.chmod(receipt_path, 0o440)
            print(account + ': current and read-back verified')
            return
        data = render(account, operator)
        for fd, names in all_targets:
            gid = operator.pw_gid if fd == mfd else user.pw_gid
            for name in names:
                atomic_markdown(fd, name, data, gid, account)
        digest = hashlib.sha256(data).hexdigest()
        if not all(output_matches(fd, name, digest) for fd, names in all_targets for name in names):
            raise RuntimeError('pamphlet readback mismatch')
        save_receipt(receipt_path, dict(account=account, inputs=key, document_sha256=digest,
                                       delivered_at=time.time(), checked_at=time.time()))
        os.chown(receipt_path, 0, operator.pw_gid)
        os.chmod(receipt_path, 0o440)
        print(account + ': installed and read-back verified')
    finally:
        for fd, _ in opened:
            os.close(fd)
        os.close(mfd)


def input_digests(registry, grants, local_services=''):
    operator = pwd.getpwnam(OPERATOR)
    manifest = trusted_text(REPO / 'MANIFEST.sha256', owner_uid=operator.pw_uid)
    return dict(manifest_sha256=hashlib.sha256(manifest.encode()).hexdigest(),
                registry_sha256=hashlib.sha256(registry.encode()).hexdigest(),
                grants_sha256=hashlib.sha256(grants.encode()).hexdigest(),
                local_services_sha256=hashlib.sha256(local_services.encode()).hexdigest())


def check_delivery(since=0):
    """Operator-readable receipts; never read another agent's private home."""
    registry, grants = trusted_text(REGISTRY), trusted_text(GRANTS)
    policy = local_service_policy()
    inputs = input_digests(registry, grants, policy)
    for account, login, user in local_accounts(registry, explicit_local=declared_local_services(policy)):
        mirror = ROOT / account
        fd = open_directory(mirror, 0)
        try:
            receipt = json.loads(trusted_text(mirror / 'receipt.json'))
            actual = receipt.get('inputs', {})
            if (any(actual.get(k) != v for k, v in inputs.items())
                    or actual.get('uid') != user.pw_uid or actual.get('home') != user.pw_dir
                    or actual.get('gid') != user.pw_gid or actual.get('login') != login
                    or float(receipt.get('checked_at', 0)) < since
                    or not output_matches(fd, 'PAMPHLET.md', receipt.get('document_sha256'))):
                return False
        finally:
            os.close(fd)
    return True


def ensure_request_marker(fd, gid, owner_uid=0):
    try:
        requestfd = os.open('refresh-request', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        requestfd = os.open('refresh-request', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                            0o660, dir_fd=fd)
    # Existing markers are read-only; a write-open/close retriggers PathChanged.
    try:
        meta = os.fstat(requestfd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != owner_uid:
            raise ValueError('untrusted refresh request marker')
        if meta.st_gid != gid:
            os.fchown(requestfd, owner_uid, gid)
        if stat.S_IMODE(meta.st_mode) != 0o660:
            os.fchmod(requestfd, 0o660)
    finally:
        os.close(requestfd)


def main():
    if sys.argv[1:2] == ['--check']:
        if len(sys.argv) > 3:
            raise SystemExit('usage: --check [minimum-check-epoch]')
        return 0 if check_delivery(float(sys.argv[2]) if len(sys.argv) == 3 else 0) else 1
    if len(sys.argv) != 1:
        raise SystemExit('no install arguments accepted')
    if os.geteuid() != 0:
        raise SystemExit('run only as the root-managed installer')
    operator = pwd.getpwnam(OPERATOR)
    registry = trusted_text(REGISTRY)
    grants = trusted_text(GRANTS)
    policy = local_service_policy()
    accounts = local_accounts(registry, explicit_local=declared_local_services(policy))
    r = subprocess.run(['runuser', '-u', OPERATOR, '--', 'bash', str(REPO / 'scripts/verify-manifest.sh')],
                       capture_output=True, timeout=120, check=False)
    if r.returncode:
        raise RuntimeError('unsigned or divergent source; delivery held')
    manifest = trusted_text(REPO / 'MANIFEST.sha256', owner_uid=operator.pw_uid)
    expected = next((line.split()[0] for line in manifest.splitlines()
                     if line.split()[-1:] == ['scripts/agent-pamphlet-install.py']), None)
    ownfd = os.open(__file__, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(ownfd, 'rb') as own:
        if hashlib.sha256(own.read()).hexdigest() != expected:
            raise RuntimeError('installed delivery helper differs from signed source; install root copies first')
    # The operator source can render text, but never executes with root rights.
    inputs = input_digests(registry, grants, policy)
    ROOT.mkdir(mode=0o750, exist_ok=True)
    fd = open_directory(ROOT, 0)
    try:
        os.fchown(fd, 0, operator.pw_gid)
        os.fchmod(fd, 0o750)
        ensure_request_marker(fd, operator.pw_gid)
        lockfd = os.open('delivery.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(lockfd, 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            failed = False
            # Catch a deployment request arriving while an earlier pass is active.
            for _ in range(2):
                started = time.time()
                for account, login, user in accounts:
                    try:
                        deliver(account, login, user, operator, inputs)
                    except Exception as e:
                        print(account + ': delivery failed (' + type(e).__name__ + ')')
                        failed = True
                if os.stat('refresh-request', dir_fd=fd, follow_symlinks=False).st_mtime <= started:
                    break
            return int(failed)
    finally:
        os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
