"""2026-10-04 duel fixes U4 (M1/M2/X3) and U8 (L3/L4).

M1: the rolling guard looks windows up by the CURRENT weekday at check time
    (maintenance_candidates), with yesterday's windows spilling past midnight.
M2/X3: the dispatcher's state machine (scripts/lib/maintenance_queue.py):
    dispatched -> done | failed | skipped, one retry, failures never block.
L3: plan.sh run() fails fast in execute mode.
L4: redact-screenshot skips boxes outside the image.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "scripts" / "maintenance-window-guard.sh"
spec = importlib.util.spec_from_file_location("mq", REPO / "scripts" / "lib" / "maintenance_queue.py")
mq = importlib.util.module_from_spec(spec); spec.loader.exec_module(mq)

CANDIDATES = {
    "Mon": [{"start_hour": 3, "width_h": 2}, {"start_hour": 7, "width_h": 2}, {"start_hour": 23, "width_h": 2}],
    "Tue": [{"start_hour": 4, "width_h": 2}, {"start_hour": 22, "width_h": 2}],
    "Sun": [{"start_hour": 5, "width_h": 2}, {"start_hour": 23, "width_h": 2}],
}


class RollingGuardWeekdayLookup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        # deliberately stale today_windows: the guard must NOT use them
        json.dump({"today": "Sun", "today_windows": [{"start_hour": 17, "width_h": 2}],
                   "maintenance_candidates": CANDIDATES}, self.tmp)
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def inside(self, weekday, hhmm):
        env = dict(os.environ, CTDC_QUIET_WINDOWS_JSON=self.tmp.name)
        r = subprocess.run([str(GUARD), "--rolling", "--check", "--weekday", weekday, "--at", hhmm],
                           capture_output=True, text=True, env=env)
        return r.returncode == 0

    def test_monday_0300_window_open_at_0330(self):
        self.assertTrue(self.inside("Mon", "03:30"))           # the M1 case

    def test_stale_today_windows_ignored(self):
        self.assertFalse(self.inside("Mon", "17:30"))          # 17:00 is only in today_windows

    def test_previous_day_window_spills_past_midnight(self):
        self.assertTrue(self.inside("Mon", "00:30"))           # Sun 23:00+2h
        self.assertTrue(self.inside("Tue", "00:30"))           # Mon 23:00+2h
        self.assertFalse(self.inside("Tue", "01:30"))          # spill ended at 01:00

    def test_own_late_window_does_not_open_after_midnight(self):
        # Tue 22:00+2h ends exactly at midnight; it must not open Tue 00:30 itself
        self.assertFalse(self.inside("Wed", "00:30"))

    def test_outside_all_windows(self):
        self.assertFalse(self.inside("Mon", "12:00"))
        self.assertFalse(self.inside("Mon", "05:10"))


class QueueStateMachine(unittest.TestCase):
    Q = ["a", "b", "c"]

    def st(self):
        return {"day": "2026-10-05", "jobs": {}, "unrun_notified": False}

    def test_classify_outcomes(self):
        self.assertEqual(mq.classify(["Starting a.service", "Finished a.service - x"]), "done")
        self.assertEqual(mq.classify(["Starting a", "a.service: Failed with result 'exit-code'."]), "failed")
        self.assertEqual(mq.classify(["a.service: Skipped due to 'exec-condition'."]), "skipped")
        self.assertEqual(mq.classify(["Main process exited, code=exited, status=75/TEMPFAIL", "Finished a"]), "skipped")
        self.assertEqual(mq.classify(["flock: failed to get lock"]), "skipped")
        self.assertEqual(mq.classify(["Starting a.service"]), "dispatched")

    def test_failed_job_does_not_block_queue_and_retries_once(self):
        s = self.st()
        self.assertEqual(mq.next_job(s, self.Q), "a")
        mq.mark_dispatched(s, "a")
        self.assertEqual(mq.reconcile(s, "a", ["Failed with result 'exit-code'"], active=False), "failed")
        self.assertEqual(mq.next_job(s, self.Q), "b")           # never-tried first
        mq.mark_dispatched(s, "b"); mq.reconcile(s, "b", ["Finished b"], active=False)
        mq.mark_dispatched(s, "c"); mq.reconcile(s, "c", ["Finished c"], active=False)
        self.assertEqual(mq.next_job(s, self.Q), "a")           # one retry
        mq.mark_dispatched(s, "a")
        self.assertEqual(mq.reconcile(s, "a", ["Failed with result"], active=False), "failed-final")
        self.assertIsNone(mq.next_job(s, self.Q))
        self.assertEqual(mq.unrun(s, self.Q), ["a"])

    def test_running_job_stays_dispatched(self):
        s = self.st(); mq.mark_dispatched(s, "a")
        self.assertEqual(mq.reconcile(s, "a", [], active=True), "dispatched")

    def test_skip_is_not_done(self):
        s = self.st(); mq.mark_dispatched(s, "a")
        self.assertEqual(mq.reconcile(s, "a", ["Skipped due to 'exec-condition'"], active=False), "skipped")
        self.assertIn("a", mq.unrun(s, self.Q))
        self.assertEqual(mq.next_job(s, self.Q), "b")

    def test_position_after_update(self):
        s = self.st()
        mq.mark_dispatched(s, "a"); mq.reconcile(s, "a", ["Finished a"], active=False)
        mq.mark_dispatched(s, "b")
        self.assertEqual(mq.position(s, self.Q, "b"), "2/3 (attempt 1)")   # X3: not "1/3" twice

    def test_migrates_old_state_shape(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"day": "2026-10-05", "started": ["a", "b"], "unrun_notified": False}, f)
        try:
            s = mq.load(f.name, "2026-10-05")
            self.assertEqual(s["jobs"]["a"]["status"], "done")
            self.assertEqual(mq.next_job(s, self.Q), "c")
        finally:
            os.unlink(f.name)

    def test_new_day_resets(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"day": "2026-10-04", "jobs": {"a": {"status": "done"}}}, f)
        try:
            self.assertEqual(mq.load(f.name, "2026-10-05")["jobs"], {})
        finally:
            os.unlink(f.name)


class PlanRunFailFast(unittest.TestCase):
    def harness(self, mode, cmd):
        plan = (REPO / "scripts" / "agent-segmentation" / "plan.sh").read_text()
        i = plan.index("run() {"); j = plan.index("\n}\n", i) + 3
        script = f'MODE={mode}\nCUR_STEP=7\nin_scope() {{ return 0; }}\n{plan[i:j]}\nrun "{cmd}"\necho AFTER\n'
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_execute_failure_stops_with_step(self):
        r = self.harness("execute", "false")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED at step 7: false", r.stderr)
        self.assertNotIn("AFTER", r.stdout)

    def test_execute_success_continues(self):
        r = self.harness("execute", "true")
        self.assertEqual(r.returncode, 0); self.assertIn("AFTER", r.stdout)

    def test_dry_run_never_executes(self):
        r = self.harness("dry-run", "false")
        self.assertEqual(r.returncode, 0); self.assertIn("AFTER", r.stdout)


class RedactSkipsEmptyBoxes(unittest.TestCase):
    def test_outside_box_skipped_inside_box_applied(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "s.png"), os.path.join(d, "o.png")
            Image.new("RGB", (200, 100), (10, 10, 10)).save(src)
            tool = str(REPO / "scripts" / "redact-screenshot.py")
            r = subprocess.run(["python3", tool, src, dst, "500,500,600,600"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 65); self.assertFalse(os.path.exists(dst))
            r = subprocess.run(["python3", tool, src, dst, "500,500,600,600", "10,10,50,40"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0); self.assertIn("skipped", r.stderr)
            self.assertTrue(os.path.exists(dst))


if __name__ == "__main__":
    unittest.main()
