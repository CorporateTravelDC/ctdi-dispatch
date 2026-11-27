"""common.gateway_hold (2026-11-27): reminders a week before a hold or grant lapses; the Cowork daily check-in
tripwire; ChatGPT kept linked by its hold without a quiet alarm; each reminder sent once per target."""
from __future__ import annotations

from pathlib import Path

import pytest

from common import gateway_hold as gh
from common import platform_sentinels as ps
from common import push_policy_registry as reg

NOW = 1_795_000_000.0
DAY = 86400.0
REPO = Path(__file__).resolve().parents[2]


class FakeGW:
    def __init__(self, rows):
        self.rows = rows

    def status(self, slug=None):
        return [dict(r) for r in self.rows if r["slug"] == slug]

    def refresh_grant_until(self, cid):
        return next(r["_grant"] for r in self.rows if r["id"] == cid)


def row(slug, status="active", hold=None, grant=NOW + 25 * DAY, last=NOW - 3600, acct=None):
    return {"id": f"cx_{slug}", "slug": slug, "account": acct or f"ctdc-agent-x-{slug}", "status": status,
            "hold_until": hold, "_grant": grant, "last_renewal_at": last, "last_call_at": last, "revoke_reason": None}


@pytest.fixture
def env(tmp_path, monkeypatch):
    pushes, board = [], []
    monkeypatch.setattr(gh, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(ps, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(ps, "push", lambda kind, msg, **kw: pushes.append({"kind": kind, "msg": msg, **kw}) or True)
    from common import db
    monkeypatch.setattr(db, "board_insert", lambda **kw: board.append(kw))
    return {"pushes": pushes, "board": board}


def kinds(rows, now=NOW):
    return {(r["slug"], r["kind"]) for r in gh.due(gh.connections(FakeGW(rows)), now)}


def test_a_hold_ending_within_a_week_is_reminded_with_the_renew_command():
    k = gh.due(gh.connections(FakeGW([row("chatgpt", "held", hold=NOW + 6 * DAY, last=NOW - 40 * DAY), row("cowork", hold=NOW + 80 * DAY)])), NOW)
    ending = [r for r in k if r["kind"] == "gateway_hold_ending"]
    assert [r["slug"] for r in ending] == ["chatgpt"]
    assert "scripts/agent-gateway.sh hold chatgpt --days 90" in ending[0]["message"]


def test_no_hold_and_a_grant_ending_soon_is_a_relink_warning():
    assert ("cowork", "gateway_relink_due") in kinds([row("cowork", grant=NOW + 5 * DAY), row("chatgpt", hold=NOW + 60 * DAY)])
    assert ("cowork", "gateway_hold_missing") in kinds([row("cowork", grant=NOW + 25 * DAY), row("chatgpt", hold=NOW + 60 * DAY)])


def test_the_cowork_tripwire_fires_after_30h_and_chatgpt_has_none():
    quiet = [row("cowork", hold=NOW + 60 * DAY, last=NOW - 31 * 3600), row("chatgpt", hold=NOW + 60 * DAY, last=NOW - 20 * DAY)]
    k = kinds(quiet)
    assert ("cowork", "gateway_link_quiet") in k and ("chatgpt", "gateway_link_quiet") not in k
    assert ("cowork", "gateway_link_quiet") not in kinds([row("cowork", hold=NOW + 60 * DAY, last=NOW - 20 * 3600),
                                                         row("chatgpt", hold=NOW + 60 * DAY)])


def test_revoked_or_never_linked_is_reported_as_needing_a_relink():
    k = kinds([row("cowork", "revoked")])
    assert ("cowork", "gateway_hold_missing") in k and ("chatgpt", "gateway_hold_missing") in k


def test_each_reminder_is_sent_once_per_target_with_a_board_post(env):
    rows = [row("chatgpt", "held", hold=NOW + 6 * DAY, last=NOW - 40 * DAY), row("cowork", hold=NOW + 60 * DAY)]
    assert [s["kind"] for s in gh.run(now=NOW, gw=FakeGW(rows))] == ["gateway_hold_ending"]
    assert gh.run(now=NOW + 6 * 3600, gw=FakeGW(rows)) == []                       # same target: no repeat
    assert len(env["pushes"]) == 1 and env["board"][0]["thread"] == "coord"
    rows[0]["hold_until"] = NOW + 96 * DAY                                          # renewed: nothing due
    assert gh.run(now=NOW + DAY, gw=FakeGW(rows)) == []
    rows[0]["hold_until"] = NOW + 95 * DAY - 0.5 * DAY                              # a new end date, a week out later
    later = gh.run(now=NOW + 89 * DAY, gw=FakeGW(rows))
    assert [s["kind"] for s in later if s["slug"] == "chatgpt"] == ["gateway_hold_ending"]


def test_kinds_registered_units_and_lapse_exclusion():
    for k in ("gateway_hold_ending", "gateway_relink_due", "gateway_hold_missing", "gateway_link_quiet"):
        assert reg.resolve("platform_ops", k) is not None, k
    u = REPO / ".config/systemd/user"
    assert "gateway-hold run" in (u / "corporatetraveldc-gateway-hold-sentinel.service").read_text()
    t = (u / "corporatetraveldc-gateway-hold-sentinel.timer").read_text()
    assert "OnCalendar=" in t and "Requires=" not in t.split("[Timer]")[0].replace("# no Requires=/Wants=", "")
    assert "ctdc-agent-openai-chatgpt" in (u / "corporatetraveldc-agent-lapse-sentinel.service").read_text()
