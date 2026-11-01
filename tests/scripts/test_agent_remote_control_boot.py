import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('agent_boot', ROOT / 'scripts/agent-remote-control-boot.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NAME = 'ctdc-agent-openai-codex'
UNIT = m.UNITS['codex']


class FakeSystem:
    def __init__(self, *, masked=False, missing=False, fail=None, ready=True):
        self.calls, self.masked, self.missing, self.fail = [], masked, missing, fail
        self.ready = ready
        self.did_enable = False
        self.is_expired = self.is_locked = False

    def run(self, args):
        self.calls.append(tuple(args))
        return SimpleNamespace(returncode=int(self.fail == args[1]), stdout='yes\n')

    def properties(self, user, unit):
        self.calls.append(('properties', user.pw_name, unit))
        return dict(LoadState='masked' if self.masked else 'not-found' if self.missing else 'loaded',
                    ActiveState='active' if self.did_enable and self.ready else 'inactive',
                    UnitFileState='masked' if self.masked else 'enabled' if self.did_enable else 'disabled')

    def userctl(self, user, args):
        self.calls.append(('userctl', user.pw_name, *args))
        self.did_enable = True
        return SimpleNamespace(returncode=int(self.fail == 'enable'), stdout='')

    def expired(self, name):
        return self.is_expired

    def locked(self, name):
        return self.is_locked


class BootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inert = patch.object(m, 'INERT', Path(self.tmp.name))
        self.inert.start()
        self.addCleanup(self.inert.stop)
        self.row = dict(account=NAME, mode='codex', preloaded=False)
        self.user = SimpleNamespace(pw_name=NAME, pw_uid=1003, pw_shell='/bin/bash')

    def call(self, s, **kwargs):
        return m.reconcile(self.row, s, lookup=lambda _: self.user,
                           policy_reader=kwargs.pop('policy_reader', lambda: (False, set())), **kwargs)

    def test_registry_selects_only_local_interactive_agents(self):
        rows = m.registry(f'{NAME} agent codex 0\nctdc-agent-anthropic-claude agent claude 0\n'
                          'ctdc-agent-anthropic-cowork service none 0\n'
                          'ctdc-agent-ssh agent ssh 0\n')
        self.assertEqual([r['mode'] for r in rows], ['codex', 'claude'])

    def test_bad_registry_fails_closed(self):
        for text in (f'{NAME} agent codex 2', f'{NAME} agent codex 0\n{NAME} agent codex 0',
                     'root agent codex 0', 'broken'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                m.registry(text)

    def test_restore_enablement_and_start(self):
        s = FakeSystem()
        self.assertEqual(self.call(s)['status'], 'ready')
        self.assertIn(('/usr/bin/loginctl','enable-linger',NAME), s.calls)
        self.assertIn(('/usr/bin/systemctl','start','user@1003.service'), s.calls)
        self.assertIn(('userctl',NAME,'enable','--now',UNIT), s.calls)
        self.assertFalse(any('restart' in a or 'unmask' in a or 'stop' in a for a in s.calls))

    def test_repeated_run_does_not_restart(self):
        s = FakeSystem()
        self.call(s)
        self.call(s)
        self.assertEqual(sum(c[:2] == ('userctl', NAME) for c in s.calls), 2)
        self.assertFalse(any('restart' in c for c in s.calls))

    def test_oneshot_active_exited_is_accepted(self):
        s = FakeSystem()
        self.assertEqual(self.call(s)['status'], 'ready')

    def test_preloaded_never_starts_manager(self):
        self.row['preloaded'] = True
        s = FakeSystem()
        self.assertEqual(self.call(s)['reason'], 'preloaded')
        self.assertEqual(s.calls, [])

    def test_inert_never_enables_linger(self):
        (Path(self.tmp.name) / NAME).touch()
        s = FakeSystem()
        self.assertEqual(self.call(s)['reason'], 'inert')
        self.assertEqual(s.calls, [])

    def test_broken_inert_symlink_is_still_a_hold(self):
        (Path(self.tmp.name) / NAME).symlink_to('absent')
        self.assertEqual(self.call(FakeSystem())['reason'], 'inert')

    def test_expired_never_starts(self):
        s = FakeSystem()
        s.is_expired = True
        self.assertEqual(self.call(s)['reason'], 'expired')
        self.assertEqual(s.calls, [])

    def test_locked_never_starts(self):
        s = FakeSystem()
        s.is_locked = True
        self.assertEqual(self.call(s)['reason'], 'locked')
        self.assertEqual(s.calls, [])

    def test_nologin_and_root_are_held(self):
        self.user.pw_shell = '/usr/sbin/nologin'
        self.assertEqual(self.call(FakeSystem())['reason'], 'login disabled')
        self.user.pw_uid = 0
        self.assertEqual(self.call(FakeSystem())['reason'], 'invalid account identity')

    def test_lockdown_and_named_hold(self):
        for policy, expected in (((True, set()), 'access lockdown'),
                                 ((False, {NAME}), 'explicit access hold')):
            s = FakeSystem()
            self.assertEqual(self.call(s, policy_reader=lambda: policy)['reason'], expected)
            self.assertEqual(s.calls, [])

    def test_hold_arriving_before_manager_start(self):
        values = iter([(False, set()), (False, set()), (True, set())])
        s = FakeSystem()
        result = self.call(s, policy_reader=lambda: next(values))
        self.assertEqual(result['status'], 'held')
        self.assertFalse(any('user@1003.service' in c for c in s.calls))

    def test_hold_arriving_before_unit_enablement(self):
        values = iter([(False,set())] * 3 + [(False,{NAME})])
        s = FakeSystem()
        self.assertEqual(self.call(s, policy_reader=lambda: next(values))['status'], 'held')
        self.assertFalse(s.did_enable)

    def test_mask_is_not_removed(self):
        s = FakeSystem(masked=True)
        self.assertEqual(self.call(s)['status'], 'blocked')
        self.assertFalse(s.did_enable)
        self.assertFalse(any('enable-linger' in c for c in s.calls))

    def test_missing_unit_is_reported_without_provisioning(self):
        s = FakeSystem(missing=True)
        self.assertEqual(self.call(s)['status'], 'blocked')
        self.assertFalse(s.did_enable)

    def test_command_failures_do_not_claim_success(self):
        for fail in ('enable-linger', 'start', 'enable'):
            with self.subTest(fail=fail):
                self.assertEqual(self.call(FakeSystem(fail=fail))['status'], 'failed')

    def test_postcondition_is_required(self):
        self.assertEqual(self.call(FakeSystem(ready=False))['status'], 'failed')

    def test_audit_is_read_only(self):
        s = FakeSystem()
        self.assertEqual(self.call(s, audit=True)['status'], 'observed')
        self.assertFalse(s.did_enable)
        self.assertFalse(any('enable-linger' in c or 'start' in c for c in s.calls))

    def test_corrupt_policy_blocks_before_mutation(self):
        s = FakeSystem()
        def broken():
            raise ValueError('invalid policy')
        with self.assertRaises(ValueError):
            self.call(s, policy_reader=broken)
        self.assertEqual(s.calls, [])

    def test_trusted_metadata_rejects_symlink(self):
        target = Path(self.tmp.name)/'target'
        target.write_text('plain metadata')
        link = Path(self.tmp.name)/'link'
        link.symlink_to(target)
        with self.assertRaises(OSError):
            m.trusted_text(link)

    def test_trusted_metadata_rejects_wrong_owner(self):
        target = Path(self.tmp.name)/'target'
        target.write_text('plain metadata')
        with self.assertRaises(ValueError):
            m.trusted_text(target)


if __name__ == '__main__':
    unittest.main()
