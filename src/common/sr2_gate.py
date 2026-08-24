"""
SR-2: Hash gate.
Every automated skill calls hash_gate() before any Anthropic API call.
If inputs haven't changed, the gate returns "skipped" and the caller exits.
State dir: /var/lib/corporatetraveldc/skill-state/
"""

import hashlib
import json
from pathlib import Path

GATE_STATE_DIR = Path("/var/lib/corporatetraveldc/skill-state")


def hash_gate(skill_name: str, inputs: dict, force: bool = False) -> str:
    """
    Returns gate_result: "new" | "skipped" | "forced".

    "new"     → inputs changed; proceed with API call.
    "skipped" → inputs unchanged; caller should sys.exit(0).
    "forced"  → --force flag set; bypass gate, proceed.

    Caller is responsible for exiting cleanly on "skipped".
    Hash only content-bearing inputs — never timestamps or sequence numbers.
    """
    if force:
        return "forced"

    GATE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    gate_file = GATE_STATE_DIR / f"{skill_name}.hash"

    current_hash = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, default=str).encode()
    ).hexdigest()[:24]

    try:
        last_hash = gate_file.read_text().strip()
        if last_hash == current_hash:
            return "skipped"
    except FileNotFoundError:
        pass  # First run — no prior hash; proceed.

    gate_file.write_text(current_hash)
    return "new"
