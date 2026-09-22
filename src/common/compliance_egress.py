"""
common.compliance_egress -- optional outbound push of this platform's own
audit_log records to an operator-configured external recordkeeping system
(a SIEM, Global Relay, Smarsh, or anything else that accepts an HTTP POST
of JSON).

Built 2026-08-03. This corrects docs/COMPLIANCE_SECURITY.md, which
previously (and inaccurately) stated no such mechanism existed anywhere in
this codebase -- that was true of the code at the time, but not of the
intent, and the document should never have denied something the operator
actually wanted built. It exists now, for real, and ships disabled.

Ships DISABLED by default -- same activation pattern already used for the
inbound webhooks in web/routes/webhooks.py: the code is real and ready,
but does nothing until an operator explicitly sets both
COMPLIANCE_HOOK_ENABLED=true and COMPLIANCE_TARGET_URL. There is no
default target anywhere in this file -- it will never phone home to
"firm.local" or anything else by itself.

Config, non-secret (/etc/corporatetraveldc/dispatch.env):
    COMPLIANCE_HOOK_ENABLED   "true"/"false" (default: false/unset)
    COMPLIANCE_RETRY_LIMIT    integer, default 5 -- after this many failed
                              attempts a record is marked failed_permanent
                              in audit_log.egress_status and stops being
                              retried every cycle. Still visible via
                              GET /admin/audit for manual follow-up.

Config, secret (/etc/corporatetraveldc/dispatch-secrets.env -- these can
reveal or grant access to an internal endpoint, unlike the boolean/int
above):
    COMPLIANCE_TARGET_URL          the operator's own endpoint. No default,
                                   no fallback -- required for anything to
                                   ship even if the enabled flag is true.
    COMPLIANCE_TARGET_AUTH_HEADER  optional -- sent verbatim as the
                                   Authorization header, for targets that
                                   require their own API key/bearer token.

What actually gets sent: the real audit_log columns only (record id,
event time, action, tier, token prefix, remote address, JSON detail).
No PNR, reservation, or travel-booking fields -- this platform has no such
data model, and this envelope doesn't invent one.

Runs on a timer (see scripts/compliance-egress-push.sh +
corporatetraveldc-compliance-egress-push.timer), not synchronously inside
db.audit() -- a batch-and-retry design tolerates a slow or temporarily
unreachable external target without blocking whatever request just wrote
the audit row.
"""
from __future__ import annotations

import json
import logging

import httpx

from common import config, db

log = logging.getLogger("common.compliance_egress")

_DEFAULT_RETRY_LIMIT = 5
_BATCH_SIZE = 50
_TIMEOUT_SECS = 10


def is_enabled() -> bool:
    return config.get("COMPLIANCE_HOOK_ENABLED", "false").strip().lower() == "true"


def _retry_limit() -> int:
    try:
        return int(config.get("COMPLIANCE_RETRY_LIMIT", str(_DEFAULT_RETRY_LIMIT)))
    except ValueError:
        return _DEFAULT_RETRY_LIMIT


def _envelope(row: dict) -> dict:
    """Real audit_log columns only -- see module docstring. Deliberately
    flat and small; if an operator's target needs a different shape,
    that's a real integration conversation to have with them, not
    something to guess a "format" flag for ahead of time."""
    return {
        "record_id": f"ctdi-audit-{row['id']}",
        "event_time_utc": row["event_time"],
        "source_node": config.get("NODE_LABEL", "corporatetraveldc-dispatch"),
        "action": row["action"],
        "tier": row["tier"],
        "token_prefix": row.get("token_prefix"),
        "remote_addr": row.get("remote_addr"),
        "detail": json.loads(row["detail"]) if row.get("detail") else None,
    }


def push_pending_audit_events() -> dict:
    """Push any unshipped audit_log rows to COMPLIANCE_TARGET_URL. Never
    raises -- a misconfigured or unreachable target degrades to "nothing
    shipped this run" so the timer that calls this doesn't need its own
    try/except, and one bad run doesn't take the poller down."""
    if not is_enabled():
        return {"enabled": False, "shipped": 0, "failed": 0}

    target_url = config.get("COMPLIANCE_TARGET_URL", "").strip()
    if not target_url:
        log.warning(
            "compliance_egress: COMPLIANCE_HOOK_ENABLED=true but "
            "COMPLIANCE_TARGET_URL is not set -- nothing to push to, skipping."
        )
        return {"enabled": True, "shipped": 0, "failed": 0,
                 "error": "COMPLIANCE_TARGET_URL not set"}

    retry_limit = _retry_limit()
    auth_header = config.get("COMPLIANCE_TARGET_AUTH_HEADER", "").strip()
    headers = {"Content-Type": "application/json"}
    if auth_header:
        headers["Authorization"] = auth_header

    rows = db.get_unshipped_audit_events(limit=_BATCH_SIZE, retry_limit=retry_limit)
    shipped = failed = 0
    for row in rows:
        try:
            with httpx.Client(timeout=_TIMEOUT_SECS) as client:
                r = client.post(target_url, json=_envelope(row), headers=headers)
            if 200 <= r.status_code < 300:
                db.mark_audit_shipped(row["id"])
                shipped += 1
            else:
                db.mark_audit_egress_failed(row["id"], f"HTTP {r.status_code}: {r.text[:200]}", retry_limit)
                failed += 1
        except Exception as e:
            db.mark_audit_egress_failed(row["id"], str(e)[:500], retry_limit)
            failed += 1

    if shipped or failed:
        log.info("compliance_egress: pushed %d, failed %d (target=%s)",
                  shipped, failed, target_url)
    return {"enabled": True, "shipped": shipped, "failed": failed}


if __name__ == "__main__":
    print(json.dumps(push_pending_audit_events()))
