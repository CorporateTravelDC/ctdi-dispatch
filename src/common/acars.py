"""
common.acars — ACARS/VDL2/HFDL authoritative flight state.

ACARS/VDL2/HFDL messages originate directly from aircraft avionics — they
are the most reliable source of flight phase truth available.  ADS-B is
secondary.

Source priority (2026-07-23 -- operator directive: default to the
aggregators, which are already credentialed, rather than local-SDR-only):
  1. ACARS Drama Jumpseat (api.jumpseat.acarsdrama.com) -- queried live,
     by registration. Near-global coverage, not limited to this station's
     own VHF/SDR range.
  2. airframes.io REST -- queried live, global stream filtered client-side
     by flight/tail. Secondary aggregator, used when Jumpseat has nothing
     (no token, rate-limited, or no hit for this registration).
  3. Local acarshub (this station's own acarsdec/dumpvdl2 decode,
     messages.db) -- fallback when neither aggregator has anything. This
     was previously the ONLY source get_latest_phase() checked; kept as
     the last resort, not removed, since it's zero-latency and has no
     third-party dependency once a flight is actually in range.

This is deliberately separate from the "in local range" ADS-B-proximity
notification in ingest/local_airspace.py and poller.main's local_aircraft
sweep -- those stay exactly as they are, driven by UltraFeeder position
data, not ACARS. Nothing here changes that path.

Consumers:
  pusher  — all four OOOI phases drive _flight_state directly
  poller  — ACARS phase overrides ADS-B-derived OOOI transitions
"""

import logging
import os
import pathlib
import sqlite3
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

ACARSHUB_DB_PATH = os.environ.get(
    "ACARSHUB_DB_PATH",
    "/var/lib/corporatetraveldc/acarshub/messages.db",
)

# ── Aggregator config (mirrors acars_watcher.py's env vars/token resolution
# so both consumers of the same credentials stay in sync) ───────────────────
JUMPSEAT_API_BASE  = os.environ.get("JUMPSEAT_API_BASE",  "https://api.jumpseat.acarsdrama.com/v1")
AIRFRAMES_API_BASE = os.environ.get("AIRFRAMES_API_BASE", "https://api.airframes.io/v1")
_AGGREGATOR_TIMEOUT = 8  # seconds -- this runs inline in the per-entry poller sweep loop


def _resolve_jumpseat_token() -> str | None:
    env = (os.environ.get("ACARSDRAMA_JUMPSEAT_TOKEN", "").strip()
           or os.environ.get("JUMPSEAT_API_KEY", "").strip())
    if env:
        return env
    secret = pathlib.Path.home() / ".secrets" / "jumpseat.key"
    if secret.exists():
        return secret.read_text().strip()
    return None


def _resolve_airframes_token() -> str | None:
    env = os.environ.get("AIRFRAMES_TOKEN", "").strip()
    if env:
        return env
    secret = pathlib.Path.home() / ".secrets" / "airframes.token"
    if secret.exists():
        return secret.read_text().strip()
    return None


_JUMPSEAT_TOKEN = _resolve_jumpseat_token()
_AIRFRAMES_TOKEN = _resolve_airframes_token()


def _extract_reg(msg: dict) -> str:
    for key in ("tail", "registration", "reg", "aircraft_reg"):
        v = msg.get(key, "")
        if v:
            return str(v).strip().upper().replace("-", "")
    acars = msg.get("acars") or {}
    if isinstance(acars, dict):
        v = acars.get("reg", "") or acars.get("tail", "")
        if v:
            return str(v).strip().upper().replace("-", "")
    return ""


def _extract_flight(msg: dict) -> str:
    v = (msg.get("flight") or msg.get("flightNumber")
         or (msg.get("acars") or {}).get("flight") or "")
    return str(v).strip().upper().replace(" ", "").replace("-", "")


def _extract_label(msg: dict) -> str:
    return str(msg.get("label") or (msg.get("acars") or {}).get("label")
               or msg.get("type") or "").strip()


def _extract_text(msg: dict) -> str:
    return str(msg.get("cleanedText") or msg.get("text")
               or (msg.get("acars") or {}).get("msg_text")
               or msg.get("message") or "").strip()


def _extract_epoch(msg: dict) -> float:
    """Best-effort timestamp -> epoch seconds. Aggregator timestamps are
    ISO 8601; fall back to "now" (sorts last, never crashes the sweep)."""
    raw = msg.get("timestamp") or msg.get("msg_time") or msg.get("time")
    if raw is None:
        return time.time()
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        s = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return time.time()


def _phase_hit(label: str, text: str, patterns: list[tuple[str, str]]) -> bool:
    """Reimplements the SQL `label=? AND upper(msg_text) LIKE ?` check for
    an in-memory aggregator message. Every pattern in _PHASE_PATTERNS is a
    plain '%substring%' wildcard (no other SQL wildcards used), so this is
    just a label-equality + substring-containment check."""
    label_u = (label or "").upper().strip()
    text_u = (text or "").upper()
    for want_label, like in patterns:
        if label_u != want_label:
            continue
        needle = like.strip("%")
        if needle in text_u:
            return True
    return False


