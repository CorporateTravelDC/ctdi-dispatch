#!/usr/bin/env python3
"""
Claude Code Stop hook -- keeps the dispatch-state snapshot fresh as the
context fills, so Claude Code's OWN auto-compact (autoCompactWindow = 900000
in ~/.claude/settings.json) finds a current snapshot to restore from.
A hook cannot run /compact; Claude Code does that itself at the window.
Install in settings.json under hooks.Stop. Payload arrives on stdin as JSON;
output is a JSON systemMessage; always exits 0.
"""

import json
import os
import subprocess
import sys

WARN_LIMIT = 800_000   # start saving snapshots; Claude Code compacts at 900k

# 2026-08-16: was a hardcoded absolute path -- broke silently when the skill
# got relocated (see git history: "relocate dispatch-context-guardian skill
# to stable path") because this constant was never updated to match. Derived
# from the script's own location instead so a future relocate can't break it
# the same way again.
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVE_SCRIPT = os.path.join(SKILL_DIR, "scripts", "save_dispatch_state.py")
LOG_FILE = os.path.expanduser("~/.config/Claude/dispatch_context_guardian.log")

# 2026-10-03: the hook fired once at 963k then never re-saved across the next
# five 900k+ turns, and left no trace of why. Every exit path now appends one
# line here so the next silent miss is diagnosable. Kept tiny: one line per
# turn, truncated at ~200 KB.
def _log(msg):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 200_000:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        from datetime import datetime, timezone
        with open(LOG_FILE, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass


def _context_tokens_from_transcript(path, tail_bytes=4 * 1024 * 1024):
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
        _log("tokens=0 (no usage block found in transcript tail) -- exit silent")
        sys.exit(0)

    if total < WARN_LIMIT:
        _log(f"tokens={total} below warn -- exit silent")
        sys.exit(0)

    # Save dispatch state. 7 endpoints x 8s each can exceed the old 30s on a
    # loaded box, and a timed-out save writes nothing (file is written last).
    try:
        r = subprocess.run(
            ["python3", SAVE_SCRIPT, str(total)],
            timeout=90, check=False, capture_output=True, text=True
        )
        _log(f"tokens={total} save rc={r.returncode} "
             f"{(r.stderr or '').strip().splitlines()[-1][:120] if r.stderr else ''}")
    except subprocess.TimeoutExpired:
        _log(f"tokens={total} save TIMEOUT (90s) -- snapshot NOT updated")
    except Exception as e:
        _log(f"tokens={total} save error {type(e).__name__}: {e}")

    # 2026-10-05: this hook used to tell the operator to run /compact (and at
    # >= 900k blocked the stop so the reply said so). That was never what
    # compacted the session: Claude Code's built-in auto-compact did, at its
    # default ~967k on a 1M-window model -- so a session parked at 949k sat
    # there waiting on a manual /compact. Now Claude Code compacts by itself
    # at autoCompactWindow (900k, user settings) and this hook only saves the
    # snapshot each turn from WARN_LIMIT up and says whether the window is set.
    print(json.dumps({"systemMessage": f"[CONTEXT] {total:,} tokens -- dispatch state saved; "
                                       + _compact_note()}))
    sys.exit(0)


def _compact_note():
    """Report whether Claude Code will compact on its own at the 900k window."""
    try:
        with open(os.path.expanduser("~/.claude/settings.json")) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    if os.environ.get("DISABLE_AUTO_COMPACT") == "1" or cfg.get("autoCompactEnabled") is False:
        return "auto-compact is DISABLED -- run /compact by hand."
    win = os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") or cfg.get("autoCompactWindow")
    if not win:
        return "autoCompactWindow is NOT set -- Claude Code will not compact until ~967k."
    return f"Claude Code compacts automatically at {int(win):,}."


if __name__ == "__main__":
    main()
