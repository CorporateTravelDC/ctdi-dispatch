"""
board_sweep -- dispatch-side automated poll of the Cowork<->Dispatch message
board for new to:"dispatch" messages. The mirror of Cowork's own scheduled
research sweep, so coordination is fully bidirectional: neither side relies on
manual checks.

Runs on a systemd timer (hourly). Reads the board DIRECTLY via common.db (same
SQLite -- no HTTP/network hop needed), tracks a per-thread cursor so it only
surfaces NEW messages, appends them to a durable dispatch-side inbox log, and
pushes an ntfy ping so incoming coordination isn't missed. READ-ONLY wrt the
board -- it never posts; replying is a human / dispatch-session action.

SR-2: exempt -- deterministic, no LLM call.
"""
import json
import logging
import os

from common import db, ntfy_push
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "board-sweep"
_STATE_DIR = "/var/lib/corporatetraveldc/skill-state"
_CURSOR_FILE = os.path.join(_STATE_DIR, "board-sweep.json")
_INBOX_LOG = os.path.join(_STATE_DIR, "board-inbox.jsonl")
# The two named threads: "coord" (active back-and-forth) + "research" (series
# work, the thread Cowork's own weekly sweep watches). Sweep both for to:dispatch.
_THREADS = ("coord", "research")
_SELF = "dispatch"


def _load_cursors() -> dict:
    try:
        with open(_CURSOR_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cursors(cursors: dict) -> None:
    os.makedirs(_STATE_DIR, exist_ok=True)
    tmp = _CURSOR_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cursors, f)
    os.replace(tmp, _CURSOR_FILE)


def _append_inbox(msgs: list) -> None:
    if not msgs:
        return
    os.makedirs(_STATE_DIR, exist_ok=True)
    with open(_INBOX_LOG, "a") as f:
        for m in msgs:
            f.write(json.dumps(m) + "\n")


def run_sweep() -> dict:
    """Poll each thread since its stored cursor; return new to:dispatch messages
    and advance cursors past everything seen (so already-swept coord chatter
    isn't re-scanned). Never raises to the timer -- a broken sweep must not
    become an outage."""
    cursors = _load_cursors()
    new_for_dispatch = []
    for thread in _THREADS:
        since = cursors.get(thread)
        msgs, cursor = db.board_query(thread=thread, since=since, limit=200)
        new_for_dispatch.extend(
            m for m in msgs if (m.get("to") or "").lower() == _SELF
        )
        # advance to the newest SEEN seq (not just ours) so the next run starts
        # after all currently-visible messages in this thread
        if cursor and cursor != (since or ""):
            cursors[thread] = cursor
    _save_cursors(cursors)
    _append_inbox(new_for_dispatch)
    return {"new": new_for_dispatch, "cursors": cursors}


def main() -> None:
    status = "ok"
    try:
        result = run_sweep()
        new = result["new"]
        if new:
            lines = [
                f"- [{m.get('thread')}] {m.get('from', '?')}: {m.get('subject', '(no subject)')}"
                + (f"  refs={m['refs']}" if m.get("refs") else "")
                for m in new
            ]
            body = "\n".join(lines)
            log.info("%s: %d new to:dispatch message(s):\n%s", SKILL_NAME, len(new), body)
            ntfy_push.send(
                "ops-health",
                f"Board: {len(new)} new coordination message(s) for dispatch:\n{body}",
                title="Board: new message(s) for dispatch",
                priority=3, tags="incoming_envelope",
            )
        else:
            log.info("%s: no new to:dispatch messages (cursors=%s)", SKILL_NAME, result["cursors"])
    except Exception as e:
        log.error("%s: sweep failed: %s", SKILL_NAME, e)
        status = "error"
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