def _best_phase_from_messages(messages: list[dict], cutoff_epoch: float) -> tuple[str, dict] | None:
    """Given a list of raw aggregator message dicts (already filtered to the
    aircraft/flight of interest), find the single most recent one matching
    any OOOI phase pattern. Mirrors get_latest_phase()'s local-DB UNION+
    ORDER BY msg_time DESC LIMIT 1 semantics, done in Python."""
    best: tuple[float, str, dict] | None = None
    for msg in messages:
        ts = _extract_epoch(msg)
        if ts < cutoff_epoch:
            continue
        label = _extract_label(msg)
        text = _extract_text(msg)
        for phase, patterns in _PHASE_PATTERNS.items():
            if _phase_hit(label, text, patterns):
                if best is None or ts > best[0]:
                    best = (ts, phase, msg)
                break
    if best is None:
        return None
    _, phase, msg = best
    return phase, {
        "tail": _extract_reg(msg),
        "flight": _extract_flight(msg),
        "label": _extract_label(msg),
        "msg_text": _extract_text(msg),
        "msg_time": _extract_epoch(msg),
    }


def _query_jumpseat_phase(registration: str, cutoff_epoch: float) -> tuple[str, dict] | None:
    if not _JUMPSEAT_TOKEN or not registration:
        return None
    try:
        resp = requests.get(
            f"{JUMPSEAT_API_BASE}/messages/search",
            params={"registration": registration, "limit": "20", "source": "messages"},
            headers={"Authorization": f"Bearer {_JUMPSEAT_TOKEN}"},
            timeout=_AGGREGATOR_TIMEOUT,
        )
        if resp.status_code != 200:
            log.debug("jumpseat phase query %s: HTTP %s", registration, resp.status_code)
            return None
        data = resp.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return None
        result = _best_phase_from_messages(items, cutoff_epoch)
        if result:
            phase, msg = result
            msg["_source"] = "JUMPSEAT"
            return phase, msg
    except Exception as exc:
        log.debug("jumpseat phase query %s failed: %s", registration, exc)
    return None


def _query_airframes_phase(identifier: str, registration: str, cutoff_epoch: float) -> tuple[str, dict] | None:
    try:
        headers = {}
        if _AIRFRAMES_TOKEN:
            headers["Authorization"] = f"Bearer {_AIRFRAMES_TOKEN}"
        since = datetime.fromtimestamp(cutoff_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = requests.get(
            f"{AIRFRAMES_API_BASE}/messages",
            params={"since": since, "limit": 500},
            headers=headers,
            timeout=_AGGREGATOR_TIMEOUT,
        )
        if resp.status_code != 200:
            log.debug("airframes.io phase query: HTTP %s", resp.status_code)
            return None
        data = resp.json()
        msgs = data if isinstance(data, list) else data.get("messages", data.get("data", []))
        if not isinstance(msgs, list):
            return None
        ident_norm = (identifier or "").upper().replace(" ", "").replace("-", "")
        reg_norm = (registration or "").upper().replace("-", "")
        matched = [
            m for m in msgs
            if isinstance(m, dict)
            and ((reg_norm and _extract_reg(m) == reg_norm)
                 or (ident_norm and _extract_flight(m) == ident_norm))
        ]
        if not matched:
            return None
        result = _best_phase_from_messages(matched, cutoff_epoch)
        if result:
            phase, msg = result
            msg["_source"] = "AIRFRAMES"
            return phase, msg
    except Exception as exc:
        log.debug("airframes.io phase query %s failed: %s", identifier, exc)
    return None

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
    registration: str | None = None,
) -> tuple[str, dict] | None:
    """
    Return (phase, message_dict) for the most recent ACARS/VDL2/HFDL OOOI
    event across all four phases, or None if no data is available.

    Source priority, 2026-07-23: Jumpseat (by registration) -> airframes.io
    (global, filtered by identifier/registration) -> local acarshub DB
    (this station's own decode). Aggregators default first since they're
    already credentialed and have near-global coverage; local is the
    fallback, not the gate -- see module docstring.

    `registration` is optional but strongly recommended: Jumpseat's search
    endpoint is registration-keyed, so without it that source is skipped
    entirely and airframes.io/local are the only sources tried.

    Use this when ACARS/VDL2/HFDL should authoritatively set the current
    flight state.
    """
    norm = identifier.upper().replace("-", "").strip()
    cutoff = max(int(not_before_epoch), int(time.time()) - 7200)

    reg_norm = (registration or "").upper().replace("-", "").strip() or None

    if reg_norm:
        agg = _query_jumpseat_phase(reg_norm, cutoff)
        if agg:
            return agg

    agg = _query_airframes_phase(norm, reg_norm, cutoff)
    if agg:
        return agg

    # Fall back to local acarshub decode (this station's own SDR range).

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


