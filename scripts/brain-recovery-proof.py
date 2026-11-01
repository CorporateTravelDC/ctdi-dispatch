#!/usr/bin/env python3
"""Operator-only compiler execution and acceptance receipts; never signs or pushes."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from common.brain_recovery import ProofFailed, semantic_passed, graph_passed

SEM = 'corporatetraveldc-semantic-compile-daily'
GRAPH = 'corporatetraveldc-knowledge-graph-compile'
TRIGGERS = [SEM + '.path', GRAPH + '.path', SEM + '.timer', GRAPH + '.timer']
UNIT_ROOT = Path('/home/corporatetraveldc/.config/containers/systemd')


def command(args, *, timeout=30):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ProofFailed('operator command exceeded its bounded wait') from exc
    if out.returncode:
        raise ProofFailed('operator command failed: ' + Path(args[0]).name)
    return out.stdout.strip()


def save(path, value, mode=0o600):
    fd, name = tempfile.mkstemp(prefix='.proof-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            os.fchmod(handle.fileno(), mode)
            json.dump(value, handle, indent=2, default=str)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def progress(phase):
    root = Path('/srv/ctdc-context')
    if root.is_dir() and not root.is_symlink() and root.stat().st_uid == os.getuid():
        save(root / 'RELEASE-PROGRESS.json', {'status': 'running', 'phase': phase,
             'at': datetime.now(timezone.utc).isoformat(), 'exit_code': 0}, mode=0o640)


def unit(name):
    text = command(['systemctl', '--user', 'show', name + '.service', '-p',
                    'ActiveState,SubState,Result,ExecMainStatus,ExecMainStartTimestampMonotonic,InvocationID'])
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def semantic():
    import psycopg
    from common import db_backend
    with psycopg.connect(db_backend._pg_conninfo(), host='127.0.0.1', connect_timeout=8,
            autocommit=True, options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000') as conn:
        keys = ['compile_seq', 'compiled_at', 'assignments', 'derivations',
                'geometry_edges', 'causal_edges', 'cluster_edges']
        data = dict(conn.execute('SELECT key,value FROM semantic_meta WHERE key=ANY(%s)', (keys,)).fetchall())
        for key in keys:
            if key != 'compiled_at':
                data[key] = int(data.get(key, 0))
        row = conn.execute('SELECT ok,compile_seq FROM semantic_compile_runs '
                           'WHERE ok IS TRUE ORDER BY compile_seq DESC LIMIT 1').fetchone()
        data.update(ledger_ok=bool(row and row[0]), ledger_compile_seq=int(row[1]) if row else 0)
        return data


def graph():
    path = Path('/var/lib/corporatetraveldc/knowledge_graph/graph.json')
    with path.open('rb') as handle:
        raw = handle.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise ProofFailed('served graph exceeds bounded proof-reader size')
    data = json.loads(raw)
    meta = data['meta']
    keys = ['generated_at', 'node_count', 'edge_count', 'semantic_compile_seq',
            'semantic_compiled_at', 'semantic_note_links']
    out = {key: meta.get(key) for key in keys}
    actual_links = sum(edge.get('type') == 'concept_tag' for edge in data.get('edges', []))
    if out.get('semantic_note_links') is not None and actual_links != out['semantic_note_links']:
        raise ProofFailed('graph semantic link metadata disagrees with its actual edges')
    out['actual_semantic_note_links'] = actual_links
    out['sha256'] = hashlib.sha256(raw).hexdigest()
    return out


def run_unit(name, image, image_id):
    if unit(name).get('ActiveState') in ('active', 'activating', 'deactivating'):
        raise ProofFailed('compiler already active; no concurrent run started')
    # Inspect only in memory; never emit the generated command or environment.
    exec_text = command(['systemctl', '--user', 'show', name + '.service', '-p', 'ExecStart', '--value'])
    if image not in exec_text:
        raise ProofFailed('generated compiler unit does not select the reviewed validation image')
    if command(['podman', 'image', 'inspect', '--format', '{{.Id}}', image]) != image_id:
        raise ProofFailed('validation image tag changed')
    started = datetime.now(timezone.utc)
    mono = time.monotonic()
    command(['systemctl', '--user', 'start', name + '.service'], timeout=1900)
    result = unit(name)
    unit_mono = int(result.get('ExecMainStartTimestampMonotonic') or 0) / 1_000_000
    if unit_mono < mono:
        raise ProofFailed('compiler did not start a new invocation')
    result['started_at'] = (started + timedelta(seconds=unit_mono - mono)).isoformat()
    if command(['podman', 'image', 'inspect', '--format', '{{.Id}}', image]) != image_id:
        raise ProofFailed('validation image tag changed during the compile')
    return result, started.replace(microsecond=0).isoformat()


def resume(state):
    recorded = json.loads((state / 'brain-trigger-state.json').read_text())
    if set(recorded) != set(TRIGGERS) or any(type(v) is not bool for v in recorded.values()):
        raise ProofFailed('compiler trigger receipt is invalid')
    for name in (SEM, GRAPH):
        if unit(name).get('ActiveState') in ('active', 'activating', 'deactivating'):
            raise ProofFailed('compiler is still active; scheduling was not changed')
    active = [name for name in TRIGGERS if recorded[name]]
    if active:
        command(['systemctl', '--user', 'start', *active], timeout=3800)
    print(json.dumps({'compiler_triggers_restored': True, 'active': active}))


def prove(state, image):
    if not re.fullmatch(r'localhost/[a-z0-9-]+:[a-z0-9-]+', image):
        raise ProofFailed('validation image name is invalid')
    for name in (SEM, GRAPH):
        if unit(name).get('ActiveState') in ('active', 'activating', 'deactivating'):
            raise ProofFailed('compiler already active; wait before validation')
    before = {'semantic': semantic(), 'graph': graph()}
    save(state / 'brain-before.json', before)
    trigger_path = state / 'brain-trigger-state.json'
    if not trigger_path.exists():
        active = {name: subprocess.run(['systemctl', '--user', 'is-active', '--quiet', name],
                                       capture_output=True).returncode == 0 for name in TRIGGERS}
        save(trigger_path, active)
    command(['systemctl', '--user', 'stop', *TRIGGERS])
    image_id = command(['podman', 'image', 'inspect', '--format', '{{.Id}}', image])
    body = '[Container]\nImage=' + image + '\n'
    overrides = []
    try:
        for name in (SEM, GRAPH):
            parent = UNIT_ROOT / (name + '.container.d')
            if parent.is_symlink():
                raise ProofFailed('validation override directory must not be a symlink')
            parent.mkdir(exist_ok=True)
            if parent.stat().st_uid != os.getuid():
                raise ProofFailed('validation override directory is not operator-owned')
            target = parent / '90-brain-validation.conf'
            if target.is_symlink() or (target.exists() and target.read_text() != body):
                raise ProofFailed('existing validation override differs; preserved')
            target.write_text(body)
            overrides.append(target)
        command(['systemctl', '--user', 'daemon-reload'])
        progress('semantic-compiler-running')
        sem_unit, sem_start = run_unit(SEM, image, image_id)
        sem = semantic()
        semantic_passed(before['semantic'], sem, sem_unit, sem_start)
        progress('semantic-compiler-passed')
        shared_root = Path('/srv/ctdc-context')
        if shared_root.is_dir() and not shared_root.is_symlink() and shared_root.stat().st_uid == os.getuid():
            save(shared_root / 'BRAIN-SEMANTIC-RECEIPT.json', {'ok': True, 'semantic': sem,
                 'source_commit': command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']),
                 'poller_image_id': image_id, 'verified_at': datetime.now(timezone.utc).isoformat()}, mode=0o640)
        progress('graph-compiler-running')
        graph_unit, graph_start = run_unit(GRAPH, image, image_id)
        fresh = graph()
        graph_passed(before['graph'], fresh, sem, graph_unit, graph_start)
        if semantic()['compile_seq'] != sem['compile_seq']:
            raise ProofFailed('another semantic compile intervened; repeat coherent validation')
        proof = {'ok': True, 'source_commit': command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']),
                 'poller_image_ref': image,
                 'poller_image_id': image_id, 'semantic': sem, 'graph': fresh,
                 'semantic_unit': sem_unit, 'graph_unit': graph_unit,
                 'verified_at': datetime.now(timezone.utc).isoformat(),
                 'compiler_triggers_paused_until_promotion': True}
        save(state / 'brain-proof.json', proof)
        progress('both-compilers-passed')
        print(json.dumps(proof, indent=2))
    finally:
        # A stuck active service keeps its exact validation definition for
        # inspection; never kill it or silently restore automatic triggers.
        busy = any(unit(name).get('ActiveState') in ('active', 'activating', 'deactivating')
                   for name in (SEM, GRAPH))
        if not busy:
            for target in overrides:
                if not target.is_symlink() and target.read_text() == body:
                    target.unlink()
            command(['systemctl', '--user', 'daemon-reload'])


def shared_proof(state):
    from common.platform_context import RESOURCE, read_context
    local = read_context(root=ROOT)
    key = os.environ.get('BOARD_KEY', '')
    if not key:
        raise ProofFailed('operator board-read credential is unavailable')
    url = 'http://100.x.x.x:8000/api/v1/vault/research?path=' + RESOURCE
    request = urllib.request.Request(url, headers={'X-Board-Key': key})
    for attempt in range(15):
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                remote = json.loads(response.read((1 << 20) + 8192))
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 503 or attempt == 14:
                raise
        except urllib.error.URLError:
            if attempt == 14:
                raise
        time.sleep(2)
    if remote.get('sha256') != local['sha256'] or remote.get('content') != local['content']:
        raise ProofFailed('connector/API context differs from installed bytes')
    hook = json.loads(command(['python3', '-B', str(ROOT / 'scripts/platform-context.py'), 'hook']))
    if hook.get('hookSpecificOutput', {}).get('additionalContext') != local['content']:
        raise ProofFailed('native hook context differs from installed bytes')
    if (ROOT / 'CLAUDE.md').exists():
        raise ProofFailed('retired repo CLAUDE.md still exists')
    if str(local['path']) not in (ROOT / 'AGENTS.md').read_text():
        raise ProofFailed('Codex adapter does not point at the shared file')
    try:
        with urllib.request.urlopen(url, timeout=10):
            raise ProofFailed('shared context endpoint allowed an unauthenticated read')
    except urllib.error.HTTPError as exc:
        if exc.code not in (401, 403):
            raise ProofFailed('unauthenticated shared-context response was unexpected')
    proof = {'ok': True, 'source_commit': command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']),
             'context_sha256': local['sha256'], 'api_and_native_hook_match': True,
             'repo_claude_removed': True, 'verified_at': datetime.now(timezone.utc).isoformat()}
    save(state / 'shared-context-proof.json', proof)
    print(json.dumps(proof, indent=2))


def verify_current(state):
    proof = json.loads((state / 'brain-proof.json').read_text())
    before = json.loads((state / 'brain-before.json').read_text())
    shared = json.loads((state / 'shared-context-proof.json').read_text())
    head = command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'])
    if not proof.get('ok') or not shared.get('ok') or any(p.get('source_commit') != head for p in [proof, shared]):
        raise ProofFailed('recovery/context receipts do not match the signed release commit')
    age = datetime.now(timezone.utc) - datetime.fromisoformat(proof['verified_at'])
    if not 0 <= age.total_seconds() <= 7200:
        raise ProofFailed('compiler proof is outside the two-hour release window; rerun validation')
    def current(name, reference):
        out = unit(name)
        observed = int(out.get('ExecMainStartTimestampMonotonic') or 0)
        previous = int(reference.get('ExecMainStartTimestampMonotonic') or 0)
        if observed < previous:
            raise ProofFailed('current unit invocation predates the recorded proof')
        out['started_at'] = reference['started_at'] if observed == previous else (
            datetime.now(timezone.utc) + timedelta(seconds=observed / 1_000_000 - time.monotonic())).isoformat()
        return out
    sem = semantic()
    sem_unit = current(SEM, proof['semantic_unit'])
    semantic_passed(before['semantic'], sem, sem_unit, proof['semantic_unit']['started_at'])
    fresh = graph()
    graph_unit = current(GRAPH, proof['graph_unit'])
    graph_passed(before['graph'], fresh, sem, graph_unit, proof['graph_unit']['started_at'])
    from common.platform_context import CONTEXT_PATH, read_context
    local = read_context(root=ROOT)
    if local['sha256'] != shared.get('context_sha256'):
        raise ProofFailed('shared context changed after its parity proof')
    final = {'ok': True, 'source_commit': head, 'poller_image_id': proof['poller_image_id'],
             'semantic': sem, 'graph': fresh, 'context_sha256': local['sha256'],
             'api_and_native_hook_match': True, 'repo_claude_removed': True,
             'semantic_unit_result': sem_unit.get('Result'), 'graph_unit_result': graph_unit.get('Result'),
             'verified_at': datetime.now(timezone.utc).isoformat(),
             'scope': 'Observed successful compiler runs, committed sequence, served graph revision and shared-reader parity'}
    save(state / 'release-proof.json', final)
    save(CONTEXT_PATH.parent / 'BRAIN-RECOVERY-RECEIPT.json', final, mode=0o640)
    print(json.dumps(final, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['prove', 'resume-triggers', 'shared', 'verify'])
    ap.add_argument('--state', required=True, type=Path)
    ap.add_argument('--image')
    args = ap.parse_args()
    if os.getuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'corporatetraveldc':
        print('Run in the corporatetraveldc operator terminal; no actions performed.')
        return 77
    valid_state = False
    try:
        if args.state.is_symlink() or not args.state.is_dir():
            raise ProofFailed('operator state directory unavailable')
        st = args.state.stat()
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ProofFailed('operator state directory must be private and operator-owned')
        valid_state = True
        if args.action == 'prove':
            if not args.image:
                raise ProofFailed('reviewed validation image is required')
            prove(args.state, args.image)
        elif args.action == 'resume-triggers':
            resume(args.state)
        elif args.action == 'verify':
            verify_current(args.state)
        else:
            shared_proof(args.state)
        return 0
    except Exception as exc:
        out = {'ok': False, 'error_type': type(exc).__name__,
               'reason': str(exc) if isinstance(exc, ProofFailed) else 'read-only metadata or runtime action failed; inspect operator logs',
               'compiler_triggers_may_be_paused': (args.state / 'brain-trigger-state.json').exists()}
        if isinstance(exc, urllib.error.HTTPError):
            out['http_status'] = exc.code
        if getattr(exc, 'sqlstate', None):
            out['sqlstate'] = exc.sqlstate
        for name in (SEM, GRAPH):
            try:
                out[name] = {k: v for k, v in unit(name).items()
                             if k in ('ActiveState', 'SubState', 'Result', 'ExecMainStatus')}
            except Exception:
                pass
        if valid_state:
            save(args.state / 'brain-recovery-failure.json', out)
        shared = Path('/srv/ctdc-context')
        if shared.is_dir() and not shared.is_symlink() and shared.stat().st_uid == os.getuid():
            save(shared / 'BRAIN-RECOVERY-FAILURE.json', out, mode=0o640)
        print(json.dumps(out, indent=2))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
