"""2026-11-18 (operator): the failover polkit rule is guarded. It grants the operator account the two
NetworkManager actions ONLY while no hold is in force -- a lockdown marker (volatile or persistent), an
uncommanded-reboot hold, or a boot the guard has not verified -- and never falls open on an error.
A reboot made through scripts/ctdc-reboot.sh is commanded; any other reboot writes the hold.

The rule itself is executed under node with a fake polkit object whose spawn() runs the rule's real
shell check with its absolute paths mapped into a temporary root."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RULE = ROOT / "config/polkit/49-ctdc-net-failover.rules"
BOOT_GUARD = ROOT / "scripts/ctdc-boot-guard.sh"
REBOOT = ROOT / "scripts/ctdc-reboot.sh"
NODE = shutil.which("node")
MODIFY = "org.freedesktop.NetworkManager.settings.modify.system"
CONTROL = "org.freedesktop.NetworkManager.network-control"

HARNESS = r"""
const fs = require('fs'), cp = require('child_process');
const [ruleFile, root, user, action, mode] = process.argv.slice(2);
let rule = null; const logs = [];
const polkit = {
  Result: {YES: 'yes', NO: 'no', NOT_HANDLED: 'not_handled'},
  addRule: f => { rule = f; },
  log: m => logs.push(m),
  spawn: argv => {
    if (mode === 'spawn-throws') throw new Error('boom');
    if (mode === 'garbage') return 'something else\n';
    const a = argv.map(s => s.split('/run/').join(root + '/run/').split('/var/lib/').join(root + '/var/lib/'));
    return cp.execFileSync(a[0], a.slice(1), {encoding: 'utf8'});   // throws on non-zero, like polkit
  },
};
eval(fs.readFileSync(ruleFile, 'utf8'));
const r = rule({id: action}, {user: user});
console.log(JSON.stringify({result: r === undefined ? 'not_handled' : r, logs}));
"""


def _decide(tmp_path, *, user="corporatetraveldc", action=MODIFY, mode="real"):
    h = tmp_path / "harness.js"
    h.write_text(HARNESS)
    out = subprocess.run([NODE, str(h), str(RULE), str(tmp_path), user, action, mode],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x\n")
    return p


needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


@needs_node
def test_yes_only_with_a_verified_boot_and_no_hold(tmp_path):
    d = _decide(tmp_path)                                     # boot not verified -> hold
    assert d["result"] == "no" and any("boot-unverified" in l for l in d["logs"])
    _touch(tmp_path, "run/ctdc-boot-guard/verified")
    d = _decide(tmp_path)
    assert d["result"] == "yes" and any("YES" in l for l in d["logs"])
    assert _decide(tmp_path, action=CONTROL)["result"] == "yes"


@needs_node
@pytest.mark.parametrize("marker", ["run/corporatetraveldc-lockdown-active",
                                    "var/lib/ctdc-lockdown/lockdown-active",
                                    "var/lib/ctdc-lockdown/uncommanded-reboot-hold"])
def test_any_hold_means_no(tmp_path, marker):
    _touch(tmp_path, "run/ctdc-boot-guard/verified")
    _touch(tmp_path, marker)
    d = _decide(tmp_path)
    assert d["result"] == "no" and any("NO" in l and marker.rsplit("/", 1)[-1] in l for l in d["logs"])


@needs_node
@pytest.mark.parametrize("mode", ["spawn-throws", "garbage"])
def test_errors_never_fall_open(tmp_path, mode):
    _touch(tmp_path, "run/ctdc-boot-guard/verified")
    assert _decide(tmp_path, mode=mode)["result"] == "not_handled"


@needs_node
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@pytest.mark.parametrize("locked,markers", [
    ("run/ctdc-boot-guard", ["run/ctdc-boot-guard/verified"]),                        # verified unreadable
    ("var/lib/ctdc-lockdown", ["var/lib/ctdc-lockdown/uncommanded-reboot-hold"]),     # a hold unreadable
    ("var/lib/ctdc-lockdown", []),                                                    # cannot prove absent
])
def test_an_unreadable_marker_is_an_error_never_absent(tmp_path, locked, markers):
    """2026-11-18: SELinux denied polkitd getattr on the markers and `[ -e ]` read that as absent --
    `verified` vanished (NO) and a real hold would have vanished too (YES = fail open). A permission
    failure must now give NOT_HANDLED (NetworkManager's own default), never YES and never a guess."""
    _touch(tmp_path, "run/ctdc-boot-guard/verified")
    (tmp_path / locked).mkdir(parents=True, exist_ok=True)
    for m in markers:
        _touch(tmp_path, m)
    d = tmp_path / locked
    d.chmod(0)
    try:
        r = _decide(tmp_path)
    finally:
        d.chmod(0o755)
    assert r["result"] == "not_handled", r
    assert any("cannot read marker" in l for l in r["logs"]), r


def test_rule_never_tests_existence_with_dash_e():
    body = "\n".join(l for l in RULE.read_text().splitlines() if not l.lstrip().startswith("//"))
    assert "[ -e" not in body and "[ ! -e" not in body
    assert "No such file or directory" in body and "LC_ALL=C" in body


def test_selinux_module_lets_polkitd_see_the_markers_and_nothing_more():
    te = (ROOT / "selinux/corporatetraveldc-guard-markers.te").read_text()
    allows = [l.strip() for l in te.splitlines() if l.strip().startswith("allow policykit_t")]
    assert allows == ["allow policykit_t ctdc_guard_state_t:dir { getattr search };",
                      "allow policykit_t ctdc_guard_state_t:file getattr;"]
    for name in ('"ctdc-boot-guard"', '"corporatetraveldc-lockdown-active"', '"ctdc-lockdown"'):
        assert name in te
    apply = (ROOT / "selinux/apply-selinux-policy.sh").read_text()
    assert 'build_and_load_module "corporatetraveldc-guard-markers"' in apply
    for spec in ("/run/ctdc-boot-guard(/.*)?", "/run/corporatetraveldc-lockdown-active", "/var/lib/ctdc-lockdown(/.*)?"):
        assert spec in apply


@needs_node
@pytest.mark.parametrize("user,action", [("ctdc-agent-anthropic-claude-ea", MODIFY), ("root", CONTROL),
                                         ("corporatetraveldc", "org.freedesktop.NetworkManager.wifi.scan")])
def test_other_users_and_actions_are_not_handled(tmp_path, user, action):
    _touch(tmp_path, "run/ctdc-boot-guard/verified")
    assert _decide(tmp_path, user=user, action=action)["result"] == "not_handled"


def _run(script, tmp_path, *args, **kw):
    env = dict(os.environ, CTDC_GUARD_TEST_ROOT=str(tmp_path))
    env.update({k: str(v) for k, v in kw.items()})
    return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True, timeout=90)