def get_recent_message_texts(
    identifier: str,
    registration: str | None = None,
    limit: int = 5,
    lookback_minutes: float = 180.0,
) -> list[dict]:
    """Return up to `limit` recent raw ACARS/VDL2/HFDL messages for this
    flight/tail, most recent first -- NOT filtered to OOOI phase patterns
    like get_latest_phase(). Built 2026-08-10 as a "what does ACARS say
    right now" sub-check for diversion and OOOI watchlist alerts (the
    operator's own request: attach whatever reason-relevant ACARS/VDL
    traffic exists to a diversion alert rather than a bare destination-
    changed notice with no context).

    Deliberately returns raw message text for a human to read rather than
    trying to classify a "reason" from keywords -- an unverified keyword
    match (e.g. treating any message containing "FUEL" as a fuel-related
    diversion cause) would repeat exactly the kind of overconfident,
    plausible-but-wrong signal this platform has caught and discarded
    elsewhere tonight (flight_events.status='cancelled', etc.). Same
    three-source priority as get_latest_phase(): Jumpseat (by
    registration) -> airframes.io (global, filtered) -> local acarshub
    decode -- reusing the same aggregator config/helpers so this doesn't
    introduce a fourth, differently-credentialed ACARS read path.

    Returns [] (not None) when nothing is found -- callers should treat
    that as "no ACARS traffic in this window", state it plainly in the
    alert, not as an error."""
    norm = identifier.upper().replace("-", "").strip()
    reg_norm = (registration or "").upper().replace("-", "").strip() or None
    cutoff = time.time() - lookback_minutes * 60

    def _to_result(msgs: list[dict], source: str) -> list[dict]:
        out = []
        for m in msgs:
            ts = _extract_epoch(m)
            if ts < cutoff:
                continue
            text = _extract_text(m)
            if not text:
                continue
            out.append({
                "source": source, "time": ts,
                "label": _extract_label(m), "text": text,
            })
        out.sort(key=lambda x: -x["time"])
        return out[:limit]

    if reg_norm and _JUMPSEAT_TOKEN:
        try:
            resp = requests.get(
                f"{JUMPSEAT_API_BASE}/messages/search",
                params={"registration": reg_norm, "limit": "20", "source": "messages"},
                headers={"Authorization": f"Bearer {_JUMPSEAT_TOKEN}"},
                timeout=_AGGREGATOR_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", data) if isinstance(data, dict) else data
                if isinstance(items, list):
                    result = _to_result(items, "JUMPSEAT")
                    if result:
                        return result
        except Exception as exc:
            log.debug("jumpseat recent-messages query %s failed: %s", reg_norm, exc)

    try:
        headers = {}
        if _AIRFRAMES_TOKEN:
            headers["Authorization"] = f"Bearer {_AIRFRAMES_TOKEN}"
        since = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = requests.get(
            f"{AIRFRAMES_API_BASE}/messages",
            params={"since": since, "limit": 500},
            headers=headers,
            timeout=_AGGREGATOR_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            msgs = data if isinstance(data, list) else data.get("messages", data.get("data", []))
            if isinstance(msgs, list):
                matched = [
                    m for m in msgs
                    if isinstance(m, dict)
                    and ((reg_norm and _extract_reg(m) == reg_norm)
                         or (norm and _extract_flight(m) == norm))
                ]
                result = _to_result(matched, "AIRFRAMES")
                if result:
                    return result
    except Exception as exc:
        log.debug("airframes.io recent-messages query %s failed: %s", identifier, exc)

    try:
        con = _open_db()
        # msg_text aliased to "text" -- _extract_text() (shared with the
        # aggregator paths above) checks msg.get("text"), not a bare
        # msg.get("msg_text"); without this alias every local-DB row would
        # silently extract as empty text. Caught in testing before this
        # ever ran against a real diversion.
        rows = con.execute("""
            SELECT tail, flight, label, msg_text AS text, msg_time FROM messages
            WHERE (UPPER(REPLACE(tail,'-',''))=? OR UPPER(REPLACE(flight,'-',''))=?)
              AND msg_time>=?
            ORDER BY msg_time DESC LIMIT ?
        """, (norm, norm, cutoff, limit)).fetchall()
        con.close()
        return _to_result([dict(r) for r in rows], "LOCAL")
    except Exception as exc:
        log.debug("acars get_recent_message_texts local fallback failed for %s: %s", identifier, exc)
    return []


# Backward-compat alias.
def check_wow_event(
    identifier: str, not_before_epoch: float = 0.0
) -> dict | None:
    return check_oooi_event(identifier, "on", not_before_epoch)
