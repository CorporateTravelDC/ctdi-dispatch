"""
2026-10-16 (operator): the drift jobs. (a) scripts/weekly-doc-drift-check.sh
checks PROSE docs against the live system again through
scripts/doc-claims-check.py (repo paths, unit names, documented timer
schedules); (b) both drift timers run on UTC and the weekly service no longer
claims "Fable, both repos". Synthetic docs dir + synthetic live facts: no git,
no systemctl, no ntfy.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "doc-claims-check.py"
WEEKLY = REPO / "scripts" / "weekly-doc-drift-check.sh"
UNITS = REPO / ".config" / "systemd" / "user"


def _synthetic_repo(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "skills" / "demo").mkdir(parents=True)
    (root / "README.md").write_text(
        "See `scripts/present.sh` and `src/pkg/mod.py`; the unit `corporatetraveldc-present.service` runs it.\n"
        "`corporatetraveldc-present.timer` fires `OnCalendar=Mon *-*-* 09:00:00 UTC`.\n"
        "Gone: `scripts/missing-from-readme.sh`.\n")
    (root / "docs" / "LIVE.md").write_text(
        "| `corporatetraveldc-present.timer` | `OnCalendar=*-*-* 05:15:00 America/New_York` |\n"
        "~~`scripts/struck-and-gone.sh`~~ replaced by `scripts/present.sh`.\n"
        "<del>the old `corporatetraveldc-struck.service`\nwas here</del>\n"
        "Fenced:\n```\nscripts/in-a-fence-gone.sh\n```\n"
        "Glob `docs/LIVE_STATE_CHECK_*.md` and placeholder `scripts/<name>.sh` are not claims.\n"
        "`corporatetraveldc-absent.timer` (retired) and `corporatetraveldc-notify@%n.service`.\n")
    (root / "docs" / "OLD_2026-01-01.md").write_text("`scripts/dated-doc-gone.sh`\n")
    (root / "skills" / "demo" / "SKILL.md").write_text(
        "Run `scripts/run.py` (skill-relative) and `scripts/skill-gone.py`.\n")
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({
        "tracked": ["scripts/present.sh", "src/pkg/mod.py", "skills/demo/scripts/run.py",
                    "README.md", "docs/LIVE.md"],
        "units": ["corporatetraveldc-present.service", "corporatetraveldc-present.timer",
                  "corporatetraveldc-notify@.service"],
        "timers": {"corporatetraveldc-present.timer":
                   {"OnCalendar": ["Mon *-*-* 09:00:00 UTC"], "OnUnitActiveSec": []}},
    }))
    return root, facts


def _run(root: Path, facts: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", str(CHECK), "--claims-only", str(root), "--live-facts", str(facts)],
                          capture_output=True, text=True, timeout=60)


def test_claims_extractor_finds_the_planted_misses(tmp_path):
    root, facts = _synthetic_repo(tmp_path)
    r = _run(root, facts)
    drift = [ln for ln in r.stdout.splitlines() if ln.startswith("[DRIFT]")]
    assert r.returncode == 1, r.stdout + r.stderr
    assert "[DRIFT] README.md:3: path not tracked: scripts/missing-from-readme.sh" in drift
    assert "[DRIFT] skills/demo/SKILL.md:1: path not tracked: scripts/skill-gone.py" in drift
    assert "[DRIFT] docs/LIVE.md:10: unit not installed: corporatetraveldc-absent.timer" in drift
    assert "[DRIFT] docs/LIVE.md:7: path not tracked: scripts/in-a-fence-gone.sh" in drift   # fenced commands are claims
    wrong = [ln for ln in drift if "documents OnCalendar=*-*-* 05:15:00 America/New_York" in ln]
    assert len(wrong) == 1 and wrong[0].startswith("[DRIFT] docs/LIVE.md:1: corporatetraveldc-present.timer")
    assert "live OnCalendar=Mon *-*-* 09:00:00 UTC" in wrong[0]
    assert len(drift) == 5, drift
    assert re.search(r"^\[SUMMARY\] docs=3 claims=\d+ misses=5$", r.stdout, re.M)


def test_claims_extractor_skips_struck_dated_and_placeholders(tmp_path):
    root, facts = _synthetic_repo(tmp_path)
    out = _run(root, facts).stdout
    for never in ("struck-and-gone", "corporatetraveldc-struck.service",
                  "dated-doc-gone", "LIVE_STATE_CHECK_", "<name>", "notify@%n", "scripts/run.py"):
        assert never not in out, never


def test_clean_docs_exit_zero(tmp_path):
    root = tmp_path / "clean"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text("`scripts/present.sh` under `corporatetraveldc-present.timer` "
                                    "`OnUnitActiveSec=15min`.\n")
    facts = tmp_path / "f.json"
    facts.write_text(json.dumps({"tracked": ["scripts/present.sh"],
                                 "units": ["corporatetraveldc-present.timer"],
                                 "timers": {"corporatetraveldc-present.timer":
                                            {"OnCalendar": [], "OnUnitActiveSec": ["15min"]}}}))
    r = _run(root, facts)
    assert r.returncode == 0 and "misses=0" in r.stdout, r.stdout


def test_checker_is_stdlib_only():
    src = CHECK.read_text()
    imports = set(re.findall(r"^(?:import|from)\s+([A-Za-z_][\w.]*)", src, re.M))
    assert imports <= {"argparse", "json", "os", "re", "subprocess", "sys", "pathlib", "__future__"}, imports


def test_weekly_script_runs_the_claims_check_and_reports_the_count():
    src = WEEKLY.read_text()
    assert "scripts/doc-claims-check.py" in src
    assert "## Doc claims vs live system" in src
    assert "doc-claim miss(es)" in src and "CLAIM_MISSES" in src
    assert "authhdr auth_args" in src            # ntfy token on a private fd, never argv
    assert "local-llm-synthesize.sh" in src      # narrative still optional, local only
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"\bclaude\s+(--model|-p)\b", code)   # no cloud model in the path


def test_drift_timers_declare_utc():
    daily = (UNITS / "corporatetraveldc-claude-md-drift-daily.timer").read_text()
    weekly = (UNITS / "corporatetraveldc-docs-drift-weekly.timer").read_text()
    assert re.search(r"^OnCalendar=\*-\*-\* 09:15:00 UTC$", daily, re.M)
    assert re.search(r"^OnCalendar=Mon \*-\*-\* 09:00:00 UTC$", weekly, re.M)
    for text in (daily, weekly):
        for ln in re.findall(r"^OnCalendar=.*$", text, re.M):
            assert ln.endswith(" UTC"), ln
        assert "America/New_York" not in "\n".join(re.findall(r"^OnCalendar=.*$", text, re.M))


def test_weekly_service_description_is_honest():
    svc = (UNITS / "corporatetraveldc-docs-drift-weekly.service").read_text()
    desc = re.search(r"^Description=(.*)$", svc, re.M).group(1)
    assert "Fable" not in desc and "both repos" not in desc
    assert "one repo" in desc and "optional" in desc
