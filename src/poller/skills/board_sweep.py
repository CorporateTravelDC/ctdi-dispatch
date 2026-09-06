"""
board_sweep -- dispatch-side automated poll of the Cowork<->Dispatch message
board for new to:"dispatch" messages. The mirror of Cowork's own scheduled
research sweep, so coordination is fully bidirectional: neither side relies on
manual checks.

Runs on a systemd timer (every 15 min). Reads the board DIRECTLY via
common.db (same SQLite -- no HTTP/network hop needed), tracks a per-thread
cursor so it only surfaces NEW messages, appends them to a durable
dispatch-side inbox log, and pushes an ntfy ping so incoming coordination
isn't missed.

2026-09-05 (operator directive): this skill was previously fully
READ-ONLY wrt the board ("it never posts; replying is a human /
dispatch-session action") -- that changes here, narrowly, for exactly one
case: the "token-gate" thread, where Cowork asks dispatch to relay a
GATE: APPROVED/DENIED decision before it proceeds with its own
already-autonomous board-write-token self-refresh (db.board_refresh_token
-- a courtesy check-in Cowork itself designed, NOT the server's real
security gate, which is the separate weekly GPG presence attestation
below and cannot be satisfied by any automated tap). See
_handle_token_gate()'s docstring for the full design. Every other thread
stays exactly as read-only as before.

Also handles a second, unrelated concern that happens to live in the same
15-min cadence for simplicity (no new timer/container needed): a loud,
dual-channel (ntfy + email) proactive reminder that the weekly
presence-attestation window (scripts/board-presence-attest.sh) is coming
due. See _check_presence_reminder().

SR-2: exempt -- deterministic, no LLM call.
"""
import datetime as _dt
import json
import logging
import os
import re
import time
import uuid

import requests

from common import config, db, ntfy_push
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "board-sweep"
_STATE_DIR = "/var/lib/corporatetraveldc/skill-state"
_CURSOR_FILE = os.path.join(_STATE_DIR, "board-sweep.json")
_INBOX_LOG = os.path.join(_STATE_DIR, "board-inbox.jsonl")
_GATE_STATE_FILE = os.path.join(_STATE_DIR, "board-token-gate-state.json")
_PRESENCE_STATE_FILE = os.path.join(_STATE_DIR, "board-presence-reminder-state.json")
# The three named threads: "coord" (active back-and-forth), "research"
# (series work, the thread Cowork's own weekly sweep watches), and
# "token-gate" (added 2026-09-05 -- previously unswept entirely, which is
# why a real Cowork dry-run request sat unseen for two days).
_TOKEN_GATE_THREAD = "token-gate"
_THREADS = ("coord", "research", _TOKEN_GATE_THREAD)
_SELF = "dispatch"

# How far ahead of a parsed deadline to fire the proactive Allow/Deny push
# (operator directive: "6 hours out or 12 hours out ... your call" -- 12h
# for a bigger buffer). The approval's TTL is set so it stops being
# tappable _GATE_DEADLINE_BUFFER_S before the real deadline, not at the
# full lead time -- "valid up until 10 minutes before the remint/refresh
# would be required."
_GATE_LEAD_HOURS = 12
_GATE_DEADLINE_BUFFER_S = 600
_RESOLVE_HOST = "https://dispatch.example.com"

_DEADLINE_RE = re.compile(
    r"expires\s+(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})Z", re.IGNORECASE
)

# Same lead time for the weekly presence-attestation reminder (separate
# mechanism, same "give a long lead time instead of a last-minute nag"
# philosophy, kept as one constant rather than a second magic number).
_PRESENCE_LEAD_HOURS = 12


