"""
SR-2: Hash gate.
Every automated skill calls check_gate() before any Anthropic API call.
If inputs haven't changed, the gate returns "skipped" and the caller exits.
State dir: /var/lib/corporatetraveldc/skill-state/
"""

import hashlib
import json
from pathlib import Path

GATE_STATE_DIR = Path("/var/lib/corporatetraveldc/skill-state")


def check_gate(skill_name: str, inputs: dict, force: bool = False) -> tuple[str, str]:
    """
    Returns (gate_result, current_hash): gate_result is "new" | "skipped" | "forced".

    "new"     → inputs changed; proceed with API call.
    "skipped" → inputs unchanged; caller should sys.exit(0).
    "forced"  → --force flag set; bypass gate, proceed.

    Read-only -- does NOT persist current_hash. Caller must call
    commit_gate(skill_name, current_hash) once the guarded work this hash
    corresponds to has actually completed; see that function's docstring
    for why the write is split out from the check.

    Hash only content-bearing inputs — never timestamps or sequence numbers.
    """
    current_hash = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, default=str).encode()
    ).hexdigest()[:24]

    if force:
        return "forced", current_hash

    gate_file = GATE_STATE_DIR / f"{skill_name}.hash"
    try:
        last_hash = gate_file.read_text().strip()
        if last_hash == current_hash:
            return "skipped", current_hash
    except FileNotFoundError:
        pass  # First run — no prior hash; proceed.

    return "new", current_hash


def commit_gate(skill_name: str, current_hash: str) -> None:
    """Persist current_hash as the last-processed hash for skill_name.

    2026-08-25 fix (Opus blind review C-7): the old combined hash_gate()
    wrote this hash BEFORE the caller did any of the guarded work, on the
    assumption the caller would always complete. If the caller crashed
    partway through (e.g. Ollama down, an exception in the compute step),
    the hash was already recorded for inputs whose work never actually
    finished -- the next run with the SAME inputs (e.g. a still-active
    VIP TFR, an unchanged flight status) then permanently returned
    "skipped" forever, silently suppressing retries until the underlying
    inputs happened to change on their own. Splitting the write out means
    a caller only commits the hash after confirming success (or an
    accepted fallback path), so a crash mid-run leaves the gate open for
    the next attempt instead of falsely marking it done.
    """
    GATE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    gate_file = GATE_STATE_DIR / f"{skill_name}.hash"
    gate_file.write_text(current_hash)
