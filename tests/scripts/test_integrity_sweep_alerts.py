"""2026-10-23 (operator): integrity-sweep alert de-duplication
(scripts/lib/sweep_alerts.sh). A fake ntfy_post records every page; the clock
is SWEEP_NOW_EPOCH. Covers first / repeat / reminder / changed fingerprint /
cleared, and the provenance + manifest body summaries."""
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib" / "sweep_alerts.sh"
SWEEP = REPO / "scripts" / "scheduled-integrity-sweep.sh"

T0 = 1_761_000_000


class SweepAlerts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.sent = self.dir / "sent.log"

    def tearDown(self):
        self.tmp.cleanup()

    def run_sh(self, body: str, now: int = T0, stdin: str = "") -> str:
        script = (
            f'SWEEP_ALERT_DIR="{self.dir}/alerts"; SWEEP_NOW_EPOCH={now}\n'
            f'source "{LIB}"\n'
            f'ntfy_post() {{ printf "%s|%s|%s\\n" "$3" "$1" "$2" >> "{self.sent}"; }}\n'
            'log() { :; }\n' + body
        )
        r = subprocess.run(["bash", "-c", script], input=stdin, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def pages(self) -> list[tuple[str, str, str]]:
        if not self.sent.exists():
            return []
        return [tuple(l.split("|", 2)) for l in self.sent.read_text().splitlines()]

    def fail(self, now, msg="img-a: no receipt"):
        self.run_sh(f'ntfy_send "BUILD PROVENANCE FAILED" "{msg}" 5', now=now)

    def test_first_occurrence_pages_at_its_priority(self):
        self.fail(T0)
        self.assertEqual(self.pages(), [("5", "BUILD PROVENANCE FAILED", "img-a: no receipt")])

    def test_repeat_within_two_hours_is_suppressed(self):
        for i in range(8):                       # 15-min sweeps for 1h45
            self.fail(T0 + i * 900)
        self.assertEqual(len(self.pages()), 1)

    def test_reminder_at_most_every_two_hours_at_p4(self):
        for i in range(17):                      # 4 h of 15-min sweeps
            self.fail(T0 + i * 900)
        p = self.pages()
        self.assertEqual([x[0] for x in p], ["5", "4", "4"])
        self.assertEqual(p[1][1], "BUILD PROVENANCE FAILED (still failing)")
        self.assertIn("9 consecutive sweeps", p[1][2])
        self.assertIn("img-a: no receipt", p[1][2])

    def test_changed_fingerprint_is_a_new_failure(self):
        self.fail(T0)
        self.fail(T0 + 900, "img-a: no receipt\nimg-b: digest mismatch")
        self.assertEqual([x[0] for x in self.pages()], ["5", "5"])

    def test_cleared_once_then_silent(self):
        self.fail(T0)
        self.fail(T0 + 900)
        self.run_sh('ntfy_clear "BUILD PROVENANCE FAILED"', now=T0 + 1800)
        self.run_sh('ntfy_clear "BUILD PROVENANCE FAILED"', now=T0 + 2700)
        p = self.pages()
        self.assertEqual([x[0] for x in p], ["5", "3"])
        self.assertEqual(p[1][1], "CLEARED: BUILD PROVENANCE FAILED")
        self.assertIn("2 sweep(s)", p[1][2])
        self.fail(T0 + 3600)                     # recurrence after clear pages again
        self.assertEqual([x[0] for x in self.pages()], ["5", "3", "5"])

    def test_clear_without_prior_failure_sends_nothing(self):
        self.run_sh('ntfy_clear "INTEGRITY SWEEP FAILED"')
        self.assertEqual(self.pages(), [])

    def test_titles_are_tracked_independently(self):
        self.fail(T0)
        self.run_sh('ntfy_send "INTEGRITY SWEEP FAILED" "x" 5', now=T0 + 900)
        self.fail(T0 + 1800)
        self.assertEqual([x[1] for x in self.pages()], ["BUILD PROVENANCE FAILED", "INTEGRITY SWEEP FAILED"])

    def test_provenance_summary_names_images_and_reasons(self):
        prov = (
            "FAILED     localhost/corporatetraveldc-web:latest [tag; next start of e.g. web]\n"
            "    FAILED         receipt            localhost/x -> abc has NO receipt, but ...\n"
            "FAILED     localhost/corporatetraveldc-web:latest [running 0123456789ab in e.g. web]\n"
            "    FAILED         receipt            same\n"
            "FAILED     localhost/corporatetraveldc-docgen:latest [running ffffffffffff in e.g. d]\n"
            "    VERIFIED       image id           ok\n"
            "    FAILED         sbom digest        SBOM file differs from the digest in the receipt\n"
            "    UNVERIFIED     current source     built from abc, 3 commit(s) behind HEAD def\n"
            "VERIFIED   localhost/corporatetraveldc-ok:latest [tag; next start of e.g. ok]\n"
            "    VERIFIED       receipt            fine\n"
            "FAILED          lockfiles\n"
            "    FAILED          requirements.lock is stale\n"
            "build-policy: FAILED (1 failing check(s))\n"
        )
        out = self.run_sh("sweep_prov_summary", stdin=prov).splitlines()
        self.assertEqual(out, [
            "build policy: lockfiles",
            "localhost/corporatetraveldc-docgen:latest: digest mismatch",
            "localhost/corporatetraveldc-web:latest: no receipt",
        ])

    def test_manifest_summary_names_files(self):
        out = self.run_sh("sweep_manifest_summary", stdin=(
            "scripts/a.sh: FAILED\nsrc/b.py: FAILED\n"
            "sha256sum: WARNING: 2 computed checksums did NOT match\n"
            "verify-manifest: INTEGRITY FAILURE -- one or more files do not match\n"))
        self.assertEqual(out.strip(), "2 file(s) do not match the signed manifest: scripts/a.sh src/b.py")
        out = self.run_sh("sweep_manifest_summary", stdin=(
            "verify-manifest: INTEGRITY FAILURE -- 1 file(s) present in the tree but NOT covered by the signed manifest (x):\n"
            "  docs/new.md\n"))
        self.assertEqual(out.strip(), "1 file(s) not covered by the signed manifest: docs/new.md")

    def test_sweep_uses_the_dedup_layer_and_its_state_dir(self):
        s = SWEEP.read_text()
        self.assertIn('source "${REPO_DIR}/scripts/lib/sweep_alerts.sh"', s)
        self.assertIn('SWEEP_ALERT_DIR="${STATE_DIR}/alerts"', s)
        self.assertNotIn("Check ${LOG_FILE}", s)
        for t in ("BUILD PROVENANCE FAILED", "INTEGRITY SWEEP FAILED", "AUDIT CHAIN BROKEN",
                  "ENV QUOTING REGRESSION", "DEMO ISOLATION CHECK FAILED"):
            self.assertIn(f'ntfy_clear "{t}"', s)
        r = subprocess.run(["bash", "-n", str(SWEEP)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
