"""
common.acars — ACARS/VDL2 authoritative flight state.

ACARS messages originate directly from aircraft avionics — they are the
most reliable source of flight phase truth available.  ADS-B is secondary.

Consumers:
  pusher  — all four OOOI phases drive _flight_state directly
  poller  — ACARS phase overrides ADS-B-derived OOOI transitions
"""

import logging
import os
import sqlite3
import time

log = logging.getLogger(__name__)

ACARSHUB_DB_PATH = os.environ.get(
    "ACARSHUB_DB_PATH",
    "/var/lib/corporatetraveldc/acarshub/messages.db",
)

# OOOI phase patterns confirmed from DC-area ACARS/VDL2 traffic.
# Each entry is (label, LIKE pattern).  Ordered most-reliable first.
#
# OUT — gate departure / pushback
#   label 31  /OUT HHMM   (JetBlue/B6 structured OOOI)
#   label H1  /OUT         (explicit OUT field, some Boeing types)
#
# OFF — wheels up / airborne
#   label H1  OFF OFF      (Boeing WOW status block — both main gear WOW off)
#   label H1  /OFF         (explicit OOOI OFF field)
#   label 31  /OFF HHMM
#
# ON — wheels down / landed  (Weight on Wheels)
#   label H1  ON ON        (Boeing WOW status block — both main gear WOW on)
#   label H1  /ON          (explicit OOOI ON field)
#   label 31  /ON HHMM
#
# IN — at gate / chocks in
#   label 31  /IN HHMM
#   label H1  /IN          (less common, structurally valid)
#
_PHASE_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "out": [
        ("31", "%/OUT %"),
        ("H1", "%/OUT %"),
    ],
    "off": [
        ("H1", "%OFF OFF%"),
        ("H1", "%/OFF %"),
        ("31", "%/OFF %"),
    ],
    "on": [
        ("H1", "%ON ON%"),
        ("H1", "%/ON %"),
        ("31", "%/ON %"),
    ],
    "in": [
        ("31", "%/IN %"),
        ("H1", "%/IN %"),
    ],
}


def _open_db() -> sqlite3.Connection:
    con = sqlite3.connect(
        f"file:{ACARSHUB_DB_PATH}?mode=ro", uri=True, timeout=3
    )
    con.row_factory = sqlite3.Row
    return con


def check_oooi_event(
    identifier: str,
    phase: str,
    not_before_epoch: float = 0.0,
) -> dict | None:
    """
    Return the most recent ACARS message confirming the given OOOI phase
    ('out', 'off', 'on', 'in') for identifier, or None.

    not_before_epoch — only return events at or after this Unix timestamp.
    Hard cap: never looks further back than 2 hours.
    """
    patterns = _PHASE_PATTERNS.get(phase)
    if not patterns:
        return None
    norm = identifier.upper().replace("-", "").strip()
    cutoff = max(int(not_before_epoch), int(time.time()) - 7200)

    clauses = " OR ".join("(label=? AND upper(msg_text) LIKE ?)" for _ in patterns)
    params: list = [norm, norm, cutoff]
    for label, like in patterns:
        params += [label, like]

    try:
        con = _open_db()
        row = con.execute(
            f"""
            SELECT tail, flight, label, msg_text, msg_time
            FROM messages
            WHERE (
                UPPER(REPLACE(tail,   '-',''))=?
                OR UPPER(REPLACE(flight,'-',''))=?
            )
            AND msg_time >= ?
            AND ({clauses})
            ORDER BY msg_time DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        con.close()
        return dict(row) if row else None
    except Exception as exc:
        log.debug("acars oooi query (%s/%s) failed: %s", identifier, phase, exc)
        return None


def get_latest_phase(
    identifier: str,
    not_before_epoch: float = 0.0,
) -> tuple[str, dict] | None:
    """
    Return (phase, message_dict) for the most recent ACARS OOOI event across
    all four phases, or None if no data is available.

    Queries all phases in a single UNION and returns the one most recent result.
    Use this when ACARS should authoritatively set the current flight state.
    """
    norm = identifier.upper().replace("-", "").strip()
    cutoff = max(int(not_before_epoch), int(time.time()) - 7200)

    unions: list[str] = []
    params: list = []
    for phase, patterns in _PHASE_PATTERNS.items():
        for label, like in patterns:
            # Phase name is from our own constant — safe to embed directly.
            unions.append(
                f"SELECT '{phase}' AS acars_phase, tail, flight, label, msg_text, msg_time "
                f"FROM messages "
                f"WHERE (UPPER(REPLACE(tail,'-',''))=? OR UPPER(REPLACE(flight,'-',''))=?) "
                f"AND msg_time>=? AND label=? AND upper(msg_text) LIKE ?"
            )
            params += [norm, norm, cutoff, label, like]

    sql = " UNION ALL ".join(unions) + " ORDER BY msg_time DESC LIMIT 1"
    try:
        con = _open_db()
        row = con.execute(sql, params).fetchone()
        con.close()
        if row:
            d = dict(row)
            phase_out = d.pop("acars_phase")
            return (phase_out, d)
    except Exception as exc:
        log.debug("acars get_latest_phase failed for %s: %s", identifier, exc)
    return None


# Backward-compat alias.
def check_wow_event(
    identifier: str, not_before_epoch: float = 0.0
) -> dict | None:
    return check_oooi_event(identifier, "on", not_before_epoch)