def test_fresh_commanded_reboot_means_no_hold(tmp_path):
    r = _run(REBOOT, tmp_path, "--reason", "kernel 6.18.62")
    assert r.returncode == 0 and "TEST: systemctl reboot" in r.stdout, r.stderr
    rec = (tmp_path / "var/lib/ctdc-lockdown/reboot-commanded").read_text()
    ep = int(rec.split("epoch=")[1].split()[0])
    g = _run(BOOT_GUARD, tmp_path, "--boot", CTDC_GUARD_TEST_BOOT_ID="b1", CTDC_GUARD_TEST_BOOT_EPOCH=ep + 40)
    assert g.returncode == 0, g.stderr
    assert not (tmp_path / "var/lib/ctdc-lockdown/uncommanded-reboot-hold").exists()
    assert not (tmp_path / "var/lib/ctdc-lockdown/reboot-commanded").exists()       # consumed
    assert "commanded" in (tmp_path / "run/ctdc-boot-guard/verified").read_text()


@pytest.mark.parametrize("age", [None, 1801, 86400])
def test_missing_or_stale_record_means_hold(tmp_path, age):
    now = 1_800_000_000
    if age is not None:
        d = tmp_path / "var/lib/ctdc-lockdown"
        d.mkdir(parents=True)
        (d / "reboot-commanded").write_text(f"epoch={now - age} by=x reason=old\n")
    g = _run(BOOT_GUARD, tmp_path, "--boot", CTDC_GUARD_TEST_BOOT_ID="b2", CTDC_GUARD_TEST_BOOT_EPOCH=now)
    assert g.returncode == 0, g.stderr
    hold = (tmp_path / "var/lib/ctdc-lockdown/uncommanded-reboot-hold").read_text()
    assert "boot_id=b2 " in hold and "alerted=no" in hold
    assert not (tmp_path / "run/ctdc-boot-guard/verified").exists()
    assert not (tmp_path / "var/lib/ctdc-lockdown/reboot-commanded").exists()       # a stale record is consumed too
    a = _run(BOOT_GUARD, tmp_path, "--alert", CTDC_GUARD_TEST_BOOT_ID="b2")
    assert "TEST: ntfy priority 5" in a.stdout
    assert "alerted=yes" in (tmp_path / "var/lib/ctdc-lockdown/uncommanded-reboot-hold").read_text()
    a2 = _run(BOOT_GUARD, tmp_path, "--alert", CTDC_GUARD_TEST_BOOT_ID="b2")
    assert "TEST: ntfy" not in a2.stdout                                             # alerts once