def _parse_deadline(body: str) -> float | None:
    """Extract an 'expires <ISO datetime>Z' timestamp from free-text
    message body (Cowork's dry-run message read: "Token is healthy
    (expires 2026-09-04 14:19:15Z, outside the 6h window)"). Returns None
    if no such pattern is found -- callers must treat that as "couldn't
    determine a deadline," not silently assume one."""
    m = _DEADLINE_RE.search(body or "")
    if not m:
        return None
    raw = m.group(1)
    fmt = "%Y-%m-%dT%H:%M:%S" if "T" in raw else "%Y-%m-%d %H:%M:%S"
    try:
        return _dt.datetime.strptime(raw, fmt).replace(tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


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


def _load_json_state(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json_state(path: str, data: dict) -> None:
    os.makedirs(_STATE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _ntfy_gate_push(topic: str, title: str, message: str, priority: int,
                    allow_url: str, deny_url: str) -> None:
    """Allow/Deny action-button push -- common.ntfy_push.send() has no
    support for ntfy's JSON actions field, only plain-text pushes, so this
    posts the JSON payload directly (same shape as
    scripts/sudo-approval-gate.sh's own ntfy call)."""
    ntfy_token = config.ntfy_token().split(":")[0]
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": priority,
        "actions": [
            {"action": "http", "label": "Allow", "url": allow_url, "method": "GET", "clear": True},
            {"action": "http", "label": "Deny", "url": deny_url, "method": "GET", "clear": True},
        ],
    }
    headers = {"Authorization": f"Bearer {ntfy_token}"} if ntfy_token else {}
    try:
        requests.post(config.ntfy_url() + "/", json=payload, headers=headers, timeout=5)
    except Exception as e:
        log.warning("%s: ntfy gate push failed: %s", SKILL_NAME, e)


def _handle_token_gate(new_msgs: list) -> None:
    """Cowork's own courtesy gate-approval loop for its board-write token
    self-refresh (db.board_refresh_token) -- NOT the server's real
    security gate (the weekly presence attestation, see
    _check_presence_reminder(), which cannot be satisfied by any
    automated tap). This is a low-stakes coordination handshake Cowork
    itself designed and is waiting on; posting a reply here doesn't
    authorize anything the server enforces.

    Three things happen here, in order, every 15-min sweep:
      1. Register any newly-seen token-gate message, parsing its deadline
         (see _parse_deadline). If no deadline can be parsed, fire
         immediately rather than silently dropping it -- a coordination
         request with an ambiguous deadline still deserves a human's
         attention, just without the proactive lead time.
      2. For any registered message whose lead-time has arrived (now >=
         deadline - _GATE_LEAD_HOURS) and hasn't fired yet, create a real
         approval_requests row (db.create_approval_request) with a TTL
         that expires _GATE_DEADLINE_BUFFER_S before the real deadline,
         and push a real Allow/Deny ntfy action (not plain-text) -- same
         mechanism scripts/sudo-approval-gate.sh uses, called directly
         via common.db rather than shelling out (this skill already reads
         the board the same direct-DB way).
      3. For any already-fired request, check whether it's been resolved
         (tapped, or expired past its TTL) and if so post GATE:
         APPROVED/DENIED back to the board thread -- this is what closes
         the loop for Cowork, and is the one narrow exception to this
         skill's otherwise-read-only board contract.
    """
    state = _load_json_state(_GATE_STATE_FILE)
    now = time.time()
    changed = False

    for m in new_msgs:
        if m.get("thread") != _TOKEN_GATE_THREAD:
            continue
        mid = m["id"]
        if mid in state:
            continue
        deadline = _parse_deadline(m.get("body", ""))
        if deadline is None:
            log.warning(
                "%s: could not parse a deadline from token-gate message %s -- "
                "firing immediately instead of silently dropping it",
                SKILL_NAME, mid,
            )
            fire_at = now
            deadline_for_ttl = now + _GATE_DEADLINE_BUFFER_S + 60
        else:
            fire_at = deadline - _GATE_LEAD_HOURS * 3600
            deadline_for_ttl = deadline
        state[mid] = {
            "thread": m["thread"], "from": m.get("from"), "subject": m.get("subject"),
            "deadline": deadline, "fire_at": fire_at, "deadline_for_ttl": deadline_for_ttl,
            "approval_request_id": None, "replied": False,
        }
        changed = True

    for mid, entry in state.items():
        if entry.get("replied") or entry.get("approval_request_id"):
            continue
        if now < entry["fire_at"]:
            continue
        request_id = str(uuid.uuid4())
        ttl = max(60.0, entry["deadline_for_ttl"] - _GATE_DEADLINE_BUFFER_S - now)
        db.create_approval_request(
            request_id, "cowork-board-token-gate",
            f"Post GATE: APPROVED/DENIED reply to Cowork board message {mid} "
            f"({entry.get('subject')})",
            reasoning=f"Cowork token-gate request from {entry.get('from')}: {entry.get('subject')}",
            ttl_seconds=ttl,
        )
        entry["approval_request_id"] = request_id
        allow_url = f"{_RESOLVE_HOST}/admin/approval-requests/{request_id}/resolve?action=allow"
        deny_url = f"{_RESOLVE_HOST}/admin/approval-requests/{request_id}/resolve?action=deny"
        deadline_str = (
            time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(entry["deadline"]))
            if entry["deadline"] else "unknown"
        )
        _ntfy_gate_push(
            "approval-gate",
            f"Cowork board gate: {entry.get('subject')}",
            f"From Cowork: {entry.get('subject')}\ndeadline: {deadline_str}\n"
            f"valid to tap until {_GATE_DEADLINE_BUFFER_S // 60} min before deadline. "
            f"No tap = denied at expiry.",
            4,
            allow_url, deny_url,
        )
        changed = True
        log.info("%s: fired proactive token-gate approval %s for message %s",
                 SKILL_NAME, request_id, mid)

    for mid, entry in list(state.items()):
        if entry.get("replied") or not entry.get("approval_request_id"):
            continue
        req = db.get_approval_request(entry["approval_request_id"])
        if not req or req["status"] == "pending":
            continue
        reply = {
            "allowed": "GATE: APPROVED",
            "denied": "GATE: DENIED",
            "expired": "GATE: DENIED (expired, no response)",
        }.get(req["status"], "GATE: DENIED")
        db.board_insert(
            from_side=_SELF, to_side=entry.get("from") or "cowork", thread=_TOKEN_GATE_THREAD,
            subject=f"Re: {entry.get('subject')}", body=reply, in_reply_to=mid,
        )
        entry["replied"] = True
        changed = True
        log.info("%s: replied '%s' to token-gate message %s", SKILL_NAME, reply, mid)

    cutoff = now - 7 * 86400
    for mid in list(state.keys()):
        if state[mid].get("replied") and (state[mid].get("deadline") or 0) < cutoff:
            del state[mid]
            changed = True

    if changed:
        _save_json_state(_GATE_STATE_FILE, state)


def _fire_presence_alert(status: dict, override_reason: str | None) -> None:
    valid_until = status.get("valid_until")
    deadline_str = (
        time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(valid_until))
        if valid_until else "unknown -- never attested"
    )
    gate_state = "VALID" if status.get("valid") else "EXPIRED/MISSING"
    reason_line = f"{override_reason}.\n" if override_reason else ""
    body = (
        f"Weekly Cowork board-write presence attestation gate state: {gate_state}.\n"
        f"{reason_line}"
        f"Remint by {deadline_str} -- run scripts/board-presence-attest.sh yourself "
        f"(requires your GPG passphrase; this cannot be completed by tapping a notification)."
    )
    ntfy_push.send(
        "hot-alerts", body,
        title="Weekly board presence attestation due",
        priority=5, tags="key", email=True,
    )


