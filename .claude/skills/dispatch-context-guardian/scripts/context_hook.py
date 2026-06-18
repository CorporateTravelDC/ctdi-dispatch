#!/usr/bin/env python3
"""
Claude Code Stop hook -- checks context token count and triggers compact if >= 900k.
Install in .claude/settings.json under hooks.Stop.

Hook payload arrives on stdin as JSON.
Output on stdout is shown to the user / Claude.
Non-zero exit code blocks the stop and shows the message.
"""

import json
import os
import subprocess
import sys

HARD_LIMIT = 900_000
WARN_LIMIT = 800_000

SKILL_DIR = "/opt/corporatetraveldc/.claude/skills/dispatch-context-guardian"
SAVE_SCRIPT = os.path.join(SKILL_DIR, "scripts", "save_dispatch_state.py")


def main():
    # Read hook payload from stdin
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    # Extract token usage
    usage = payload.get("usage", {})
    input_tokens  = usage.get("inputTokens", 0)
    cache_read    = usage.get("cacheReadInputTokens", 0)
    output_tokens = usage.get("outputTokens", 0)
    total = input_tokens + cache_read + output_tokens

    if total == 0:
        total = payload.get("session_context_tokens", 0)

    if total == 0:
        sys.exit(0)

    if total < WARN_LIMIT:
        sys.exit(0)

    # Save dispatch state
    try:
        subprocess.run(
            ["python3", SAVE_SCRIPT, str(total)],
            timeout=30,
            check=False
        )
    except Exception:
        pass

    if total >= HARD_LIMIT:
        print(
            f"\n[CONTEXT LIMIT] {total:,} tokens >= 900,000 hard limit.\n"
            f"Dispatch state saved to ~/.config/Claude/dispatch_state_snapshot.json\n"
            f"Run /compact now to reset context. After compacting, run:\n"
            f"  python3 {SKILL_DIR}/scripts/restore_dispatch_state.py\n"
            f"to restore dispatch situational awareness.",
            file=sys.stdout
        )
        sys.exit(1)
    else:
        print(
            f"[CONTEXT WARNING] {total:,} tokens -- approaching 900,000 limit. "
            f"Dispatch state saved.",
            file=sys.stdout
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
