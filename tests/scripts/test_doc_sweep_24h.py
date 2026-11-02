"""2026-11-02 (pre-pentest docs parity): the daily doc sweep keeps drift to 24 hours.

(a) coverage: SECURITY.md, CONTRIBUTING*, every undated doc under docs/ (not only its top level), the
    .agents skill twins, the pamphlet template and src/ READMEs are claims; dated records, frozen
    investor/security-review folders and untracked files are not;
(b) a first-seen ledger keyed on doc + claim (never the line number) turns a miss open longer than
    24 h into an [AGED] line, and the daily script pages p4 on it;
(c) the public mirror ref is compared: dead links to scrub-dropped files, and docs changed on HEAD
    after the mirror commit that were not published within 24 h.
Synthetic trees and throwaway git repos only: no systemctl, no ntfy, never the operator checkout.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("dcc24", REPO / "scripts" / "doc-claims-check.py")
dcc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dcc)

DAY = 86400.0
T0 = 1_790_000_000.0


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _git(root: Path, *args: str, when: float | None = None) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{int(when)} +0000"
    return subprocess.run(["git", "-C", str(root), *args], env=env, check=True,
                          capture_output=True, text=True).stdout


def test_coverage_reaches_security_subfolders_skill_twins_template_and_src_readmes(tmp_path):
    for rel in ("README.md", "SECURITY.md", "CONTRIBUTING.md", "docs/TOP.md", "docs/deployment-profiles/README.md",
                "docs/ops/RUNBOOK.md", ".agents/skills/x/SKILL.md", "skills/x/SKILL.md",
                "scripts/agent-segmentation/onboarding-template.md", "src/ingest/README.md",
                # never claims: dated records, frozen folders, the retired list
                "docs/OLD_2026-01-01.md", "docs/docs-refresh-2026-10-06/CHANGES.md",
                "docs/investor-materials/v1/faq.md", "docs/security-reviews/r1.md", "docs/RETIRED.md"):
        _write(tmp_path, rel, "x\n")
    got = {p.relative_to(tmp_path).as_posix() for p in dcc.collect_docs(tmp_path)}
    assert got == {"README.md", "SECURITY.md", "CONTRIBUTING.md", "docs/TOP.md", "docs/deployment-profiles/README.md",
                   "docs/ops/RUNBOOK.md", ".agents/skills/x/SKILL.md", "skills/x/SKILL.md",
                   "scripts/agent-segmentation/onboarding-template.md", "src/ingest/README.md"}


def test_in_a_checkout_only_tracked_docs_are_claims(tmp_path):
    _git(tmp_path, "init", "-q")
    _write(tmp_path, "SECURITY.md", "x\n")
    _write(tmp_path, "src/runner/frontend/node_modules/pkg/README.md", "x\n")   # untracked vendor README
    _git(tmp_path, "add", "SECURITY.md")
    got = {p.relative_to(tmp_path).as_posix() for p in dcc.collect_docs(tmp_path)}
    assert got == {"SECURITY.md"}


def test_agent_skill_twin_paths_resolve_skill_relative(tmp_path):
    _write(tmp_path, ".agents/skills/x/SKILL.md", "Run `scripts/run.py`.\n")
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"tracked": [".agents/skills/x/scripts/run.py"], "units": [], "timers": {}}))
    drift, _, _ = dcc.check(tmp_path, dcc.LiveFacts(tmp_path, facts))
    assert drift == []


def test_ledger_ages_a_miss_past_24h_by_claim_not_line(tmp_path):
    ledger = tmp_path / "state" / "ledger.json"
    first = ["[DRIFT] SECURITY.md:10: path not tracked: scripts/gone.sh"]
    assert dcc.age_ledger(ledger, first, T0, 24) == []
    # the doc gained lines above the claim: same miss, same first-seen time
    moved = ["[DRIFT] SECURITY.md:14: path not tracked: scripts/gone.sh",
             "[DRIFT] docs/X.md:3: route not in src/: /api/v1/new"]
    assert dcc.age_ledger(ledger, moved, T0 + 23 * 3600, 24) == []
    aged = dcc.age_ledger(ledger, moved, T0 + 25 * 3600, 24)
    assert len(aged) == 1 and aged[0].startswith("[AGED] open 25h (since ")
    assert aged[0].endswith("SECURITY.md:14: path not tracked: scripts/gone.sh")
    # fixed: drops out of the ledger, so a later regression starts its own clock
    assert dcc.age_ledger(ledger, [], T0 + 26 * 3600, 24) == []
    assert json.loads(ledger.read_text()) == {}


def test_unreadable_ledger_starts_fresh(tmp_path):
    ledger = tmp_path / "ledger.json"
    ledger.write_text("not json")
    assert dcc.age_ledger(ledger, ["[DRIFT] README.md:1: path not tracked: scripts/a.sh"], T0, 24) == []
    assert len(json.loads(ledger.read_text())) == 1


def _mirror_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _write(root, "README.md", "See [infra](docs/INFRA_MAP.md), [ok](docs/X.md), [web](https://example.com/a), "
                              "[anchor](#top) and [img](docs/images/a.png).\n")
    _write(root, "docs/X.md", "v1\n")
    _write(root, "docs/images/a.png", "png\n")
    _write(root, "docs/INFRA_MAP.md", "private\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "private 1", when=T0)
    # the scrubbed mirror: same docs minus the private map and the unreviewed image
    _git(root, "checkout", "-q", "-b", "mirror")
    _git(root, "rm", "-q", "docs/INFRA_MAP.md", "docs/images/a.png")
    _git(root, "commit", "-q", "-m", "sanitize", when=T0 + 60)
    _git(root, "update-ref", "refs/remotes/public/main", "mirror")
    _git(root, "checkout", "-q", "main")
    return root


def test_public_mirror_dead_links_and_unpublished_doc_changes(tmp_path):
    root = _mirror_repo(tmp_path)
    _write(root, "docs/X.md", "v2\n")
    _git(root, "commit", "-q", "-am", "docs change", when=T0 + 3600)
    _write(root, "docs/NEW.md", "not in the mirror yet\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "new doc", when=T0 + 7200)
    within = dcc.public_parity(root, "public/main", T0 + 20 * 3600, 24)
    assert "[DRIFT] public:README.md:1: link target not in the public mirror: docs/INFRA_MAP.md" in within
    assert "[DRIFT] public:README.md:1: link target not in the public mirror: docs/images/a.png" in within
    assert not any("example.com" in d or "#top" in d or "docs/X.md" in d for d in within)
    assert not any("changed on HEAD" in d for d in within)               # inside the 24 h bound
    late = dcc.public_parity(root, "public/main", T0 + 30 * 3600, 24)
    lag = [d for d in late if "changed on HEAD" in d]
    assert len(lag) == 1 and lag[0].startswith("[DRIFT] public:docs/X.md: changed on HEAD in ")
    assert "29h ago" in lag[0]


def test_missing_public_ref_is_a_finding_not_a_crash(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q")
    assert dcc.public_parity(root, "public/main", T0, 24) == [
        "[DRIFT] public:public/main: ref not found -- the mirror cannot be compared"]


def test_second_daily_sighting_ages_at_the_script_threshold(tmp_path):
    ledger = tmp_path / "l.json"
    miss = ["[DRIFT] README.md:1: path not tracked: scripts/a.sh"]
    assert dcc.age_ledger(ledger, miss, T0, 20) == []
    assert len(dcc.age_ledger(ledger, miss, T0 + DAY - 5, 20)) == 1          # next 09:00 run, a few s early


def test_cli_summary_carries_aged_count_only_with_a_ledger(tmp_path):
    root = tmp_path / "t"
    _write(root, "SECURITY.md", "`scripts/gone.sh`\n")
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"tracked": [], "units": [], "timers": {}}))
    cmd = ["python3", str(REPO / "scripts" / "doc-claims-check.py"), "--claims-only", str(root),
           "--live-facts", str(facts)]
    plain = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert plain.returncode == 1 and plain.stdout.rstrip().endswith("misses=1")
    led = cmd + ["--age-ledger", str(tmp_path / "l.json"), "--now", str(T0)]
    subprocess.run(led, capture_output=True, text=True, timeout=60)
    later = subprocess.run(led[:-1] + [str(T0 + 2 * DAY)], capture_output=True, text=True, timeout=60)
    assert "[AGED] open 48h" in later.stdout and later.stdout.rstrip().endswith("misses=1 aged=1")


def test_daily_script_uses_the_ledger_the_mirror_and_pages_p4_past_24h():
    src = (REPO / "scripts" / "weekly-doc-drift-check.sh").read_text()
    assert '--age-ledger "${CLAIMS_LEDGER}" --max-age-hours 20' in src     # second daily sighting = aged
    assert "PUBLIC_ARGS=(--public-ref public/main)" in src
    assert "git fetch" not in src                                         # the mirror ref is never fetched here
    assert 'Doc drift over 24h: ${CLAIM_AGED} miss(es)' in src and '-- see ${REPORT}" 4' in src
    # every day is still covered once: Tue-Sun daily + Monday weekly, both the same script
    units = REPO / ".config" / "systemd" / "user"
    assert "OnCalendar=Tue..Sun *-*-* 09:00:00 UTC" in (units / "corporatetraveldc-docs-drift-daily.timer").read_text()
    assert "OnCalendar=Mon *-*-* 09:00:00 UTC" in (units / "corporatetraveldc-docs-drift-weekly.timer").read_text()
    for svc in ("daily", "weekly"):
        assert "weekly-doc-drift-check.sh" in (units / f"corporatetraveldc-docs-drift-{svc}.service").read_text()
