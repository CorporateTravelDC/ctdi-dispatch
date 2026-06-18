"""
common.acars — ACARS/VDL2 helper utilities shared across dispatch services.

Consumers:
  pusher  — landing confirmation bypass for _check_flight_landing()
  poller  — OOOI phase altitude-guard override in _check_flight_airplanes_live()
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


def check_wow_event(identifier: str, not_before_epoch: float = 0.0) -> dict | None:
    """
    Check the acarshub messages DB for a recent Weight-on-Wheels (ON/landed) event.

    identifier       — aircraft registration or callsign; normalized internally
                       (upper-cased, hyphens stripped) before querying.
    not_before_epoch — only return events at or after this Unix timestamp.
                       Pass state["last_seen"] (pusher) or the time the aircraft
                       was last seen in the "off" phase (poller) to avoid matching
                       a WOW event from a previous flight leg.

    Patterns matched (confirmed from DC-area ACARS/VDL2 traffic):
      label H1 + 'ON ON'  — Boeing WOW status block (WN/UA/DL/AS/AA/N757AF)
      label H1 + '/ON '   — explicit OOOI ON field
      label 31 + '/ON '   — JetBlue/B6 OOOI format

    Returns the most recent matching message as a dict, or None if absent or
    if the acarshub DB is unavailable.  Hard cap: never looks further back than
    2 hours regardless of not_before_epoch.
    """
    norm = identifier.upper().replace("-", "").strip()
    cutoff = max(int(not_before_epoch), int(time.time()) - 7200)
    try:
        con = sqlite3.connect(
            f"file:{ACARSHUB_DB_PATH}?mode=ro", uri=True, timeout=3
        )
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT tail, flight, label, msg_text, msg_time
            FROM messages
            WHERE (
                UPPER(REPLACE(tail,   '-', '')) = ?
                OR UPPER(REPLACE(flight, '-', '')) = ?
            )
            AND msg_time >= ?
            AND (
                (label = 'H1' AND upper(msg_text) LIKE '%ON ON%')
                OR (label = 'H1' AND upper(msg_text) LIKE '%/ON %')
                OR (label = '31' AND upper(msg_text) LIKE '%/ON %')
            )
            ORDER BY msg_time DESC
            LIMIT 1
            """,
            (norm, norm, cutoff),
        ).fetchone()
        con.close()
        return dict(row) if row else None
    except Exception as exc:
        log.debug("acars wow query failed for %s: %s", identifier, exc)
        return None
