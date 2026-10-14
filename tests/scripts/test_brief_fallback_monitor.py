"""2026-10-14 (operator): the brief fallback alert fires when a brief FALLS BACK on its latest run,
with a short narrative of which briefs and whether the trend is improving or worsening. The old
">= half the window" rule kept paging at max priority for hours after the model recovered."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/brief-fallback-monitor.sh"


def run(tmp_path, outcomes: dict[str, list[str]]):
    """outcomes: unit -> list of 'LLM'/'FALLBACK', oldest first. A fake journalctl serves them."""
    bindir = tmp_path / "bin"; bindir.mkdir()
    data = tmp_path / "data"; data.mkdir()
    for unit, seq in outcomes.items():
        rows = []
        for i, o in enumerate(seq):
            kind = "brief generated (deterministic fallback)" if o == "FALLBACK" else "brief generated (Ollama/m)"
            rows.append(f"Oct 14 1{i}:00:00 host {unit}[1]: INFO:__main__:x: {kind}")
        (data / unit).write_text("\n".join(rows) + "\n")
    (bindir / "journalctl").write_text(
        f'#!/bin/bash\nfor a in "$@"; do [[ -f "{data}/$a" ]] && cat "{data}/$a"; done; exit 0\n')
    (bindir / "journalctl").chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", DRY_RUN="1")
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    return r.stdout


def test_recovered_briefs_do_not_alert(tmp_path):      # the 10-14 15:50/16:50Z stale pages
    out = run(tmp_path, {"corporatetraveldc-ops-brief": ["LLM", "FALLBACK", "FALLBACK", "FALLBACK", "FALLBACK", "LLM"],
                         "corporatetraveldc-ep-advance": ["FALLBACK"] * 4 + ["LLM", "LLM"]})
    assert "would ntfy" not in out and "no brief fell back on its latest run" in out


def test_a_latest_fallback_alerts_with_which_and_trend(tmp_path):
    out = run(tmp_path, {"corporatetraveldc-ops-brief": ["LLM", "LLM", "LLM", "LLM", "FALLBACK", "FALLBACK"],
                         "corporatetraveldc-ep-advance": ["FALLBACK", "FALLBACK", "FALLBACK", "LLM", "LLM", "LLM"]})
    assert "would ntfy" in out and "Brief fallback: ops-brief" in out
    assert "ops-brief" in out and "trend=worsening (2 of last 3 vs 0 of prior 3)" in out
    assert "trend=improving (0 of last 3 vs 3 of prior 3)" in out
