"""Push parity lock: the one policy gate every ntfy push goes through
(2026-11-04, operator: "everything that gets an ntfy has a parity check ...
and it is actually run ... at the same parity as aviation").

    from common import push_policy
    d = push_policy.decide("aviation", "tbfm_sequenced", entry_id, detail, 3,
                           context={"phase": entry.get("oooi_phase")})
    if d.fire: send(priority=d.priority)   # else: history only, d.as_dict() recorded

The registry (classes, thresholds, verticals, parity) is
common.push_policy_registry. Change detection is semantic, not textual: a
slot's last FIRED values live in push_policy_state (Postgres, shared by every
container -- pg_schema/0087), so a TBFM status flipping between two wordings,
an unchanged TMI re-send, or the same OFF seen by three sources is decided
once, wherever it arrives. State is written only when a push fires, so slow
drift accumulates against the last value the operator actually saw.

Fail-open, never silent: an unregistered kind is sent at the caller's
priority and logged (WARNING) as unregistered; a state-store error is sent at
the caller's priority and logged. Nothing is ever dropped because the gate
could not decide.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass

from common import push_policy_registry as reg
from common.push_policy_registry import POLICY_VERSION, Rule

log = logging.getLogger(__name__)

_AIRBORNE = frozenset({"off", "on", "in", "airborne", "enroute", "en route", "landed"})
# Continuous telemetry never forms an identity (same idea as watchlist's noise keys).
_NOISE_KEYS = frozenset({
    "lat", "lon", "latitude", "longitude", "alt", "alt_ft", "altitude", "altitude_ft", "gs",
    "gs_kt", "sog", "speed", "track", "dist", "dist_nm", "distance_nm", "minutes_out",
    "tracking_url", "acars_context", "raw", "raw_json", "message_time", "eta_label", "summary",
    "seen", "seen_pos", "rssi", "updated_at", "fetched_at", "push_policy", "watchlist_trigger",
})
_MINUTE_FIELDS = frozenset({"delay_min"})   # declared "times" that are already minutes, not instants
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


@dataclass(frozen=True)
class Decision:
    priority: int | None
    cls: str
    rule: str
    policy_version: str
    vertical: str
    kind: str
    registered: bool = True

    @property
    def fire(self) -> bool:
        return self.priority is not None

    def as_dict(self) -> dict:
        return asdict(self)


# ── time helpers ────────────────────────────────────────────────────────────

def _epoch(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    from common.flight_instance import to_epoch
    return to_epoch(v)


def _bucket(ep: float, minutes: float) -> int:
    return int(ep // (minutes * 60))


def _norm_value(v):
    if isinstance(v, str) and _TS_RE.match(v):
        ep = _epoch(v)
        if ep is not None:
            return _bucket(ep, 10.0)
    return v


# ── value extractors (named in Rule.extractor) ──────────────────────────────

def _tbfm_runway_eta(detail: dict) -> float | None:
    """The usable runway ETA of one TBFM watch hit, or None. A runway-kind row's
    own ETA; a meter-fix row only when the parser estimated a runway time from
    it (runway_eta_epoch). Descent/speed-fix times and wording never count."""
    if detail.get("runway_eta_epoch"):
        return _epoch(detail["runway_eta_epoch"])
    if (detail.get("eta_kind") or "") == "rwy":
        return _epoch(detail.get("eta"))
    return None


def _mit_value(detail: dict) -> float | None:
    try:
        return float(detail.get("mit_value"))
    except (TypeError, ValueError):
        return None


_EXTRACTORS = {"tbfm_runway_eta": _tbfm_runway_eta, "mit_value": _mit_value}


# ── state store ─────────────────────────────────────────────────────────────

_ENSURED = threading.local()


def _ensure(c) -> None:
    from common import db_backend
    if db_backend.backend() == "postgres":
        return                                           # pg_schema/0087_push_policy_state.sql
    c.execute("""CREATE TABLE IF NOT EXISTS push_policy_state (vertical TEXT NOT NULL, kind TEXT NOT NULL,
        entity TEXT NOT NULL, sub_id TEXT NOT NULL, semantic_hash TEXT NOT NULL, state_values TEXT,
        priority INTEGER, decided_at REAL NOT NULL, PRIMARY KEY (vertical, kind, entity, sub_id))""")


def _load(vertical, kind, entity, sub_id) -> dict | None:
    from common import db
    with db.conn() as c:
        _ensure(c)
        row = c.execute("SELECT semantic_hash, state_values, priority, decided_at FROM push_policy_state "
                        "WHERE vertical=? AND kind=? AND entity=? AND sub_id=?",
                        (vertical, kind, entity, sub_id)).fetchone()
    if not row:
        return None
    r = dict(row)
    try:
        r["values"] = json.loads(r.get("state_values") or "{}")
    except ValueError:
        r["values"] = {}
    return r


def _store(vertical, kind, entity, sub_id, h, values, priority, now) -> None:
    from common import db
    with db.conn() as c:
        _ensure(c)
        c.execute("""INSERT INTO push_policy_state
                       (vertical, kind, entity, sub_id, semantic_hash, state_values, priority, decided_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT (vertical, kind, entity, sub_id) DO UPDATE SET
                       semantic_hash=excluded.semantic_hash, state_values=excluded.state_values,
                       priority=excluded.priority, decided_at=excluded.decided_at""",
                  (vertical, kind, entity, sub_id, h, json.dumps(values, default=str, sort_keys=True),
                   priority, now))


# ── the gate ────────────────────────────────────────────────────────────────

def _identity(rule: Rule, detail: dict) -> dict:
    if rule.generic_identity:
        return {k: _norm_value(v) for k, v in sorted(detail.items())
                if k not in _NOISE_KEYS and v not in (None, "", [], {})}
    return {k: detail.get(k) for k in rule.identity if detail.get(k) not in (None, "")}


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _mk(priority, rule_cls, why, vertical, kind, registered=True) -> Decision:
    return Decision(priority, rule_cls, why, POLICY_VERSION, vertical, kind, registered)


_UNREG_WARNED: set = set()


def decide(vertical: str, kind: str, entity: str, detail: dict | None,
           requested_priority: int = 3, *, context: dict | None = None,
           now: float | None = None) -> Decision:
    """Priority 4, priority 3, or None (suppressed: history only) for one push."""
    detail = detail or {}
    now = time.time() if now is None else float(now)
    vertical = vertical or "unknown"
    rule = reg.resolve(vertical, kind)
    if rule is None:
        key = (vertical, kind)
        if key not in _UNREG_WARNED:
            _UNREG_WARNED.add(key)
            log.warning("push_policy: UNREGISTERED push kind %s/%s -- sent at requested priority %s "
                        "(register it in common/push_policy_registry.py)", vertical, kind,
                        requested_priority)
        return _mk(requested_priority, "unregistered", "unregistered_passthrough", vertical, kind, False)
    if requested_priority is not None and requested_priority >= 5:
        return _mk(requested_priority, rule.cls, "hot_passthrough", vertical, kind)
    if rule.cls == "approval":
        return _mk(max(int(requested_priority or 4), 4), "approval", "approval_never_suppressed",
                   vertical, kind)
    try:
        return _decide_stateful(rule, vertical, kind, str(entity or ""), detail, context or {}, now)
    except Exception as e:                                    # state store down: never drop
        log.warning("push_policy: state unavailable for %s/%s (%s) -- sent at requested priority",
                    vertical, kind, e)
        return _mk(requested_priority, rule.cls, "state_unavailable_passthrough", vertical, kind)


def _decide_stateful(rule: Rule, vertical, kind, entity, detail, context, now) -> Decision:
    sub_id = next((str(detail[k]) for k in rule.sub if detail.get(k) not in (None, "")), "")
    ident = _identity(rule, detail)
    first_p = rule.first if rule.first is not None else (4 if rule.cls in ("milestone", "ops") else 3)
    if rule.downgrade_if and any(detail.get(k) for k in rule.downgrade_if):
        first_p = min(first_p, 3)
    prior = _load(vertical, kind, entity, sub_id)

    if rule.cls in ("milestone", "status"):
        h = _hash(ident)
        if prior and prior["semantic_hash"] == h:
            return _mk(None, rule.cls, "unchanged", vertical, kind)
        _store(vertical, kind, entity, sub_id, h, {"identity": ident}, first_p, now)
        return _mk(first_p, rule.cls, "first" if not prior else "identity_changed", vertical, kind)

    if rule.cls == "ops":
        h = _hash(ident)
        if prior and prior["semantic_hash"] == h:
            period = reg.threshold(vertical, "heartbeat_min") * 60
            if rule.heartbeat and now - float(prior["decided_at"]) >= period:
                _store(vertical, kind, entity, sub_id, h, {"identity": ident}, 3, now)
                return _mk(3, "ops", "still_active_heartbeat", vertical, kind)
            return _mk(None, "ops", "unchanged", vertical, kind)
        _store(vertical, kind, entity, sub_id, h, {"identity": ident}, 4, now)
        return _mk(4, "ops", "state_changed" if prior else "first", vertical, kind)

    # ── change ──
    if rule.extractor:
        tvals = {"value": _EXTRACTORS[rule.extractor](detail)}
    else:
        tvals = {k: (_num(detail.get(k)) if k in _MINUTE_FIELDS else _epoch(detail.get(k)))
                 for k in rule.times}
    usable = {k: v for k, v in tvals.items() if v is not None}
    if rule.require_time and not usable:
        return _mk(None, "change", "no_usable_value", vertical, kind)
    if rule.past_while_ground and usable:
        grace = reg.threshold(vertical, "past_grace_min") * 60
        phase = (context.get("phase") or "").lower()
        if phase not in _AIRBORNE and all(v < now - grace for v in usable.values()):
            return _mk(None, "change", "past_while_on_ground", vertical, kind)
    is_minutes = rule.extractor == "mit_value" or any(k in _MINUTE_FIELDS for k in rule.times)
    values = {"identity": ident, "times": usable}
    h = _hash({"identity": ident, "times": {k: _bucket(v * (60 if is_minutes else 1),
                                                       reg.threshold(vertical, "time_bucket_min"))
                                            for k, v in usable.items()}})
    if not prior:
        _store(vertical, kind, entity, sub_id, h, values, first_p, now)
        return _mk(first_p, "change", "first", vertical, kind)
    pv = prior.get("values") or {}
    if pv.get("identity", {}) != ident:
        _store(vertical, kind, entity, sub_id, h, values, rule.on_threshold, now)
        return _mk(rule.on_threshold, "change", "identity_changed", vertical, kind)
    old = pv.get("times") or {}
    thr = reg.threshold(vertical, rule.threshold)
    pairs = [(float(v), float(old[k])) for k, v in usable.items() if old.get(k) is not None]
    if not pairs:
        _store(vertical, kind, entity, sub_id, h, values, rule.on_threshold, now)
        return _mk(rule.on_threshold, "change", "first_usable_value", vertical, kind)
    if rule.extractor == "mit_value":
        biggest = max(n - o for n, o in pairs)                 # tighter = more miles
    elif is_minutes:
        biggest = max(abs(n - o) for n, o in pairs)
    else:
        biggest = max(abs(n - o) / 60.0 for n, o in pairs)     # epoch seconds -> minutes
    if biggest >= thr:
        _store(vertical, kind, entity, sub_id, h, values, rule.on_threshold, now)
        return _mk(rule.on_threshold, "change", f"moved_{biggest:.0f}_ge_{thr:.0f}", vertical, kind)
    if prior["semantic_hash"] != h and rule.below_threshold is not None and rule.extractor != "mit_value":
        _store(vertical, kind, entity, sub_id, h, values, rule.below_threshold, now)
        return _mk(rule.below_threshold, "change", f"moved_{biggest:.0f}_below_{thr:.0f}", vertical, kind)
    return _mk(None, "change", "unchanged_within_bucket", vertical, kind)


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def vertical_for_entry_type(entry_type: str | None) -> str:
    return reg.ENTRY_TYPE_VERTICAL.get(entry_type or "", entry_type or "unknown")
