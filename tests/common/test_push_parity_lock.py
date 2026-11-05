"""
2026-11-04 push parity lock: every push kind is registered, every vertical
states its parity, non-aviation verticals inherit aviation's thresholds unless
they say otherwise, and nothing in src/ posts to ntfy around the gate.
"""
import re
from pathlib import Path

import pytest

from common import push_policy, push_policy_registry as reg

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
WATCH_VERTICALS = ("aviation", "rail", "maritime", "uas")

# f-string / concatenated trigger families and the concrete values they take.
FAMILIES = {
    r'f"oooi_\{current_phase\}"': ["oooi_out", "oooi_off", "oooi_on", "oooi_in"],
    r'f"\{source\}_tail_change"': ["tdes_tail_change", "fdps_tail_change", "tbfm_tail_change", "tfms_tail_change"],
    r'f"\{source\}_tail_change_rejected"': ["tdes_tail_change_rejected", "fdps_tail_change_rejected"],
    r'f"acars_\{msg_type\.lower\(\)\}"': ["acars_out", "acars_off", "acars_on", "acars_in", "acars_free_text"],
    r'"inbound_" \+ a\.status': ["inbound_late_beyond_turn", "inbound_diverted", "inbound_cancelled",
                                 "inbound_on_time", "inbound_at_origin"],
}

# Direct ntfy posters that still bypass the gate -- each is P2 work, listed here
# so a NEW bypass fails this test. TODO-P2: route each through ntfy_push.send(kind=...).
DIRECT_POSTERS_TODO_P2 = {
    "web/main.py",                    # TODO-P2
    "poller/skills/board_sweep.py",   # TODO-P2
    "ingest/local_airspace.py",       # TODO-P2 (_local_ntfy helper)
}
GATED_SENDERS = {"common/ntfy_push.py", "shared/watchlist.py"}


def _src_files():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _literal_triggers():
    out = set()
    rx = re.compile(r'"watchlist_trigger"\s*:\s*"([a-z0-9_]+)"')
    for p in _src_files():
        out.update(rx.findall(p.read_text(errors="ignore")))
    return sorted(out)


def _registered_anywhere(kind):
    return any(reg.resolve(v, kind) for v in WATCH_VERTICALS)


@pytest.mark.parametrize("kind", _literal_triggers())
def test_every_literal_watch_trigger_is_registered(kind):
    assert _registered_anywhere(kind), f"watchlist_trigger {kind!r} has no push_policy rule"


def test_every_trigger_family_in_src_is_known_and_registered():
    text = "\n".join(p.read_text(errors="ignore") for p in _src_files())
    dynamic = re.findall(r'"watchlist_trigger"\s*:\s*(f"[^"]*"|"[a-z_]+"\s*\+\s*[\w.]+)', text)
    for expr in dynamic:
        known = [pat for pat in FAMILIES if re.fullmatch(pat, expr)]
        assert known, f"new dynamic watchlist_trigger family {expr} -- add it to FAMILIES and the registry"
    for values in FAMILIES.values():
        for kind in values:
            assert _registered_anywhere(kind), kind


def test_at_least_the_47_triggers_of_2026_11_04_are_covered():
    kinds = set(_literal_triggers()) | {k for v in FAMILIES.values() for k in v}
    assert len(kinds) >= 47 and all(_registered_anywhere(k) for k in kinds)


def test_every_vertical_declares_its_parity():
    assert reg.VERTICALS["aviation"]["parity"] == "benchmark"
    for name, row in reg.VERTICALS.items():
        assert row.get("parity") in reg.PARITY_VALUES, name
        if row["parity"] == "defined-break":
            assert (row.get("reason") or "").strip(), f"{name}: a defined break needs a reason"
        if row["parity"] == "target-parity":
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row.get("target_date") or ""), name
        for t in (row.get("thresholds") or {}):
            assert t in reg.THRESHOLDS, f"{name}: unknown threshold {t}"


def test_every_rule_belongs_to_a_declared_vertical_and_a_known_class():
    for (v, kind), rule in reg.KINDS.items():
        assert v in reg.VERTICALS, f"{v}/{kind}: declare the vertical (and its parity) first"
        assert rule.cls in reg.CLASSES, (v, kind, rule.cls)
        if rule.extractor:
            assert rule.extractor in push_policy._EXTRACTORS, (v, kind)


def test_undeclared_vertical_runs_at_aviation_parity(monkeypatch):
    assert reg.vertical_row("ferry")["parity"] == "at-parity"
    assert reg.threshold("ferry", "eta_move_min") == reg.THRESHOLDS["eta_move_min"]
    assert reg.threshold("rail", "time_move_min") == reg.THRESHOLDS["time_move_min"]


def test_a_non_aviation_change_rule_uses_aviation_thresholds(monkeypatch):
    import common.db as db
    db.init_db_all()
    rule = reg.Rule("change", times=("eta",), threshold="eta_move_min", first=3)
    monkeypatch.setitem(reg.KINDS, ("ferry", "arrival_eta"), rule)
    t0 = 1_900_000_000.0
    d1 = push_policy.decide("ferry", "arrival_eta", "boat1", {"eta": t0}, 3, now=t0 - 3600)
    d2 = push_policy.decide("ferry", "arrival_eta", "boat1", {"eta": t0 + 11 * 60}, 3, now=t0 - 3500)
    d3 = push_policy.decide("ferry", "arrival_eta", "boat1", {"eta": t0 + 11 * 60}, 3, now=t0 - 3400)
    assert (d1.priority, d2.priority, d3.priority) == (3, 4, None)


def test_unregistered_kind_is_sent_and_flagged_never_dropped(caplog):
    d = push_policy.decide("aviation", "brand_new_kind", "x", {}, 3)
    assert d.fire and d.priority == 3 and not d.registered
    assert "UNREGISTERED" in caplog.text


def test_approvals_are_never_suppressed_and_at_least_priority_4():
    for _ in range(3):
        d = push_policy.decide("approvals", "connector_link", "req1", {"id": "req1"}, 3)
        assert d.fire and d.priority >= 4


def test_hot_priority_5_passes_through():
    d = push_policy.decide("aviation", "oooi_off", "vip", {"phase": "off"}, 5)
    assert d.priority == 5


def test_no_module_posts_to_ntfy_around_the_gate():
    # a POST (or urlopen) whose own statement names an ntfy address; reading ntfy
    # (the runner's SSE subscription, feed-watch's health GET) is not a push
    rx = re.compile(r"(\.post\(|urlopen\()[^\n]*(NTFY_URL|ntfy_url\(\)|NTFY_BASE|:2586)")
    offenders = set()
    for p in _src_files():
        rel = p.relative_to(SRC).as_posix()
        if rel not in GATED_SENDERS and rx.search(p.read_text(errors="ignore")):
            offenders.add(rel)
    assert offenders <= DIRECT_POSTERS_TODO_P2, f"new direct ntfy poster(s): {offenders - DIRECT_POSTERS_TODO_P2}"
    stale = DIRECT_POSTERS_TODO_P2 - offenders
    assert not stale, f"no longer a direct poster -- drop from the TODO-P2 list: {stale}"


def test_ntfy_push_send_goes_through_the_gate(monkeypatch):
    import common.ntfy_push as np
    import common.db as db
    db.init_db_all()
    posted = []

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass

    monkeypatch.setattr(np.requests, "post", lambda *a, **k: posted.append(k.get("headers", {})) or _Resp(),
                        raising=False)
    np.send("approval-gate", "link request", title="t", priority=3, kind="connector_link", entity="r1")
    assert posted and posted[-1].get("X-Priority") == "4"
