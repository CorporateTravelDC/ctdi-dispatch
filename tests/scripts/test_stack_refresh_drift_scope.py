"""weekly-external-image-update (stack-refresh --weekly) no longer fails on units it does not
touch (lean-out L2, 2026-10-19).

Any failed user unit absent from CLAUDE.md's Known bad made check-claude-md-drift.sh report
[DRIFT], which held the whole weekly refresh (exit 2); the end verdict also failed on ANY failed
unit. Failed-unit drift is now scoped to the rollout's own units: a failed unit outside them (or
any SYSTEM unit) is reported and the run continues; every other drift kind still holds."""
import os
import tempfile
from pathlib import Path

from tests.scripts.test_stack_refresh_logic import REFRESH, bash_funcs, run_bash

PRE = ("REPO_PATH=/opt/corporatetraveldc/private/ctdi-dispatch-internal; "
       "WEBSITE_PATH=/opt/corporatetraveldc/private/csexecutiveservices-website; MODE=weekly\n")


def funcs():
    return PRE + bash_funcs("rollout_rows", "rollout_units", "classify_drift", "failed_rollout_count") + "\n"


def classify(text):
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(text)
    try:
        return run_bash(funcs() + f'classify_drift < "{f.name}"').splitlines()
    finally:
        os.unlink(f.name)


def test_failed_unit_outside_the_rollout_is_a_note():
    out = classify("[OK] fine\n[DRIFT] unit corporatetraveldc-airline-time-fidelity is failed/crash-looping "
                   "and is absent from CLAUDE.md's Known bad section\n")
    assert out == ["NOTE [DRIFT] unit corporatetraveldc-airline-time-fidelity is failed/crash-looping "
                   "and is absent from CLAUDE.md's Known bad section"]


def test_failed_rollout_unit_still_holds():
    out = classify("[DRIFT] unit corporatetraveldc-ingest-fdps is failed/crash-looping and is absent\n")
    assert out and out[0].startswith("AUDIT ")


def test_system_unit_failure_is_reported_not_blocking():
    out = classify("[DRIFT] SYSTEM unit nginx is failed/crash-looping and is absent\n")
    assert out and out[0].startswith("NOTE ")


def test_other_drift_kinds_still_hold():
    out = classify("[DRIFT] quadlet corporatetraveldc-web.container differs from live\n"
                   "[DRIFT] Known bad section is 9 days old (2026-10-10) -- re-verify or restamp\n")
    assert [l.split(" ", 1)[0] for l in out] == ["AUDIT", "AUDIT"]


def test_failed_rollout_count_ignores_units_outside_the_rollout(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "systemctl").write_text(
        "#!/bin/bash\nprintf '%s\\n' 'corporatetraveldc-airline-time-fidelity.service loaded failed failed x' "
        "'corporatetraveldc-ingest-fdps.service loaded failed failed y'\n")
    (b / "systemctl").chmod(0o755)
    out = run_bash(funcs() + "failed_rollout_count", env={"PATH": f"{b}:{os.environ['PATH']}"})
    assert out.strip() == "1"


def test_verdict_uses_the_rollout_scoped_count():
    src = Path(REFRESH).read_text()
    assert "if (( rrc != 0 || FAILED_ROLLOUT != 0 ||" in src
    assert "FAILED != 0" not in src
