"""NET_FAILOVER_PARKED (2026-11-01): a parked watchdog never probes or reroutes; a park
that lands while failed over restores the backup once. Stubbed tools; never touches networking."""
from pathlib import Path
import os
import subprocess

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'scripts/net-failover-watchdog.sh'


def run_watchdog(tmp_path, *, parked, failed_over):
    bindir = tmp_path / 'bin'; bindir.mkdir()
    (tmp_path / 'net/backup0').mkdir(parents=True)
    (tmp_path / 'net/backup0/carrier').write_text('1')
    calls = tmp_path / 'calls'; calls.write_text('')
    for tool in ('ping', 'curl'):
        (bindir / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >>"$CALLS"\nexit 1\n')
    (bindir / 'nmcli').write_text('''#!/bin/sh
echo "nmcli $*" >>"$CALLS"
case "$*" in
  '-g GENERAL.STATE device show backup0') echo '100 (connected)' ;;
  '-g GENERAL.CON-UUID device show backup0') echo fixture-uuid ;;
  '-g connection.uuid connection show fixture-backup') echo fixture-uuid ;;
esac
''')
    (bindir / 'ip').write_text('#!/bin/sh\nprintf "1.1.1.1 via 192.0.2.1 dev primary0 src 192.0.2.2\\n"\n')
    (bindir / 'sleep').write_text('#!/bin/sh\nexit 0\n')
    for p in bindir.iterdir():
        p.chmod(0o755)
    state = tmp_path / 'wd-state'
    state.write_text(f'failed_over={failed_over}\nsaved_backup_metric=600\nlast_alert_epoch=9999999999\n')
    (tmp_path / 'run').mkdir()
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'], CALLS=str(calls),
               XDG_RUNTIME_DIR=str(tmp_path / 'run'),
               NET_FAILOVER_STATE_FILE=str(state), NET_FAILOVER_LOG_FILE=str(tmp_path / 'log'),
               NET_FAILOVER_PRIMARY_IF='primary0', NET_FAILOVER_BACKUP_IF='backup0',
               NET_FAILOVER_BACKUP_CON='fixture-backup', NET_FAILOVER_PRIMARY_CON='fixture-primary',
               NET_FAILOVER_SYS_NET_DIR=str(tmp_path / 'net'),
               NET_FAILOVER_PARKED='true' if parked else 'false')
    p = subprocess.run(['bash', str(SCRIPT)], text=True, capture_output=True, env=env, timeout=60)
    return p, calls.read_text(), state.read_text()


def test_parked_watchdog_never_probes_or_touches_routes(tmp_path):
    p, calls, state = run_watchdog(tmp_path, parked=True, failed_over=0)
    assert p.returncode == 0, p.stdout + p.stderr
    assert calls == ''
    assert 'failed_over=0' in state


def test_park_while_failed_over_restores_the_backup_once(tmp_path):
    p, calls, state = run_watchdog(tmp_path, parked=True, failed_over=1)
    assert p.returncode == 0, p.stdout + p.stderr
    assert 'ping' not in calls and 'curl' not in calls
    assert 'nmcli' in calls
    assert 'failed_over=0' in state


def test_unparked_watchdog_still_probes(tmp_path):
    p, calls, _ = run_watchdog(tmp_path, parked=False, failed_over=0)
    assert p.returncode == 0, p.stdout + p.stderr
    assert 'ping' in calls
