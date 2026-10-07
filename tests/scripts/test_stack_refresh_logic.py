"""
tests/scripts/test_stack_refresh_logic.py

Pure-logic pieces of the 2026-10-04 adversarial-duel fixes (unit U3) to
scripts/stack-refresh.sh, scripts/serialized-rollout.sh and
scripts/lib/tripwire_draw.py. Nothing here pulls, builds or restarts: the
bash helpers are extracted from the script text and run against temp files.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REFRESH = REPO / "scripts" / "stack-refresh.sh"
ROLLOUT = REPO / "scripts" / "serialized-rollout.sh"
TRIPWIRE_UNIT = REPO / ".config" / "systemd" / "user" / "corporatetraveldc-stack-refresh-tripwire.service"


def load_draw():
    spec = importlib.util.spec_from_file_location("tripwire_draw", REPO / "scripts" / "lib" / "tripwire_draw.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.QUIET_JSON = "/nonexistent/quiet-windows.json"   # -> uniform draws, deterministic range
    return m


def bash_funcs(*names: str) -> str:
    """Extract top-level bash function definitions by brace counting, skipping
    heredoc bodies (debt_update/record_built embed python with braces)."""
    lines = REFRESH.read_text().splitlines()
    out = []
    for n in names:
        try:
            i = next(k for k, l in enumerate(lines) if re.match(rf"^{re.escape(n)}\(\)\s+\{{", l))
        except StopIteration:
            raise AssertionError(f"function {n} not found in stack-refresh.sh")
        depth, block, heredoc = 0, [], None
        for l in lines[i:]:
            block.append(l)
            if heredoc:
                if l.strip() == heredoc:
                    heredoc = None
                continue
            m = re.search(r"<<'([A-Z]+)'", l)
            depth += l.count("{") - l.count("}")
            if m:
                heredoc = m.group(1)
            if depth <= 0 and not heredoc:
                break
        out.append("\n".join(block))
    return "\n".join(out)


def run_bash(script: str, env: dict | None = None) -> str:
    e = dict(os.environ); e.update(env or {})
    r = subprocess.run(["bash", "-c", script], cwd=REPO, env=e, capture_output=True, text=True, timeout=60)
    if r.returncode not in (0, 1):
        raise AssertionError(f"bash failed rc={r.returncode}: {r.stderr}")
    return r.stdout


class TripwireDraw(unittest.TestCase):
    def setUp(self):
        self.m = load_draw()

    def test_mark_does_not_remove_next_run(self):
        st = {"next_run": 123, "next_label": "uniform"}
        st = self.m.mark(st)
        self.assertIn("next_run", st)
        self.assertGreater(st["last_run"], 0)

    def test_mark_and_draw_range(self):
        st = self.m.draw(self.m.mark({}))
        last = st["last_run"]
        self.assertGreaterEqual(st["next_run"], last + self.m.MIN_GAP_S)
        self.assertLessEqual(st["next_run"], last + self.m.MAX_GAP_S)

    def test_draw_failure_falls_back_never_empty(self):
        def boom(_st):
            raise KeyError("schema changed")
        self.m._draw = boom
        st = self.m.draw({"last_run": int(time.time())})
        self.assertIn("next_run", st)
        self.assertIn("fallback", st["next_reason"])
        self.assertGreaterEqual(st["next_run"], st["last_run"] + self.m.MIN_GAP_S)

    def test_missing_next_run_is_not_due_and_gets_drawn(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            json.dump({"last_run": int(time.time()) - 86400}, open(p, "w"))
            rc = self.m.main(["due", p])
            self.assertEqual(rc, 1, "missing next_run must NOT be due")
            self.assertIn("next_run", json.load(open(p)))

    def test_near_ceiling(self):
        now = int(time.time())
        self.assertTrue(self.m.near_ceiling({"last_run": now - self.m.MAX_GAP_S + 1800}, now))
        self.assertTrue(self.m.near_ceiling({"last_run": now - self.m.MAX_GAP_S - 10}, now))
        self.assertFalse(self.m.near_ceiling({"last_run": now - 6 * 86400}, now))
        self.assertFalse(self.m.near_ceiling({}, now))


class RolloutListAndDebt(unittest.TestCase):
    PRE = ("REPO_PATH=/opt/corporatetraveldc/private/ctdi-dispatch-internal; "
           "WEBSITE_PATH=/opt/corporatetraveldc/private/csexecutiveservices-website; MODE=weekly\n")

    def funcs(self):
        return self.PRE + bash_funcs("rollout_rows", "rollout_units", "units_for_image", "is_pg_unit", "debt_update") + "\n"

    def test_image_maps_to_every_unit(self):
        out = run_bash(self.funcs() + "units_for_image localhost/corporatetraveldc-ingest:latest")
        units = out.split()
        self.assertEqual(len(units), 7)
        self.assertTrue(all(u.startswith("corporatetraveldc-ingest-") for u in units))

    def test_pg_units_detected(self):
        out = run_bash(self.funcs() + "for u in corporatetraveldc-pgsql nextcloud-db corporatetraveldc-web; do is_pg_unit $u && echo $u; done")
        self.assertEqual(out.split(), ["corporatetraveldc-pgsql", "nextcloud-db"])

    def test_debt_keyed_by_unit_and_clears_by_unit(self):
        with tempfile.TemporaryDirectory() as d:
            debt = os.path.join(d, "held.json")
            script = self.funcs() + f'DEBT_FILE="{debt}"\n' + (
                'held=""; for u in $(units_for_image localhost/corporatetraveldc-ingest:latest); do held="${held}${u}=build-failed;"; done\n'
                'debt_update "$held" "" >/dev/null\n'
                'debt_update "" "$(units_for_image localhost/corporatetraveldc-ingest:latest | paste -sd" ")"\n')
            out = run_bash(script)
            self.assertIn("review debt: none", out, "unit-keyed debt must clear when those units restart (duel M6)")
            self.assertEqual(json.load(open(debt)), {})


    def test_dry_run_never_writes_debt(self):
        with tempfile.TemporaryDirectory() as d:
            debt = os.path.join(d, "held.json")
            out = run_bash(self.funcs() + f'DEBT_FILE="{debt}"; DRY=1\ndebt_update "corporatetraveldc-web=x;" ""')
            self.assertIn("1 unit(s) held", out)
            self.assertFalse(os.path.exists(debt), "a dry-run must not create or modify the debt file")


class AuditTrust(unittest.TestCase):
    def test_built_ids_manifest_is_the_only_extra_trust(self):
        text = REFRESH.read_text()
        self.assertNotIn("run_bd", text, "the build-date label heuristic must be gone (duel H3)")
        with tempfile.TemporaryDirectory() as d:
            ids = os.path.join(d, "built.json")
            json.dump([{"id": "abcdef0123456789aaaa", "image": "x"}], open(ids, "w"))
            fn = bash_funcs("is_our_build")
            out = run_bash(f'BUILT_IDS="{ids}"\n{fn}\nis_our_build abcdef012345 && echo trusted; is_our_build 999999999999 || echo untrusted; is_our_build "" || echo empty-untrusted')
            self.assertEqual(out.split(), ["trusted", "untrusted", "empty-untrusted"])


class HandOffContract(unittest.TestCase):
    def test_refresh_hands_off_restart_only_with_deadline(self):
        t = REFRESH.read_text()
        self.assertRegex(t, r"FROM_REFRESH=1 ROLLOUT_DEADLINE=")
        self.assertIn("STACK_REFRESH_MAX_S:-14400", t)

    def test_rollout_from_refresh_skips_build_pull_tag(self):
        t = ROLLOUT.read_text()
        i_from = t.index('if [[ $FROM_REFRESH == 1 ]]; then')
        i_build = t.index("podman build")
        i_pull = t.index("podman pull")
        self.assertLess(i_from, i_build); self.assertLess(i_from, i_pull)
        self.assertIn("elif [[ $kind == local ]]; then", t[i_from:i_build])

    def test_rollout_skips_postgres_without_canary(self):
        t = ROLLOUT.read_text()
        self.assertIn("docker.io/library/postgres:* && $PG_CANARY_DONE != 1", t)

    def test_previous_moves_only_on_change(self):
        t = ROLLOUT.read_text()
        self.assertNotRegex(t, r'podman tag "\$image" "\$\{image%:\*\}:previous"', "no unconditional :previous re-tag")
        self.assertNotRegex(t, r'podman tag "\$image" "\$\{image%:latest\}:previous"')
        self.assertIn('"$old_id" != "$new_id"', t)

    def test_tripwire_unit_has_no_start_timeout(self):
        self.assertIn("TimeoutStartSec=infinity", TRIPWIRE_UNIT.read_text())

    def test_fingerprints(self):
        t = REFRESH.read_text()
        fp = bash_funcs("fp_sudo")
        self.assertNotIn("sudo -n -l", fp, "fp_sudo must not depend on a cached sudo ticket (duel M4)")
        self.assertIn("check_fp team_authorized_keys", t)
        self.assertEqual(run_bash(fp + "\nfp_sudo"), run_bash(fp + "\nfp_sudo"), "fp_sudo must be deterministic")


if __name__ == "__main__":
    unittest.main()


class PromotionOutage20261006(unittest.TestCase):
    """2026-10-06 18:47Z-19:37Z outage: promotion ran `podman untag <check>`
    (no NAME = remove EVERY name, :latest included), an unset `declare -A`
    crashed the held-units step under bash 5.3 `set -u`, and the rollout still
    restarted web/poller/ingest/verifier onto images that no longer existed."""

    def _code(self, path):
        return "\n".join(l for l in path.read_text().splitlines() if not l.lstrip().startswith("#"))

    def test_no_single_argument_untag(self):
        for path in (REFRESH, ROLLOUT):
            for m in re.finditer(r'podman untag ("[^"]+"|\S+)(\s+("[^"]+"|[^\s>|&;]+))?', self._code(path)):
                self.assertIsNotNone(m.group(2), f"{path.name}: `{m.group(0)}` removes every name from the image")

    def test_assoc_arrays_initialised(self):
        for path in (REFRESH, ROLLOUT):
            for m in re.finditer(r"declare -A ([^\n;]+)", self._code(path)):
                for name in m.group(1).split():
                    self.assertTrue(name.endswith("=()"), f"{path.name}: `declare -A {name}` without =() is unset under set -u")

    def test_bash_semantics_the_fix_relies_on(self):
        ok = subprocess.run(["bash", "-uc", 'declare -A H=(); echo ${#H[@]}; for u in "${!H[@]}"; do :; done'],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_handoff_guard_precedes_rollout(self):
        code = self._code(REFRESH)
        guard, rollout = code.find("PROMOTION CHECK FAILED"), code.find("FROM_REFRESH=1 ROLLOUT_DEADLINE")
        self.assertGreater(guard, 0); self.assertGreater(rollout, guard)

    def test_rollout_refuses_missing_image_before_restart(self):
        code = self._code(ROLLOUT)
        guard, restart = code.find('podman image exists "$image"'), code.find('systemctl --user restart "$unit.service"')
        self.assertGreater(guard, 0); self.assertGreater(restart, guard)
