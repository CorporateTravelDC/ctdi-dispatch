"""
2026-10-07: scripts/approved-exec.py -- the root executor that makes the sudo
approval gate mechanical (docs/AGENT_TRUST_MODEL.md §9.1). Nothing here runs as
root or executes a command: the decision functions are exercised directly.
"""
from __future__ import annotations

import base64
import importlib.util
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


def load():
    spec = importlib.util.spec_from_file_location("approved_exec", REPO / "scripts" / "approved-exec.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class ApprovedExec(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.m = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.allow = self.m.load_allowlist(str(REPO / "scripts" / "approved-exec.conf"))
        k = self.d / "appr"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(k)], check=True)
        self.key = str(k)
        pub = (self.d / "appr.pub").read_text().split()
        self.pin = self.d / "allowed_signers"
        self.pin.write_text(f'op namespaces="corporatetraveldc-approval" {pub[0]} {pub[1]}\n')

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self, **kw):
        now = time.time()
        row = {"id": "00000000-0000-0000-0000-000000000001", "kind": "sudo", "status": "allowed",
               "requester": "token:sudo-gate", "command_pattern": "dnf-remove",
               "command": "/usr/bin/dnf remove -y foo", "reasoning": "clean up foo",
               "expires_at": now + 300, "resolved_at": now, "resolved_by": "op"}
        row.update(kw)
        return row

    def _sign(self, row):
        r = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", self.key, "-n", "corporatetraveldc-approval"],
                           input=self.m.canonical(row), capture_output=True, check=True)
        row["resolution_sig"] = base64.b64encode(r.stdout).decode()
        return row

    def test_canonical_is_byte_identical_to_the_server(self):
        from common import governance
        row = self._row()
        self.assertEqual(self.m.canonical(row, "allow"), governance.approval_canonical(row, "allow"))

    def test_a_valid_signature_verifies_and_a_forged_row_does_not(self):
        row = self._sign(self._row())
        self.m.verify_signature(row, str(self.pin))
        forged = dict(row, command="/usr/bin/dnf remove -y sudo")       # a DB writer swaps the command
        with self.assertRaises(self.m.Refused):
            self.m.verify_signature(forged, str(self.pin))
        other = dict(row, resolved_by="someone-else")
        with self.assertRaises(self.m.Refused):
            self.m.verify_signature(other, str(self.pin))

    def test_allowlist_shapes(self):
        ok = self.m.command_argv(self._row(command="sudo -n /usr/bin/dnf remove -y foo bar"), self.allow)
        self.assertEqual(ok, ["/usr/bin/dnf", "remove", "-y", "foo", "bar"])
        self.assertEqual(self.m.command_argv(self._row(command_pattern="semanage-port-add",
                         command="/usr/sbin/semanage port -a -t http_port_t -p tcp 8443"), self.allow)[0],
                         "/usr/sbin/semanage")
        bad = [("dnf-remove", "/usr/bin/dnf remove -y 'foo; reboot'"),
               ("dnf-remove", "dnf remove -y foo"),                         # not absolute
               ("dnf-remove", "/usr/bin/dnf install -y foo"),               # wrong verb for the pattern
               ("semanage-port-add", "/usr/bin/dnf remove -y foo"),         # command from another pattern
               ("no-such-pattern", "/usr/bin/dnf autoremove")]
        for pattern, cmd in bad:
            with self.subTest(cmd=cmd), self.assertRaises(self.m.Refused):
                self.m.command_argv(self._row(command_pattern=pattern, command=cmd), self.allow)

    def test_row_state_and_timing(self):
        now = time.time()
        self.m.check_row(self._sign(self._row()), "00000000-0000-0000-0000-000000000001", now)
        cases = [dict(status="pending"), dict(status="denied"), dict(kind="console-login"),
                 dict(resolution_sig=""), dict(resolved_at=now + 10, expires_at=now + 5),
                 dict(expires_at=now - self.m.EXEC_GRACE_S - 1, resolved_at=now - 400)]
        for kw in cases:
            with self.subTest(kw=kw), self.assertRaises(self.m.Refused):
                self.m.check_row(self._row(**{"resolution_sig": "x", **kw}),
                                 "00000000-0000-0000-0000-000000000001", now)
        with self.assertRaises(self.m.Refused):
            self.m.check_row(self._row(resolution_sig="x"), "00000000-0000-0000-0000-000000000002", now)

    def test_each_approval_executes_at_most_once(self):
        ledger = self.d / "ledger"
        ledger.mkdir()
        os.close(self.m.consume("00000000-0000-0000-0000-000000000001", str(ledger)))
        with self.assertRaises(self.m.Refused):
            self.m.consume("00000000-0000-0000-0000-000000000001", str(ledger))

    def test_trust_files_must_be_root_owned(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        with self.assertRaises(self.m.Refused):
            self.m._root_only(str(self.pin))

    def test_refuses_when_not_root(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        self.assertEqual(self.m.main(["approved-exec.py", "00000000-0000-0000-0000-000000000001"]), 65)
        self.assertEqual(self.m.main(["approved-exec.py", "not-a-uuid"]), 64)


class SudoersShape(unittest.TestCase):
    """The tracked sudoers fragment grants passwordless root ONLY to the
    executor, at the path install-root-copies.sh installs it to, and requires a
    password for every formerly passwordless gated command."""

    def test_fragment(self):
        import re
        frag = (REPO / "config" / "sudoers.d" / "zz-ctdc-approved-exec").read_text()
        body = re.sub(r"\\\n\s*", " ", "\n".join(l for l in frag.splitlines() if not l.startswith("#")))
        dest = re.search(r"^DEST=(\S+)", (REPO / "scripts" / "install-root-copies.sh").read_text(), re.M).group(1)
        nopasswd = [l for l in body.splitlines() if "NOPASSWD:" in l]
        self.assertEqual(len(nopasswd), 1)
        self.assertTrue(nopasswd[0].rstrip().endswith(f"{dest}/approved-exec.py"))
        passwd = next(l for l in body.splitlines() if " PASSWD:" in l)
        for cmd in ("/usr/bin/dnf remove *", "/usr/bin/dnf autoremove", "/usr/bin/semanage port -a *"):
            self.assertIn(cmd, passwd)
        inst = (REPO / "scripts" / "install-root-copies.sh").read_text()
        self.assertIn("scripts/approved-exec.py", inst)
        self.assertIn("scripts/approved-exec.conf", inst)
