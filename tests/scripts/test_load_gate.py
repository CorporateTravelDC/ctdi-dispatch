"""tests/scripts/test_load_gate.py -- 2026-10-13: the rollout's hold-before-restart gate is relative to
the start load and bounded; a resting load no longer holds a restart forever (acarshub held 45 min)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "scripts/lib/load_gate.sh"


def run(tmp_path, loads, script="load_gate_wait; echo rc=$?", **env):
    """loads: the load1 values the gate reads, one per check (the last one repeats)."""
    seq = tmp_path / "seq"
    seq.write_text("\n".join(str(x) for x in loads) + "\n")
    # load_gate_now is replaced by a stub that pops the next value on each read
    pre = (f'load_gate_now() {{ local v; v=$(head -1 {seq}); '
           f'[[ $(wc -l < {seq}) -gt 1 ]] && sed -i 1d {seq}; echo "$v"; }}; ')
    e = dict(os.environ, LOAD_GATE_STEP="1", **env)
    r = subprocess.run(["bash", "-c", f"set -euo pipefail; source {LIB}; {pre}{script}"], env=e,
                       capture_output=True, text=True, timeout=60)
    return r.stdout + r.stderr


def test_limit_is_relative_to_the_start_load(tmp_path):
    out = run(tmp_path, [20], "load_gate_init", LOAD_GATE_START="20")
    assert "hold while load1 >= 25.00" in out                         # 1.25 x 20, above the floor of 15
    out = run(tmp_path, [4], "load_gate_init", LOAD_GATE_START="4")
    assert "hold while load1 >= 15.00" in out                         # never below the absolute floor


def test_resting_load_passes_without_holding(tmp_path):
    out = run(tmp_path, [21, 21], LOAD_GATE_START="20")               # the 10-13 case: busy, not spiking
    assert "rc=0" in out and "holding" not in out


def test_a_spike_holds_until_it_passes(tmp_path):
    out = run(tmp_path, [32, 31, 18], LOAD_GATE_START="20")
    assert out.count("holding before restart") == 2 and "rc=0" in out


def test_hold_is_bounded_below_the_ceiling(tmp_path):
    out = run(tmp_path, [27], LOAD_GATE_START="20", LOAD_HOLD_MAX="3", LOAD_CEIL="30")
    assert "proceeding (resting load, not a spike)" in out and "rc=0" in out


def test_a_real_overload_keeps_holding_and_the_deadline_wins(tmp_path):
    out = run(tmp_path, [40], 'n=0; past_deadline() { n=$((n+1)); (( n > 6 )); }; '
              'rc=0; load_gate_wait || rc=$?; echo rc=$rc',
              LOAD_GATE_START="20", LOAD_HOLD_MAX="2", LOAD_CEIL="30")
    assert "proceeding" not in out and "rc=2" in out
