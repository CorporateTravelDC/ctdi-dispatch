"""Push parity lock: the registry (2026-11-04, operator directive).

Every push that reaches ntfy, in every vertical, current and future, is
decided by common.push_policy against this registry: priority 4, priority 3,
or suppressed (kept in history, no push). Aviation is the benchmark; every
other vertical runs at aviation parity unless its row below says otherwise.

  VERTICALS   one row per vertical: parity = "benchmark" (aviation only) |
              "at-parity" | "defined-break" (needs `reason`) |
              "target-parity" (needs `target_date`). A vertical missing from
              this table is treated as at-parity.
  THRESHOLDS  the aviation benchmark values. A vertical may override one by
              name in its own `thresholds`; anything it does not override is
              aviation's.
  KINDS       (vertical, kind) -> Rule. A kind may also be a family prefix
              ending in "*" (oooi_*, acars_*, inbound_*, *_tail_change).

Classes (see push_policy.decide for the exact algorithm):
  milestone   p4 the first time its identity is seen; unchanged -> suppressed
  change      first sighting at `first`; a declared time moving by at least
              its threshold, or an identity field changing -> p4 (or
              `on_threshold`); a smaller move that changes the 5-minute bucket
              -> `below_threshold` (p3); unchanged -> suppressed
  status      `first` (p3) the first time its identity is seen; unchanged ->
              suppressed
  ops         p4 on a state change; unchanged -> suppressed, except a declared
              still-active heartbeat (p3) once per `heartbeat_min`
  approval    always sent, never suppressed, priority at least 4
A requested priority of 5 (VIP/POTUS) always passes through unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field

POLICY_VERSION = "2026-11-04.1"

PARITY_VALUES = ("benchmark", "at-parity", "defined-break", "target-parity")
CLASSES = ("milestone", "change", "status", "ops", "approval")

# Aviation benchmark (operator-approved 2026-11-04).
THRESHOLDS: dict[str, float] = {
    "eta_move_min": 10.0,        # runway ETA move that is news (p4)
    "edct_move_min": 10.0,       # EDCT / release-time move (p4)
    "time_move_min": 10.0,       # any other declared time (airline OOOI, train delay)
    "time_bucket_min": 5.0,      # smaller moves count only when they change this bucket
    "mit_tighten": 5.0,          # miles-in-trail tightened by at least this (p4)
    "past_grace_min": 5.0,       # an ETA this far in the past while not airborne is stale
    "heartbeat_min": 60.0,       # ops still-active reminder period
}

VERTICALS: dict[str, dict] = {
    "aviation": {"parity": "benchmark", "entry_type": "flight",
                 "topics": ("flight-alerts", "dispatch", "bellwether-alerts", "dispatch-ops",
                            "dispatch-debriefs", "flight-debriefs")},
    "rail": {"parity": "at-parity", "entry_type": "train", "topics": ("train-alerts",)},
    "maritime": {"parity": "at-parity", "entry_type": "vessel", "topics": ("vessel-alerts",)},
    "uas": {"parity": "at-parity", "entry_type": "drone", "topics": ("drone-alerts",)},
    "weather": {"parity": "at-parity", "topics": ("weather", "wx-alerts", "itws", "nws")},
    "nas": {"parity": "at-parity", "topics": ("cps", "nas", "tfms", "tbfm")},
    "vip_tfr": {"parity": "at-parity", "topics": ("tfr-alert", "vip-tfr")},
    "platform_ops": {"parity": "at-parity", "topics": ("ops-health", "ops", "integrity")},
    "approvals": {"parity": "defined-break", "topics": ("approval-gate",),
                  "reason": "a human must see every approval or security request: never "
                            "suppressed, never below priority 4"},
    "agents": {"parity": "at-parity", "topics": ("board", "agents", "second-brain")},
}

ENTRY_TYPE_VERTICAL = {v["entry_type"]: k for k, v in VERTICALS.items() if v.get("entry_type")}


@dataclass(frozen=True)
class Rule:
    cls: str
    identity: tuple = ()            # detail keys whose value is the event's identity
    sub: tuple = ()                 # detail keys naming an independent slot (first non-empty wins)
    times: tuple = ()               # detail keys holding times compared by threshold
    threshold: str = "time_move_min"
    first: int | None = None        # priority on first sighting (class default when None)
    on_threshold: int = 4
    below_threshold: int | None = 3
    extractor: str | None = None    # named value extractor in push_policy (e.g. "tbfm_runway_eta")
    require_time: bool = False      # no usable time -> never fires (keeps the last value)
    past_while_ground: bool = False # time already in the past while not airborne -> suppressed
    downgrade_if: tuple = ()        # truthy detail key -> priority 3 instead of 4
    heartbeat: bool = False         # ops: still-active reminder allowed
    generic_identity: bool = False  # status: identity = the whole normalized detail
    note: str = ""


_M = lambda **kw: Rule("milestone", **kw)          # noqa: E731
_S = lambda **kw: Rule("status", **{"first": 3, **kw})    # noqa: E731
_SG = lambda **kw: Rule("status", **{"first": 3, "generic_identity": True, **kw})  # noqa: E731

KINDS: dict[tuple[str, str], Rule] = {
    # ── aviation: OOOI milestones ────────────────────────────────────────────
    ("aviation", "oooi_*"): _M(identity=("phase",)),
    ("aviation", "oooi_in_inferred"): _S(note="inferred, never asserted"),
    ("aviation", "oooi_seeded_at_add"): _S(identity=("phase",)),
    ("aviation", "smes_oooi"): _M(identity=("phase", "event")),
    ("aviation", "tdes_departure"): _M(),
    ("aviation", "tfms_departure"): _M(downgrade_if=("estimated",)),
    ("aviation", "tfms_arrival"): _M(downgrade_if=("estimated",)),
    ("aviation", "acars_out"): _M(),
    ("aviation", "acars_off"): _M(),
    ("aviation", "acars_on"): _M(),
    ("aviation", "acars_in"): _M(),
    ("aviation", "acars_diversion"): _M(),
    ("aviation", "acars_tfr_area"): _S(first=4, generic_identity=True),
    ("aviation", "acars_*"): _SG(),
    # ── aviation: cancellations, diversions, tails ───────────────────────────
    ("aviation", "fdps_cancelled"): _M(),
    ("aviation", "tfms_flight_plan_cancellation"): _M(),
    ("aviation", "diversion"): _M(identity=("destination", "new_destination", "alternate")),
    ("aviation", "fdps_destination_change"): _M(identity=("destination", "new_destination")),
    ("aviation", "*_tail_change"): _M(identity=("new_hex",)),
    ("aviation", "fdps_tail_change"): _M(identity=("new_hex",)),
    ("aviation", "*_tail_change_rejected"): _S(identity=("candidate_hex",)),
    ("aviation", "fdps_tail_change_rejected"): _S(identity=("candidate_hex",)),
    ("aviation", "identity_resolved"): _S(identity=("hex_id",)),
    ("aviation", "identity_mismatch"): _SG(),
    ("aviation", "identity_mismatch_reg"): _SG(),
    ("aviation", "atc_callsign_relink"): _SG(),
    # ── aviation: inbound aircraft (where's my inbound) ──────────────────────
    ("aviation", "inbound_late_beyond_turn"): _M(identity=("inbound",)),
    ("aviation", "inbound_diverted"): _M(identity=("inbound",)),
    ("aviation", "inbound_cancelled"): _M(identity=("inbound",)),
    ("aviation", "inbound_*"): _S(identity=("inbound",)),
    ("aviation", "departure_overdue"): _S(first=4, identity=("scheduled_departure",)),
    # ── aviation: ETA, release times, restrictions ───────────────────────────
    ("aviation", "tbfm_sequenced"): Rule("change", extractor="tbfm_runway_eta", threshold="eta_move_min",
                                         first=4, require_time=True, past_while_ground=True,
                                         note="semantic key = runway ETA bucketed, never wording; "
                                              "first usable ETA -> p4"),
    ("aviation", "tfms_edct"): Rule("change", identity=("cancelled",), times=("edct",),
                                    threshold="edct_move_min", first=4),
    ("aviation", "tdls_clearance"): Rule("change", identity=("clearance_type",), times=("edct",),
                                         threshold="edct_move_min", first=3),
    ("aviation", "tfms_tmi"): _S(identity=("fca_id", "fca_name"), sub=("fca_id", "fca_name"),
                                 note="first assignment p3; re-sends suppressed"),
    ("aviation", "tfms_tmi_update"): _SG(sub=("tmi_id", "tmiId", "fca_id")),
    ("aviation", "tfms_reroute"): _SG(),
    ("aviation", "tfms_amendment"): _SG(),
    ("aviation", "tfms_flight_route"): _SG(),
    ("aviation", "tfms_flight_create"): _SG(),
    ("aviation", "tfms_flight_schedule_activate"): _SG(),
    ("aviation", "tfms_oceanic_report"): _SG(),
    ("aviation", "tfms_ooooi"): Rule("change", times=("airlineOutTime", "airlineOffTime",
                                                      "airlineOnTime", "airlineInTime"),
                                     threshold="time_move_min", first=3, on_threshold=3,
                                     below_threshold=None,
                                     note="airline estimates: informative, never p4"),
    ("aviation", "fdps_status"): _S(identity=("status",)),
    ("aviation", "fdps_fh"): _SG(),
    ("aviation", "fdps_cl"): _SG(),
    ("aviation", "gate_assignment"): _SG(sub=("slot",)),
    # ── aviation: approach / track notes ─────────────────────────────────────
    ("aviation", "tfms_track_approach"): _S(),
    ("aviation", "fdps_th_approach"): _S(),
    ("aviation", "fdps_th_meterfix_approach"): _S(identity=("meter_fix",)),
    ("aviation", "watchlist_proximity"): _S(),
    # ── rail ─────────────────────────────────────────────────────────────────
    ("rail", "train_sweep"): Rule("change", identity=("state",), times=("delay_min",),
                                  threshold="time_move_min", first=4,
                                  note="delay_min is minutes, compared like a time"),
    # ── maritime ─────────────────────────────────────────────────────────────
    ("maritime", "vessel_position"): _S(note="position only, like ADS-B: never a milestone"),
    # ── NAS programs (wired in P2: ntfy_push callers pass kind=) ─────────────
    ("nas", "program_new"): _M(identity=("program_id",), note="new GS/GDP/AFP on a watched "
                                                              "origin/destination/route Center"),
    ("nas", "program_mit"): Rule("change", identity=("program_id",), times=(),
                                 extractor="mit_value", threshold="mit_tighten", first=3),
    ("nas", "program_status"): _SG(sub=("program_id",)),
    # ── approvals / security (defined break) ─────────────────────────────────
    ("approvals", "*"): Rule("approval"),
    # ── platform ops ─────────────────────────────────────────────────────────
    ("platform_ops", "state"): Rule("ops", identity=("state",), heartbeat=True),
}


def vertical_row(vertical: str) -> dict:
    return VERTICALS.get(vertical) or {"parity": "at-parity", "undeclared": True}


def threshold(vertical: str, name: str) -> float:
    own = (vertical_row(vertical).get("thresholds") or {})
    return float(own.get(name, THRESHOLDS[name]))


def resolve(vertical: str, kind: str) -> Rule | None:
    """Exact (vertical, kind), then the longest matching family pattern."""
    rule = KINDS.get((vertical, kind))
    if rule:
        return rule
    best, best_len = None, -1
    for (v, pat), r in KINDS.items():
        if v != vertical or "*" not in pat:
            continue
        if pat == "*":
            ok, n = True, 0
        elif pat.endswith("*"):
            ok, n = kind.startswith(pat[:-1]), len(pat) - 1
        elif pat.startswith("*"):
            ok, n = kind.endswith(pat[1:]), len(pat) - 1
        else:
            ok, n = False, 0
        if ok and n > best_len:
            best, best_len = r, n
    return best


def vertical_for_topic(topic: str | None) -> str | None:
    for v, row in VERTICALS.items():
        if topic and topic in row.get("topics", ()):
            return v
    return None