def test_record_from_the_future_is_not_trusted(tmp_path):
    now = 1_800_000_000
    d = tmp_path / "var/lib/ctdc-lockdown"
    d.mkdir(parents=True)
    (d / "reboot-commanded").write_text(f"epoch={now + 3600} by=x reason=forged\n")
    _run(BOOT_GUARD, tmp_path, "--boot", CTDC_GUARD_TEST_BOOT_ID="b4", CTDC_GUARD_TEST_BOOT_EPOCH=now)
    assert (d / "uncommanded-reboot-hold").exists()


def test_clear_hold_removes_only_the_hold(tmp_path):
    lock = _touch(tmp_path, "var/lib/ctdc-lockdown/lockdown-active")
    runl = _touch(tmp_path, "run/corporatetraveldc-lockdown-active")
    hold = _touch(tmp_path, "var/lib/ctdc-lockdown/uncommanded-reboot-hold")
    r = _run(REBOOT, tmp_path, "--clear-hold", "--reason", "power blip, checked")
    assert r.returncode == 0, r.stderr
    assert not hold.exists() and lock.exists() and runl.exists()
    assert (tmp_path / "run/ctdc-boot-guard/verified").exists()
    assert "only restore-network.sh lifts a lockdown" in r.stdout


def test_alert_reasserts_a_persistent_lockdown(tmp_path):
    _touch(tmp_path, "var/lib/ctdc-lockdown/lockdown-active")
    a = _run(BOOT_GUARD, tmp_path, "--alert", CTDC_GUARD_TEST_BOOT_ID="b3")
    assert "TEST: lockdown.sh --reason re-asserted at boot" in a.stdout


def test_reboot_requires_a_reason(tmp_path):
    assert _run(REBOOT, tmp_path).returncode == 64


def test_lockdown_writes_and_restore_clears_the_persistent_markers():
    lock = (ROOT / "scripts/lockdown.sh").read_text()
    rest = (ROOT / "scripts/restore-network.sh").read_text()
    assert 'PERSIST_FILE="${PERSIST_DIR}/lockdown-active"' in lock and '> "${PERSIST_FILE}.new"' in lock
    assert lock.index('PERSIST_FILE}.new"') < lock.index("withheld.kill_all")      # marker before anything else
    assert 'rm -f "${STATE_FILE}" "${PERSIST_FILE}" "${HOLD_FILE}"' in rest
    assert '! -f "${PERSIST_FILE}" && ! -f "${HOLD_FILE}"' in rest                   # restore works after a reboot
    assert "/run/ctdc-boot-guard/verified" in rest


def test_boot_units_and_root_install_coverage():
    boot = (ROOT / "systemd/system/corporatetraveldc-boot-guard.service").read_text()
    alert = (ROOT / "systemd/system/corporatetraveldc-boot-guard-alert.service").read_text()
    assert "Before=NetworkManager.service" in boot and "DefaultDependencies=no" in boot
    assert "ExecStart=/usr/local/libexec/ctdc/ctdc-boot-guard.sh --boot" in boot
    assert "After=network-online.target" in alert and "ctdc-boot-guard.sh --alert" in alert
    inst = (ROOT / "scripts" / ("install-root-" + "copies.sh")).read_text()
    for f in ("scripts/ctdc-boot-guard.sh", "scripts/ctdc-reboot.sh",
              "systemd/system/corporatetraveldc-boot-guard.service",
              "systemd/system/corporatetraveldc-boot-guard-alert.service"):
        assert f in inst, f