def _check_presence_reminder() -> None:
    """Loud (ntfy + email), dual-channel, proactive reminder that the
    weekly GPG presence attestation (scripts/board-presence-attest.sh) is
    coming due -- fired _PRESENCE_LEAD_HOURS ahead of the real deadline,
    not a last-minute nag. Unlike _handle_token_gate above, there is
    nothing to auto-resolve here: the attestation genuinely requires the
    operator's own typed GPG passphrase, so this only ever reminds, never
    gates/approves anything itself."""
    status = db.board_presence_status()
    valid_until = status.get("valid_until")
    now = time.time()
    state = _load_json_state(_PRESENCE_STATE_FILE)

    if not valid_until:
        # Never attested at all -- always worth a loud nag, but debounce
        # to once/day so it doesn't fire on every 15-min sweep forever.
        if state.get("last_alerted_for") == "never" and now - state.get("last_alerted_at", 0) < 86400:
            return
        _fire_presence_alert(status, "No presence attestation on record at all")
        _save_json_state(_PRESENCE_STATE_FILE, {"last_alerted_for": "never", "last_alerted_at": now})
        return

    if valid_until - now > _PRESENCE_LEAD_HOURS * 3600:
        return  # not due yet

    if state.get("last_alerted_for") == valid_until:
        return  # already alerted for this exact attestation cycle -- a NEW
                # attestation changes valid_until, which naturally re-arms this

    _fire_presence_alert(status, None)
    _save_json_state(_PRESENCE_STATE_FILE, {"last_alerted_for": valid_until, "last_alerted_at": now})


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

        # Each of these has its own try/except internally via the outer
        # try here NOT catching them separately -- deliberate: a failure
        # in one must not be masked by "sweep succeeded" nor block the
        # other. Splitting into their own try/except below.
        try:
            _handle_token_gate(new)
        except Exception as e:
            log.error("%s: token-gate handling failed: %s", SKILL_NAME, e)
            status = "error"

        try:
            _check_presence_reminder()
        except Exception as e:
            log.error("%s: presence-reminder check failed: %s", SKILL_NAME, e)
            status = "error"
    except Exception as e:
        log.error("%s: sweep failed: %s", SKILL_NAME, e)
        status = "error"
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
