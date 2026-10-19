"""common/input_gate.py -- skip a model narrative when the brief's input DATA is unchanged.

2026-10-19 (lean-out L2, F7). ep-advance (~25 min/h) and ops-brief (15-25 min/h)
held the long-runner model lane every hour with no unchanged-input check;
overnight most of their inputs do not move. Each skill now builds a key from
the stable DATA fields of its prompt sections and asks this gate whether the
last successful model narrative was made from the same key.

Operator rule (dedup never hashes timestamps, 2026-10-14): the key is built
ONLY from the data's own stable fields. Callers leave out fetch/pull stamps,
ages, counts and narrative text (CPS / route narratives); mask_stamps() strips
the volatile stamps that are embedded inside otherwise-stable lines. A change
in any data field changes the key and the model runs as before.

On a match the caller re-sends the stored narrative with a deterministic
"no change since HH:MMZ (HH:MM ET)" line. The reuse is capped at
INPUT_GATE_MAX_REUSE_S (default 6 h): after that the model runs again even if
nothing changed, so a narrative is never older than that. `--force` bypasses.
State: one small JSON file per skill in the state dir (atomic replace).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Volatile stamps embedded in data lines. Event fields (program end times,
# TFR ids, delay minutes) are NOT masked: a change there is a real change.
_STAMP_PATTERNS = [
    # METAR/SPECI issue group DDHHMMZ (the observation stamp, not the weather)
    (re.compile(r"\b\d{6}Z\b"), "<obs>"),
    # "(local-db, 12min old)" / "12 min ago" style ages
    (re.compile(r"\b\d+\s*(?:s|sec|secs|seconds|min|mins|minutes|h|hr|hrs|hours)\s+(?:old|ago)\b", re.I), "<age>"),
    # "as of <stamp>" to end of line (feed update stamps)
    (re.compile(r"\bas of [^\n]*", re.I), "as of <t>"),
]


def mask_stamps(text: str) -> str:
    out = text or ""
    for pat, rep in _STAMP_PATTERNS:
        out = pat.sub(rep, out)
    return out


def make_key(pieces: dict) -> str:
    """sha256 over the caller's stable data pieces (sorted, JSON)."""
    blob = json.dumps(pieces, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def label_utc_et(epoch: float) -> str:
    """'HH:MMZ (HH:MM ET)' -- time labels are UTC + ET, never Eastern alone."""
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return f"{dt:%H:%M}Z ({dt.astimezone(_ET):%H:%M} ET)"


class InputGate:
    def __init__(self, name: str, state_dir: str | None = None,
                 max_reuse_s: int | None = None, now=time.time):
        if state_dir is None:
            from common import config
            state_dir = config.state_dir()
        self.path = pathlib.Path(state_dir) / f"input-gate-{name}.json"
        self.max_reuse_s = int(max_reuse_s if max_reuse_s is not None
                               else os.getenv("INPUT_GATE_MAX_REUSE_S", "21600"))
        self._now = now

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {}

    def reusable(self, key: str | None) -> dict | None:
        """The stored narrative when `key` matches the last model run and it is
        younger than max_reuse_s; else None (the caller runs the model)."""
        if not key:
            return None
        st = self._load()
        if st.get("key") != key or not st.get("narrative"):
            return None
        age = self._now() - float(st.get("model_at") or 0)
        if age < 0 or age >= self.max_reuse_s:
            return None
        return st

    def record(self, key: str | None, narrative: str, concise: str) -> None:
        """Remember a SUCCESSFUL model narrative (never a deterministic fallback)."""
        if not key or not narrative:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": key, "narrative": narrative,
                                       "concise": concise, "model_at": self._now()}))
            os.replace(tmp, self.path)
        except Exception as exc:  # the gate is an optimisation; never fail the brief
            log.warning("input-gate: could not record %s: %s", self.path.name, exc)

    @staticmethod
    def no_change_note(stored: dict) -> str:
        return (f"[no change in input data since {label_utc_et(float(stored['model_at']))}: "
                f"model narrative from that run, not regenerated]")
