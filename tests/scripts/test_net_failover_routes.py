"""Exercise live-route transitions through stubbed nmcli/ip; never touch networking."""
from pathlib import Path
import os
import subprocess
import pytest

REPO = Path(__file__).resolve().parents[2]


def run_case(tmp_path, *, mode='failback', active=True, carrier='0', route='primary0', reapply=0, wrong_profile=False):
    bindir = tmp_path / 'bin'; bindir.mkdir()
    sysnet = tmp_path / 'net/backup0'; sysnet.mkdir(parents=True)
    (sysnet / 'carrier').write_text(carrier)
    log = tmp_path / 'calls'
    (bindir / 'nmcli').write_text('''#!/bin/sh
echo "$*" >>"$CALLS"
case "$*" in
  '-g GENERAL.STATE device show backup0') echo "$NM_STATE" ;;
  '-g GENERAL.CON-UUID device show backup0') echo "$ACTIVE_UUID" ;;
  '-g connection.uuid connection show fixture-backup') echo fixture-uuid ;;
  'device reapply backup0') exit "$REAPPLY_RC" ;;
esac
''')
    (bindir / 'ip').write_text('#!/bin/sh\nprintf "1.1.1.1 via 192.0.2.1 dev %s src 192.0.2.2\\n" "$ROUTE_IF"\n')
    (bindir / 'sleep').write_text('#!/bin/sh\nexit 0\n')
    for p in bindir.iterdir(): p.chmod(0o755)
    script = '''source "$LIB"
log() { echo "$*"; }
BACKUP_CON=fixture-backup; BACKUP_IF=backup0; PRIMARY_IF=primary0
apply_backup_metric 600 "$MODE"
'''
    env = dict(os.environ, PATH=str(bindir)+os.pathsep+os.environ['PATH'],
               LIB=str(REPO/'scripts/lib/net_failover_routes.sh'), CALLS=str(log),
               NM_STATE='100 (connected)' if active else '30 (disconnected)',
               REAPPLY_RC=str(reapply), ROUTE_IF=route, MODE=mode,
               ACTIVE_UUID='other-fixture-uuid' if wrong_profile else 'fixture-uuid',
               NET_FAILOVER_SYS_NET_DIR=str(tmp_path/'net'))
    p = subprocess.run(['bash'], input=script, text=True, capture_output=True, env=env, timeout=10)
    return p, log.read_text()


def test_failback_reapplies_active_backup_even_when_carrier_file_says_zero(tmp_path):
    p, calls = run_case(tmp_path)
    assert p.returncode == 0, p.stdout+p.stderr
    assert 'device reapply backup0' in calls and 'connection up' not in calls


def test_failed_live_reapply_is_not_claimed_as_success_or_bounced(tmp_path):
    p, calls = run_case(tmp_path, reapply=1)
    assert p.returncode != 0
    assert 'connection up' not in calls


def test_persisted_profile_alone_does_not_confirm_failback(tmp_path):
    p, _ = run_case(tmp_path, route='backup0')
    assert p.returncode != 0 and 'route verification failed' in p.stdout


def test_unplugged_backup_can_restore_profile_when_primary_is_actual_route(tmp_path):
    p, calls = run_case(tmp_path, active=False)
    assert p.returncode == 0
    assert 'connection up' not in calls and 'device reapply' not in calls


def test_unplugged_profile_update_cannot_hide_wrong_route(tmp_path):
    p, _ = run_case(tmp_path, active=False, route='backup0')
    assert p.returncode != 0


def test_failover_activates_inactive_backup_and_verifies_route(tmp_path):
    p, calls = run_case(tmp_path, mode='', active=False, route='backup0')
    assert p.returncode == 0 and 'connection up fixture-backup' in calls


def test_active_backup_failover_does_not_disconnect(tmp_path):
    p, calls = run_case(tmp_path, mode='', route='backup0')
    assert p.returncode == 0 and 'device reapply backup0' in calls


def test_different_active_profile_cannot_modify_the_wrong_saved_profile(tmp_path):
    p,calls=run_case(tmp_path,wrong_profile=True)
    assert p.returncode != 0 and 'profile differs' in p.stdout
    assert 'connection modify' not in calls and 'device reapply' not in calls


def test_watchdog_uses_verified_transition_helper():
    text = (REPO/'scripts/net-failover-watchdog.sh').read_text()
    assert '/lib/net_failover_routes.sh' in text
    assert 'if apply_backup_metric "${saved}" failback; then\n        state_set failed_over 0' in text
