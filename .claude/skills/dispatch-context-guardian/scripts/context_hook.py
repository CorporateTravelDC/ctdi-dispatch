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

# 2026-08-16: was a hardcoded absolute path -- broke silently when the skill
# got relocated (see git history: "relocate dispatch-context-guardian skill
# to stable path") because this constant was never updated to match. Derived
# from the script's own location instead so a future relocate can't break it
# the same way again.
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVE_SCRIPT = os.path.join(SKILL_DIR, "scripts", "save_dispatch_state.py")


def _context_tokens_from_transcript(path, tail_bytes=512 * 1024):
    """Current context occupancy from the transcript's last assistant usage
    block. Tail-only read; returns 0 on any problem (the hook then stays
    silent, exactly as before -- never blocks a turn)."""
    if not path or not os.path.isfile(path):
        return 0
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            chunk = f.read().decode("utf-8", errors="replace")
        last = None
        for line in chunk.splitlines():
            if '"usage"' not in line:
                continue
            try:
                usage = (json.loads(line).get("message") or {}).get("usage")
            except Exception:
                continue
            if isinstance(usage, dict) and "input_tokens" in usage:
                last = usage
        if not last:
            return 0
        return int(last.get("input_tokens", 0)) + int(last.get("cache_read_input_tokens", 0)) \
            + int(last.get("cache_creation_input_tokens", 0))
    except Exception:
        return 0


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

    # 2026-10-03: Claude Code's Stop payload carries NO usage block -- only
    # session_id / transcript_path / stop_hook_active -- so both reads above
    # yield 0 and this hook exited silently on every turn since 2026-08-16
    # (last snapshot). The live context size is in the transcript JSONL:
    # the most recent assistant message's usage, input + cache_read +
    # cache_creation (output tokens are not context). Read only the tail --
    # a long session's transcript is 100 MB+ and this runs every turn.
    if total == 0:
        total = _context_tokens_from_transcript(payload.get("transcript_path"))

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
