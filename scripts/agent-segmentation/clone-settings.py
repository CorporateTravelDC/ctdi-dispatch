#!/usr/bin/env python3
"""scripts/agent-segmentation/clone-settings.py BASELINE HOME -- an operator-clone agent's settings.json.

The operator's EA (2026-10-26) runs with the operator's own signed Claude Code
settings baseline (config/claude-code/user-settings.baseline.json). The only
change is the status line, which points at the account's own copy of the
signed config/claude-code/statusline-command.sh (an agent cannot read the
operator's home). Any OTHER reference to the operator's home in the baseline
is refused rather than silently carried over: it would either fail for the
agent or point it at the operator's files. Prints the JSON to stdout.
"""
from __future__ import annotations

import json
import re
import sys

OPERATOR_HOME = "/home/corporatetraveldc"


def clone(baseline: dict, home: str) -> dict:
    if not re.fullmatch(r"/home/ctdc-[a-z0-9-]{1,48}", home):
        raise ValueError(f"{home!r} is not a team account home")
    out = json.loads(json.dumps(baseline))
    sl = out.get("statusLine")
    if isinstance(sl, dict) and "command" in sl:
        sl["command"] = f"bash {home}/.claude/statusline-command.sh"
    leftover = [m for m in re.findall(re.escape(OPERATOR_HOME) + r"[^\"\s]*", json.dumps(out))]
    if leftover:
        raise ValueError(f"baseline still references the operator's home: {sorted(set(leftover))}")
    return out


def comparable(settings: dict) -> str:
    """Canonical form for drift checks (verify.sh 5f): hooks are left out because the
    hourly skill-grants apply merges the context-guardian hooks into every agent's file."""
    return json.dumps({k: v for k, v in settings.items() if k != "hooks"}, sort_keys=True)


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--keys":
        print(comparable(json.load(sys.stdin)))
        return 0
    if len(argv) != 3:
        print(__doc__.splitlines()[0], file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        baseline = json.load(f)
    json.dump(clone(baseline, argv[2].rstrip("/")), sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
