"""2026-11-18 (operator): the lockdown out-of-band (OOB) management channel -- scripts/ctdc-oob.sh,
scripts/lib/ctdc_oob.py, scripts/oob-pin-build.sh, docs/LOCKDOWN_OOB.md.

Covers: strict config validation; candidate selection (order, `never`, carrier + default route, cut);
the metered budget math including the cycle reset; enter/tick/leave actions for every on_budget mode
and with no metered candidate; every pin source type; every FIDO mode (auto flips only for an ACTIVE
ADMIN hardware key); the pin checker's rejections; the sshd drop-in; the GPG break-glass verifier with
throwaway keys built in pure Python (replay, expiry, wrong key, allowlist); the shell's test seam; and
static wiring (lockdown/restore/boot guard, root install, public scrub, no box-specific names in code).
No SSH or GPG private key is generated with a tool or agent: the SSH keys here are bare public blobs,
and the OpenPGP keys are throwaway Ed25519 keys computed in this file."""
import base64
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIBP = ROOT / "scripts/lib/ctdc_oob.py"
OOB_SH = ROOT / "scripts/ctdc-oob.sh"
PIN_SH = ROOT / "scripts/oob-pin-build.sh"
GPG = shutil.which("gpg")

_spec = importlib.util.spec_from_file_location("ctdc_oob", LIBP)
oob = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oob)

T0 = dt.datetime(2026, 11, 18, 5, 0, tzinfo=dt.timezone.utc).timestamp()


def cfg_raw(**over):
    raw = {
        "version": 1,
        "candidates": [
            {"interface": "sat0"},
            {"connection": "LTE Puck", "metered": {"allowance_gb": 100, "cycle_day": 25, "timezone": "America/New_York",
                                                   "baseline_used_gb": 54.5, "baseline_at": "2026-11-18T04:49:00Z",
                                                   "baseline_vnstat_month_bytes": 61805647109, "budget_fraction": 0.5}},
        ],
        "never": ["wan0"],
        "on_budget": "cap",
        "ssh": {"enabled": True, "allowed_accounts": ["op"], "fido": "auto"},
        "pins": [
            {"role": "primary", "source": "board_signers", "account": "op"},
            {"role": "approver", "source": "approval_signers", "account": "op"},
        ],
    }
    raw.update(over)
    return raw


def cfg(**over):
    return oob.validate(cfg_raw(**over))


def facts(sat=(False, ""), lte=(True, "192.0.2.1"), wan=(True, "198.51.100.1"), lte_bytes=0):
    return {"ifaces": {"sat0": {"carrier": sat[0], "gw4": sat[1], "gw6": "", "bytes": 0},
                       "lte0": {"carrier": lte[0], "gw4": lte[1], "gw6": "", "bytes": lte_bytes},
                       "wan0": {"carrier": wan[0], "gw4": wan[1], "gw6": "", "bytes": 0}},
            "nm_active": {"LTE Puck": "lte0", "Home": "wan0"}, "tailnet": ["100.64.0.1"]}


def vnstat(dev="lte0", month_bytes=61805647109 + 1_000_000, hours=None, month=(2026, 11)):
    hours = hours or []
    return {"interfaces": [{"name": dev, "traffic": {
        "month": [{"date": {"year": month[0], "month": month[1]}, "rx": month_bytes, "tx": 0}],
        "hour": [{"timestamp": t, "rx": b, "tx": 0} for t, b in hours], "fiveminute": [], "day": []}}]}


# -- config ---------------------------------------------------------------------------------------

def test_shipped_configs_validate():
    for name in ("oob.example.json", "oob.json"):
        p = ROOT / "config/network" / name
        if p.exists():
            oob.load_config(str(p))


@pytest.mark.parametrize("mutate, why", [
    (lambda r: r.update(colour="blue"), "unknown key"),
    (lambda r: r["candidates"][0].update(speed=1), "unknown key"),
    (lambda r: r["candidates"][1]["metered"].update(rollover=True), "unknown key"),
    (lambda r: r["pins"][0].update(extra=1), "unknown key"),
    (lambda r: r["ssh"].update(port=22), "unknown key"),
    (lambda r: r.update(never=["sat0"]), "also in config.never"),
    (lambda r: r.update(on_budget="throttle"), "on_budget"),
    (lambda r: r["ssh"].update(fido="maybe"), "fido"),
    (lambda r: r.update(candidates=[]), "non-empty"),
    (lambda r: r["candidates"][0].update(connection="x"), "exactly one"),
    (lambda r: r["candidates"][1]["metered"].update(cycle_day=31), "1-28"),
    (lambda r: r["candidates"][1]["metered"].update(budget_fraction=1.5), "(0, 1]"),
    (lambda r: r["candidates"][1]["metered"].update(timezone="Mars/Olympus"), "time zone"),
    (lambda r: r.update(tailscale={"mask": "0xff0000"}), "0xff0000"),
    (lambda r: r.update(vpn={"mask": "0xff0000"}), "0xff0000"),
    (lambda r: r.update(vpn={}, tailscale={}), "not both"),
    (lambda r: r.update(vpn={"colour": "blue"}), "unknown key"),
    (lambda r: r.update(vpn={"protocol": "icmp"}), "udp or tcp"),
    (lambda r: r.update(vpn={"interface": "a-very-long-tunnel0"}), "interface name"),
    (lambda r: r.update(vpn={"interface": "tun 0"}), "interface name"),
    (lambda r: r.update(vpn={"port": 0}), "1..65535 or null"),
    (lambda r: r.update(vpn={"relay_tcp_port": 70000}), "1..65535 or null"),
    (lambda r: r.update(vpn={"protocol": "tcp", "port": 443, "relay_tcp_port": 443}), "must differ"),
    (lambda r: r.update(pins=[{"role": "x", "source": "ldap", "account": "op"}]), "source"),
    (lambda r: r.update(pins=[{"role": "x", "source": "ssh_pubkey_file", "path": "/k.pub"}]), "needs an account"),
    (lambda r: r.update(breakglass={"enabled": True, "signers": ["primary"]}), "not a gpg_asc pin role"),
    (lambda r: r.update(breakglass={"enabled": True, "commands": ["reboot"], "signers": []}), "not implemented"),
    (lambda r: r.update(version=2), "version"),
])
def test_invalid_config_is_rejected(mutate, why):
    raw = cfg_raw()
    mutate(raw)
    with pytest.raises(oob.OobError, match=why.replace("(", r"\(").replace(")", r"\)").replace("]", r"\]")):
        oob.validate(raw)


def test_allowed_accounts_default_to_the_operator():
    raw = cfg_raw()
    del raw["ssh"]["allowed_accounts"]
    assert oob.validate(raw, default_account="theop")["ssh"]["allowed_accounts"] == ["theop"]


# -- selection --------------------------------------------------------------------------------------

def test_first_candidate_with_carrier_and_default_route_wins():
    c = cfg()
    assert oob.select(c, facts(sat=(True, "203.0.113.1")), {}) == ("c0", "sat0")
    assert oob.select(c, facts(sat=(True, "")), {}) == ("c1", "lte0")          # carrier, no default route
    assert oob.select(c, facts(sat=(False, "203.0.113.1")), {}) == ("c1", "lte0")  # route, no carrier
    assert oob.select(c, facts(lte=(False, "192.0.2.1")), {}) == (None, None)


def test_never_is_respected_by_interface_and_by_connection():
    c = cfg(candidates=[{"interface": "wan1"}, {"connection": "LTE Puck"}], never=["Home", "lte9"])
    f = facts()
    f["ifaces"]["wan1"] = {"carrier": True, "gw4": "198.51.100.9", "gw6": "", "bytes": 0}
    f["nm_active"]["Home"] = "wan1"                       # wan1 carries a never-listed connection
    assert oob.select(c, f, {}) == ("c1", "lte0")
    c2 = cfg(candidates=[{"interface": "lte0"}], never=["LTE Puck"])
    assert oob.select(c2, facts(), {}) == (None, None)


def test_works_with_no_metered_candidate():
    c = cfg(candidates=[{"interface": "sat0"}, {"interface": "lte0"}])
    st, acts = oob.enter(c, facts(), {}, {}, T0, "t")
    assert ["route_on", "lte0"] in acts
    assert ["shape_oob", "lte0", "2048", "41641", "udp", "443"] in acts                # the hard cap applies to ANY candidate
    assert not any(a[0] in ("cap_on", "disconnect") for a in acts)
    assert st["metered"] == {}


# -- budget -------------------------------------------------------------------------------------------

def test_budget_today_is_half_the_remaining_allowance():
    m = cfg()["candidates"][1]["metered"]
    b = oob.budget(m, vnstat(month_bytes=61805647109 + 500_000_000), T0, "lte0")
    assert b["used_bytes"] == 54_500_000_000 + 500_000_000
    assert b["remaining_bytes"] == 45_000_000_000
    assert b["budget_bytes"] == 22_500_000_000
    assert b["cycle_start_utc"] == "2026-10-25T04:00:00Z"           # 00:00 America/New_York (EDT)
    assert b["method"] == "most conservative of 3 sources: carrier"
    car = b["sources"]["carrier"]
    assert car["label"].startswith("carrier figure as of 2026-11-18T04:49:00Z, not live") and car["live"] is False
    assert car["age_s"] == 11 * 60 and car["figure_bytes"] == 54_500_000_000
    assert b["sources"]["live"]["used_bytes"] is None                 # no hour series: live unavailable, not fatal


def test_budget_after_the_cycle_reset_counts_only_since_the_25th():
    m = cfg()["candidates"][1]["metered"]
    start = dt.datetime(2026, 11, 25, 5, 0, tzinfo=dt.timezone.utc).timestamp()   # 00:00 EST
    hours = [(start - 3600, 7 * 10 ** 9)] + [(start + 3600 * i, 10 ** 9) for i in range(3)]
    now = start + 3 * 3600
    b = oob.budget(m, vnstat(hours=hours), now, "lte0")
    assert b["cycle_start_utc"] == "2026-11-25T05:00:00Z"
    assert b["used_bytes"] == 3 * 10 ** 9                         # the hour before the reset is not counted
    assert b["budget_bytes"] == int((100 - 3) * 10 ** 9 * 0.5)
    assert b["method"] == "most conservative of 3 sources: live"
    assert "earlier cycle" in b["sources"]["carrier"]["label"]


def test_cycle_start_crosses_the_year():
    assert oob.cycle_start(dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc).timestamp(), 25, "America/New_York") == \
        dt.datetime(2025, 12, 25, 5, 0, tzinfo=dt.timezone.utc).timestamp()


def test_budget_refuses_vnstat_below_the_baseline_and_missing_history():
    m = cfg()["candidates"][1]["metered"]
    with pytest.raises(oob.OobError, match="below the baseline"):
        oob.budget(m, vnstat(month_bytes=1), T0, "lte0")
    with pytest.raises(oob.OobError, match="does not reach back"):
        oob.budget(m, vnstat(hours=[]), dt.datetime(2026, 11, 26, tzinfo=dt.timezone.utc).timestamp(), "lte0")


# -- enter / tick / leave --------------------------------------------------------------------------------

def _enter(c, used=0):
    return oob.enter(c, facts(lte_bytes=1000), {}, {"lte0": vnstat(month_bytes=61805647109 + used)}, T0, "test")


def test_enter_hard_caps_routes_and_alerts():
    st, acts = _enter(cfg())
    assert st["active"] and st["metered"]["c1"]["budget_bytes"] == int(45.5e9 * 0.5)
    assert ["shape_oob", "lte0", "2048", "41641", "udp", "443"] in acts and ["route_on", "lte0"] in acts and ["sshd_on"] in acts
    assert ["cap_off", "lte0"] not in acts                              # never uncapped while the channel is up
    assert any(a[0] == "alert" and a[1] == "4" and "2048 kbit/s hard cap" in a[3] for a in acts)


@pytest.mark.parametrize("mode, expect", [("soft", ["shape_oob", "lte0", "2048", "41641", "udp", "443"]),
                                          ("cap", ["shape_oob", "lte0", "2048", "41641", "udp", "443"]),          # superseded name = soft
                                          ("alert-only", ["shape_oob", "lte0", "2048", "41641", "udp", "443"]),   # superseded name = soft
                                          ("cut", ["disconnect", "lte0"])])
def test_budget_exhaustion_acts_once_per_on_budget(mode, expect):
    c = cfg(on_budget=mode)
    st, _ = _enter(c)
    bud = st["metered"]["c1"]["budget_bytes"]
    f = facts(lte_bytes=1000 + bud)
    st, acts = oob.tick(c, f, st, {}, T0 + 60)
    assert expect in acts
    assert [a for a in acts if a[0] == "alert" and a[1] == "5"]
    st, acts2 = oob.tick(c, f, st, {}, T0 + 120)
    assert not [a for a in acts2 if a[0] == "alert"]               # one alert only
    assert expect in acts2                                         # and the action is re-asserted
    if mode == "cut":
        assert ["route_off"] in acts                               # the only path is cut
    else:
        assert ["route_on", "lte0"] in acts


def test_cut_moves_to_the_next_candidate():
    c = cfg(on_budget="cut", candidates=[cfg_raw()["candidates"][1], {"interface": "sat0"}])
    st, _ = _enter(c)
    f = facts(sat=(True, "203.0.113.1"), lte_bytes=1000 + st["metered"]["c0"]["budget_bytes"])
    st, acts = oob.tick(c, f, st, {}, T0 + 60)
    assert ["disconnect", "lte0"] in acts and ["route_on", "sat0"] in acts


def test_counter_reset_and_reenter_keep_the_budget():
    c = cfg()
    st, _ = _enter(c)
    bud = st["metered"]["c1"]["budget_bytes"]
    st, _ = oob.tick(c, facts(lte_bytes=5000), st, {}, T0 + 60)
    st, _ = oob.tick(c, facts(lte_bytes=300), st, {}, T0 + 120)    # counter reset (reboot)
    assert st["metered"]["c1"]["used_bytes"] == 4000 + 300
    st, acts = oob.enter(c, facts(lte_bytes=300), st, {}, T0 + 180, "again")
    assert st["metered"]["c1"]["budget_bytes"] == bud and "re-asserted" in acts[0][1]


def test_budget_unavailable_fails_closed_but_the_channel_stays_up():
    st, acts = oob.enter(cfg(), facts(), {}, {}, T0, "t")             # no vnstat data
    assert st["metered"]["c1"]["exhausted"]                           # the BUDGET fails closed (alert)
    assert ["shape_oob", "lte0", "2048", "41641", "udp", "443"] in acts and ["route_on", "lte0"] in acts
    assert any(a[0] == "alert" and a[1] == "5" for a in acts)


def test_default_on_budget_is_soft_and_only_soft_or_cut_exist():
    raw = cfg_raw()
    del raw["on_budget"]
    assert oob.validate(raw)["on_budget"] == "soft"
    assert cfg(on_budget="cap")["on_budget"] == "soft"


@pytest.mark.parametrize("over, why", [
    ({"hard_cap_kbit": 32}, "64..1000000"),
    ({"floor_kbit": 4096}, "64..hard_cap_kbit"),
    ({"throttled_kbit": 300}, "64..floor_kbit"),
])
def test_cap_settings_are_bounded(over, why):
    with pytest.raises(oob.OobError, match=why):
        cfg(**over)


def test_a_candidate_may_only_lower_the_hard_cap():
    raw = cfg_raw()
    raw["candidates"][0]["hard_cap_kbit"] = 4096
    with pytest.raises(oob.OobError, match="may only LOWER"):
        oob.validate(raw)
    raw["candidates"][0]["hard_cap_kbit"] = 1024
    c = oob.validate(raw)
    st, acts = oob.enter(c, facts(sat=(True, "203.0.113.1")), {}, {"lte0": vnstat()}, T0, "t")
    assert ["shape_oob", "sat0", "1024", "41641", "udp", "443"] in acts
    assert ["cap_on", "lte0", "2mbit"] in acts                        # the metered link that is NOT the OOB one


def test_an_unmetered_link_gets_the_hard_cap_too_and_loses_it_only_at_leave():
    c = cfg()
    f = facts(sat=(True, "203.0.113.1"), lte_bytes=1000)
    st, acts = oob.enter(c, f, {}, {"lte0": vnstat()}, T0, "t")
    assert ["shape_oob", "sat0", "2048", "41641", "udp", "443"] in acts and ["route_on", "sat0"] in acts
    st, acts = oob.tick(c, f, st, {}, T0 + 60)
    assert ["shape_oob", "sat0", "2048", "41641", "udp", "443"] in acts and ["cap_off", "sat0"] not in acts
    st, acts = oob.leave(c, f, st)
    assert ["cap_off", "sat0"] in acts and ["cap_on", "lte0", "2mbit"] in acts


def test_used_up_allowance_shapes_just_under_the_floor():
    c = cfg()
    st, acts = _enter(c, used=46 * 10 ** 9)                           # 54.5 + 46 > 100 GB: no high-speed data left
    assert st["metered"]["c1"]["allowance_out"]
    assert ["shape_oob", "lte0", "230", "41641", "udp", "443"] in acts
    assert any(a[0] == "alert" and "allowance looks used up" in a[2] for a in acts)


def test_allowance_running_out_during_the_lockdown_drops_to_the_throttled_rate_once():
    c = cfg(candidates=[{"connection": "LTE Puck", "metered": dict(cfg_raw()["candidates"][1]["metered"], budget_fraction=1.0)}])
    st, _ = _enter(c, used=45 * 10 ** 9)                              # 0.5 GB left
    left = st["metered"]["c0"]["remaining_bytes"]
    assert left == 500_000_000
    st, acts = oob.tick(c, facts(lte_bytes=1000 + left), st, {}, T0 + 60)
    assert ["shape_oob", "lte0", "230", "41641", "udp", "443"] in acts
    assert len([a for a in acts if a[0] == "alert" and "allowance" in a[2]]) == 1
    st, acts = oob.tick(c, facts(lte_bytes=2000 + left), st, {}, T0 + 120)
    assert ["shape_oob", "lte0", "230", "41641", "udp", "443"] in acts and not [a for a in acts if a[0] == "alert"]


def test_three_sources_take_the_most_conservative():
    raw = cfg_raw()
    raw["candidates"][1]["metered"].update(other_usage_gb=30.0, other_usage_at="2026-11-18T04:00:00Z")
    m = oob.validate(raw)["candidates"][1]["metered"]
    b = oob.budget(m, vnstat(month_bytes=61805647109 + 500_000_000), T0, "lte0")
    assert b["used_bytes"] == 54_500_000_000 + 500_000_000 + 30_000_000_000
    assert b["remaining_bytes"] == 15_000_000_000 and b["method"].endswith("+ other")
    assert "not seen by this box" in b["sources"]["other"]["label"] and b["sources"]["other"]["age_s"] == 3600
    # the box's own live count beats a stale carrier figure when it is higher
    cs = oob.cycle_start(T0, 25, "America/New_York")
    hours = [(cs - 3600, 0)] + [(cs + 3600 * i, 10 ** 9) for i in range(70)]
    b = oob.budget(m, vnstat(month_bytes=61805647109, hours=hours), T0, "lte0")
    assert b["sources"]["live"]["used_bytes"] > b["sources"]["carrier"]["used_bytes"]
    assert b["method"] == "most conservative of 3 sources: live + other"


def test_other_use_needs_a_time_and_an_old_one_is_ignored():
    raw = cfg_raw()
    raw["candidates"][1]["metered"].update(other_usage_gb=5.0)
    with pytest.raises(oob.OobError, match="other_usage_at"):
        oob.validate(raw)
    raw["candidates"][1]["metered"].update(other_usage_at="2026-10-01T00:00:00Z")   # an earlier cycle
    m = oob.validate(raw)["candidates"][1]["metered"]
    b = oob.budget(m, vnstat(month_bytes=61805647109), T0, "lte0")
    assert b["sources"]["other"]["used_bytes"] == 0 and "older" in b["sources"]["other"]["label"]


def test_overrides_bring_in_a_newer_other_use_figure():
    c = cfg()
    oob.apply_override(c, {"c1": {"other_usage_gb": 12.5, "other_usage_at": "2026-11-18T03:00:00Z"}})
    assert c["candidates"][1]["metered"]["other_usage_gb"] == 12.5


def test_floor_plan_restores_the_normal_cap():
    p = oob.floor_plan(cfg(), facts(), {})
    assert p == {"key": "c1", "device": "lte0", "floor_kbit": 256, "hard_cap_kbit": 2048, "port": 41641,
                 "protocol": "udp", "relay_tcp_port": 443, "restore": ["cap_on", "lte0", "2mbit"]}
    p = oob.floor_plan(cfg(), facts(sat=(True, "203.0.113.1")), {})
    assert p["device"] == "sat0" and p["restore"] == ["cap_off", "sat0"]


def test_leave_removes_routing_and_recaps():
    c = cfg()
    st, _ = _enter(c)
    st, acts = oob.leave(c, facts(), st)
    assert acts[:2] == [["route_off"], ["sshd_off"]] and ["cap_on", "lte0", "2mbit"] in acts
    assert st["active"] is False


# -- keys and pins ------------------------------------------------------------------------------------------

def _ssh_pub(kind, seed):
    def s(b):
        return len(b).to_bytes(4, "big") + b
    pk = hashlib.sha256(seed.encode()).digest()
    if kind == "ed25519":
        blob = s(b"ssh-ed25519") + s(pk)
        return f"ssh-ed25519 {base64.b64encode(blob).decode()} {seed}"
    blob = s(b"sk-ssh-ed25519@openssh.com") + s(pk) + s(b"ssh:")
    return f"sk-ssh-ed25519@openssh.com {base64.b64encode(blob).decode()} {seed}"


def _rows(primary_kind="ed25519", role="admin", active=True, approver_role="admin"):
    return [{"source": "board_signers", "account": "op", "pubkey": _ssh_pub(primary_kind, "p"), "role": role, "kind": "human", "active": active},
            {"source": "approval_signers", "account": "op", "pubkey": _ssh_pub("ed25519", "a"), "role": approver_role, "kind": "human", "active": True}]


def _build(tmp_path, c, rows):
    mat = tmp_path / "mat"
    text = oob.pins_build(c, rows, str(tmp_path), str(mat), T0)
    return oob.pins_parse(text), (mat / "ssh.pub").read_text().splitlines(), text


def test_every_source_type_builds_and_missing_is_recorded(tmp_path):
    (tmp_path / "spare.pub").write_text(_ssh_pub("ed25519", "spare") + "\n")
    key = Pgp(b"bg")
    (tmp_path / "bg.asc").write_bytes(key.pubkey_file())
    c = cfg(pins=[{"role": "primary", "source": "board_signers", "account": "op"},
                  {"role": "approver", "source": "approval_signers", "account": "op"},
                  {"role": "spare", "source": "ssh_pubkey_file", "account": "op", "path": "spare.pub"},
                  {"role": "breakglass-1", "source": "gpg_asc", "path": "bg.asc", "fingerprint": key.fpr},
                  {"role": "breakglass-2", "source": "gpg_asc", "path": "absent.asc", "optional": True}])
    pins, lines, text = _build(tmp_path, c, _rows())
    assert {e["role"] for e in pins["ssh"]} == {"primary", "approver", "spare"}
    assert [e["fpr"] for e in pins["gpg"]] == [key.fpr]
    assert pins["missing"] == ["breakglass-2"]
    assert "AAAA" not in text                                       # fingerprints only, no key bodies
    ok, bad = oob.pins_check(pins, c, lines)
    assert len(ok["op"]) == 3 and not bad
    assert (tmp_path / "mat" / "gpg-breakglass-1.asc").exists()


def test_expected_gpg_fingerprint_mismatch_is_missing(tmp_path):
    (tmp_path / "bg.asc").write_bytes(Pgp(b"other").pubkey_file())
    c = cfg(pins=[{"role": "bg", "source": "gpg_asc", "path": "bg.asc", "fingerprint": "A" * 40}])
    pins, _, _ = _build(tmp_path, c, [])
    assert pins["missing"] == ["bg"] and not pins["gpg"]


@pytest.mark.parametrize("fido, kind, role, active, mode", [
    ("auto", "ed25519", "admin", True, "file-keys"),       # no admin hardware key: file keys accepted
    ("auto", "sk", "admin", True, "fido-only"),            # one active admin FIDO key flips it
    ("auto", "sk", "member", True, "file-keys"),           # a non-admin FIDO key does not
    ("auto", "sk", "admin", False, "file-keys"),           # an inactive one does not
    ("required", "ed25519", "admin", True, "fido-only"),
    ("off", "sk", "admin", True, "file-keys"),
])
def test_fido_modes(tmp_path, fido, kind, role, active, mode):
    raw = cfg_raw()
    raw["ssh"]["fido"] = fido
    c = oob.validate(raw)
    pins, lines, _ = _build(tmp_path, c, _rows(primary_kind=kind, role=role, active=active))
    assert pins["mode"] == mode
    ok, bad = oob.pins_check(pins, c, lines)
    got = [ln.split()[1] for ln in ok.get("op", [])]
    if mode == "fido-only":
        assert all(t.startswith("sk-") for t in got)
        assert any("fido-only" in b for b in bad)                  # the file approver key is refused
        assert oob.sshd_config(c, mode, ["100.64.0.1"]).count("PubkeyAcceptedAlgorithms sk-ssh-ed25519") == 1
    else:
        assert "PubkeyAcceptedAlgorithms" not in oob.sshd_config(c, mode, ["100.64.0.1"])
        assert "ssh-ed25519" in got                                # the file approver key passes


def test_pin_checker_rejects_unpinned_and_other_accounts(tmp_path):
    c = cfg()
    pins, lines, _ = _build(tmp_path, c, _rows())
    ok, bad = oob.pins_check(pins, c, lines + [_ssh_pub("ed25519", "intruder"), "garbage line"])
    assert len(ok["op"]) == 2
    assert any("not pinned" in b for b in bad) and any("unparseable" in b for b in bad)
    rows = _rows()
    rows[0]["account"] = "someone"
    c2 = cfg(pins=[{"role": "primary", "source": "board_signers", "account": "someone"}])
    pins2, lines2, _ = _build(tmp_path, c2, rows)
    ok2, bad2 = oob.pins_check(pins2, c2, lines2)
    assert not ok2 and "may not log in" in bad2[0]


def test_tampered_pin_mode_and_fido_mismatch_are_refused(tmp_path):
    c = cfg()
    _, _, text = _build(tmp_path, c, _rows(primary_kind="sk"))
    with pytest.raises(oob.OobError, match="rebuild"):
        oob.pins_parse(text.replace("mode: fido-only", "mode: file-keys"))
    raw = cfg_raw()
    raw["ssh"]["fido"] = "off"
    with pytest.raises(oob.OobError, match="rebuild the pins"):
        oob.pins_check(oob.pins_parse(text), oob.validate(raw), [])


def test_sshd_drop_in():
    t = oob.sshd_config(cfg(), "file-keys", ["100.64.0.1", "fd7a:115c:a1e0::1"])
    for ln in ("ListenAddress 100.64.0.1", "ListenAddress [fd7a:115c:a1e0::1]", "PasswordAuthentication no",
               "AuthenticationMethods publickey", "AllowUsers op", "AuthorizedKeysFile /etc/ctdc-oob/authorized_keys.d/%u",
               "PermitRootLogin no", "Compression yes", "TCPKeepAlive no", "ClientAliveInterval 30",
               "ClientAliveCountMax 8"):
        assert ln in t.splitlines()
    with pytest.raises(oob.OobError, match="not a tailnet address"):
        oob.sshd_config(cfg(), "file-keys", ["192.0.2.10"])


# -- GPG break-glass, with throwaway keys computed here ---------------------------------------------------

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P


def _xrec(y):
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    return _P - x if x % 2 else x


_BY = 4 * pow(5, _P - 2, _P) % _P
_B = (_xrec(_BY), _BY, 1, _xrec(_BY) * _BY % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = p[3] * 2 * _D * q[3] % _P
    d = p[2] * 2 * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _enc(p):
    zi = pow(p[2], _P - 2, _P)
    x, y = p[0] * zi % _P, p[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


class Pgp:
    """A throwaway OpenPGP v4 Ed25519 (legacy EdDSA) key: public key file and detached signatures."""

    def __init__(self, seed: bytes):
        h = hashlib.sha512(hashlib.sha256(seed).digest()).digest()
        a = int.from_bytes(h[:32], "little")
        self.a = (a & ((1 << 254) - 8)) | (1 << 254)
        self.prefix = h[32:]
        self.A = _enc(_mul(self.a, _B))
        self.ts = int(T0) - 86400 * 400
        oid = bytes.fromhex("2B06010401DA470F01")
        self.body = b"\x04" + self.ts.to_bytes(4, "big") + b"\x16" + bytes([len(oid)]) + oid + self._mpi(b"\x40" + self.A)
        self.fpr_bytes = hashlib.sha1(b"\x99" + len(self.body).to_bytes(2, "big") + self.body).digest()
        self.fpr = self.fpr_bytes.hex().upper()

    @staticmethod
    def _mpi(b):
        n = int.from_bytes(b, "big")
        return n.bit_length().to_bytes(2, "big") + n.to_bytes((n.bit_length() + 7) // 8, "big")

    @staticmethod
    def _pkt(tag, body):
        n = len(body)
        hdr = bytes([0xC0 | tag]) + (bytes([n]) if n < 192 else bytes([((n - 192) >> 8) + 192, (n - 192) & 0xFF]))
        return hdr + body

    @staticmethod
    def _sub(t, data):
        return bytes([len(data) + 1, t]) + data

    def _ed_sign(self, msg):
        r = int.from_bytes(hashlib.sha512(self.prefix + msg).digest(), "little") % _Q
        R = _enc(_mul(r, _B))
        k = int.from_bytes(hashlib.sha512(R + self.A + msg).digest(), "little") % _Q
        return R, ((r + k * self.a) % _Q).to_bytes(32, "little")

    def _sig(self, sigtype, data, ts, extra=b""):
        hashed = self._sub(2, ts.to_bytes(4, "big")) + self._sub(33, b"\x04" + self.fpr_bytes) + extra
        head = b"\x04" + bytes([sigtype, 22, 8]) + len(hashed).to_bytes(2, "big") + hashed
        digest = hashlib.sha256(data + head + b"\x04\xff" + len(head).to_bytes(4, "big")).digest()
        R, S = self._ed_sign(digest)
        unhashed = self._sub(16, self.fpr_bytes[-8:])
        return self._pkt(2, head + len(unhashed).to_bytes(2, "big") + unhashed + digest[:2] + self._mpi(R) + self._mpi(S))

    def pubkey_file(self):
        uid = b"Throwaway test key <test@example.invalid>"
        cert = b"\x99" + len(self.body).to_bytes(2, "big") + self.body + b"\xb4" + len(uid).to_bytes(4, "big") + uid
        return self._pkt(6, self.body) + self._pkt(13, uid) + self._sig(0x13, cert, self.ts, self._sub(27, b"\x03"))

    def sign(self, data: bytes):
        return self._sig(0x00, data, int(T0) - 60)


def _cmd(command="status", nonce="ab" * 16, exp=T0 + 3600, reason="test break-glass"):
    return (f"ctdc-oob-breakglass: 1\ncommand: {command}\nnonce: {nonce}\n"
            f"expires-utc: {dt.datetime.fromtimestamp(exp, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\nreason: {reason}\n").encode()


@pytest.fixture
def bg(tmp_path):
    key = Pgp(b"breakglass-one")
    kd = tmp_path / "keys"
    kd.mkdir()
    (kd / "gpg-breakglass-1.asc").write_bytes(key.pubkey_file())
    c = cfg(pins=[{"role": "breakglass-1", "source": "gpg_asc", "path": "x.asc"}],
            breakglass={"enabled": True, "commands": ["status", "end-lockdown"], "signers": ["breakglass-1"]})
    pins = {"fido": "auto", "mode": "file-keys", "ssh": [], "missing": [],
            "gpg": [{"channel": "gpg", "role": "breakglass-1", "fpr": key.fpr}]}
    return key, kd, c, pins, tmp_path


def _run_bg(bg, text, signer=None, now=T0):
    key, kd, c, pins, tmp = bg
    t, s = tmp / "cmd", tmp / "cmd.sig"
    t.write_bytes(text)
    s.write_bytes((signer or key).sign(text))
    return oob.breakglass(c, pins, str(kd), str(t), str(s), str(tmp / "nonces"), now, gpg=GPG)


def test_openpgp_fingerprint_of_the_throwaway_key():
    k = Pgp(b"x")
    assert oob.openpgp_fpr(k.pubkey_file()) == k.fpr


def test_repo_breakglass_key_fingerprint_matches_security_md():
    p = ROOT / "security/breakglass-authorization-key.pub.asc"
    if not p.exists():
        pytest.skip("no break-glass public key in this tree")
    assert oob.openpgp_fpr(p.read_bytes()) == "5DA4A5A13949643EB7BF93A40B0744999425A548"


@pytest.mark.skipif(GPG is None, reason="gpg required")
def test_breakglass_good_then_replay(bg):
    r = _run_bg(bg, _cmd())
    assert r["command"] == "status" and r["signer"] == "breakglass-1"
    assert (bg[4] / "nonces").read_text().startswith("ab" * 16)
    with pytest.raises(oob.OobError, match="replay"):
        _run_bg(bg, _cmd())


@pytest.mark.skipif(GPG is None, reason="gpg required")
def test_breakglass_wrong_key_is_refused(bg):
    with pytest.raises(oob.OobError):
        _run_bg(bg, _cmd(nonce="cd" * 16), signer=Pgp(b"not-the-pinned-key"))
    assert not (bg[4] / "nonces").exists()


@pytest.mark.skipif(GPG is None, reason="gpg required")
def test_breakglass_tampered_text_is_refused(bg):
    key, kd, c, pins, tmp = bg
    (tmp / "cmd").write_bytes(_cmd(command="end-lockdown", nonce="ef" * 16))
    (tmp / "cmd.sig").write_bytes(key.sign(_cmd(command="status", nonce="ef" * 16)))
    with pytest.raises(oob.OobError, match="BADSIG|no single good"):
        oob.breakglass(c, pins, str(kd), str(tmp / "cmd"), str(tmp / "cmd.sig"), str(tmp / "nonces"), T0, gpg=GPG)


@pytest.mark.parametrize("text, why", [
    (_cmd(exp=T0 - 1), "expired"),
    (_cmd(exp=T0 + 25 * 3600), "max_ttl_hours"),
    (_cmd(command="restore-network"), "allowlist"),     # known, but not on THIS config's allowlist
    (_cmd(command="reboot"), "allowlist"),
    (_cmd(nonce="short"), "nonce"),
    (b"command: status\n", "needs exactly"),
])
def test_breakglass_text_checks(bg, text, why):
    with pytest.raises(oob.OobError, match=why):
        _run_bg(bg, text)


def test_breakglass_disabled(bg):
    key, kd, c, pins, tmp = bg
    c = dict(c, breakglass=dict(c["breakglass"], enabled=False))
    with pytest.raises(oob.OobError, match="disabled"):
        oob.breakglass(c, pins, str(kd), str(tmp / "x"), str(tmp / "y"), str(tmp / "n"), T0)


# -- the shell, through its test seam ----------------------------------------------------------------------

def _seam(tmp_path, raw=None, f=None):
    etc = tmp_path / "etc/ctdc-oob"
    (etc / "keys").mkdir(parents=True)
    raw = raw or cfg_raw()
    (etc / "oob.json").write_text(json.dumps(raw))
    c = oob.validate(raw)
    text = oob.pins_build(c, _rows(), str(tmp_path), str(etc / "keys"), T0)
    (etc / "pinned-fingerprints").write_text(text)
    (tmp_path / "facts.json").write_text(json.dumps(f or facts(lte_bytes=1000)))
    (tmp_path / "vnstat").mkdir()
    (tmp_path / "vnstat/vnstat-lte0.json").write_text(json.dumps(vnstat()))
    return tmp_path


def _sh(root, *args, now=None):
    env = dict(os.environ, CTDC_OOB_TEST_ROOT=str(root), PATH="/usr/sbin:/usr/bin:/bin")
    if now is not None:
        env["CTDC_OOB_TEST_NOW"] = str(int(now))
    return subprocess.run(["bash", str(OOB_SH), *args], capture_output=True, text=True, env=env, timeout=60)


def test_shell_enter_tick_leave(tmp_path):
    root = _seam(tmp_path)
    r = _sh(root, "enter", "--reason", "seam")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "ip -4 route replace default via 192.0.2.1 dev lte0 table 3313" in out
    assert "ip -4 rule add priority 5200 fwmark 0xcdc/0xfff table 3313" in out
    assert "TEST: tc qdisc replace dev lte0 root handle 1: cake bandwidth 2048kbit diffserv4" in out   # hard cap
    assert "TEST: tc qdisc add dev ifblte0 root handle 2: cake bandwidth 2048kbit diffserv4 ingress" in out
    assert "match ip dport 41641 0xffff classid 2:4" in out and "match ip6 dport 41641 0xffff classid 2:4" in out
    assert "matchall classid 2:1" in out
    assert "TEST: tc qdisc del dev lte0 root" not in out                # never uncapped
    assert "TEST: sshd -t (candidate drop-in)" in out and "TEST: systemctl reload sshd.service" in out
    assert "wan0" not in out.replace("never", "")
    drop = (root / "etc/ssh/sshd_config.d/05-ctdc-oob.conf").read_text()
    assert "ListenAddress 100.64.0.1" in drop and "AllowUsers op" in drop
    assert (root / "etc/ctdc-oob/authorized_keys.d/op").read_text().count("ctdc-oob:") == 2
    st = json.loads((root / "var/lib/ctdc-oob/state.json").read_text())
    f = facts(lte_bytes=1000 + st["metered"]["c1"]["budget_bytes"])
    (root / "facts.json").write_text(json.dumps(f))
    r = _sh(root, "budget-check")
    assert "TEST: tc qdisc replace dev lte0 root handle 1: cake bandwidth 2048kbit diffserv4" in r.stdout   # soft: stays up
    assert "TEST: ntfy priority 5" in r.stdout and "soft budget" in r.stdout
    r = _sh(root, "leave", "--reason", "done")
    assert r.returncode == 0, r.stderr
    assert "TEST: nft delete table inet ctdc_oob" in r.stdout and "sshd OOB drop-in removed" in r.stdout
    assert "TEST: tc qdisc replace dev lte0 root cake bandwidth 2mbit" in r.stdout
    assert not (root / "var/lib/ctdc-oob/state.json").exists()


def test_shell_refuses_an_invalid_config(tmp_path):
    raw = cfg_raw()
    raw["surprise"] = 1
    root = _seam(tmp_path)
    (root / "etc/ctdc-oob/oob.json").write_text(json.dumps(raw))
    r = _sh(root, "enter")
    assert r.returncode == 78 and "invalid" in r.stderr


def test_pin_builder_seam(tmp_path):
    raw = cfg_raw()
    (tmp_path / "oob.json").write_text(json.dumps(raw))
    (tmp_path / "rows.json").write_text(json.dumps(_rows(primary_kind="sk")))
    env = dict(os.environ, CTDC_OOB_PIN_TEST="1")
    r = subprocess.run(["bash", str(PIN_SH), "--config", str(tmp_path / "oob.json"), "--rows", str(tmp_path / "rows.json"),
                        "--material-dir", str(tmp_path / "mat"), "--out", str(tmp_path / "pins")],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "mode fido-only" in r.stdout and "fido-only mode accepts hardware" in r.stdout
    assert "AAAA" not in (tmp_path / "pins").read_text()


# -- static wiring ---------------------------------------------------------------------------------------------

def test_lockdown_restore_and_boot_guard_wiring():
    lock = (ROOT / "scripts/lockdown.sh").read_text()
    rest = (ROOT / "scripts/restore-network.sh").read_text()
    boot = (ROOT / "scripts/ctdc-boot-guard.sh").read_text()
    assert 'OOB_ARGS=(enter --reason' in lock
    assert lock.index("ctdc-oob.sh\" \"${OOB_ARGS[@]}\"") < lock.index('if [[ -f "${STATE_FILE}" ]]; then')
    assert 'OOB_ARGS=(leave --reason' in rest and '! -f "${OOB_STATE}"' in rest
    assert rest.index("ctdc-oob.sh\" \"${OOB_ARGS[@]}\"") < rest.index('rm -f "${STATE_FILE}" "${PERSIST_FILE}" "${HOLD_FILE}"')
    assert "ctdc-oob.sh\" boot-reset" in boot


def test_root_install_and_units():
    inst = (ROOT / "scripts" / ("install-root-" + "copies.sh")).read_text()
    chk = (ROOT / "scripts/installed-check.sh").read_text()
    for f in ("scripts/ctdc-oob.sh", "scripts/lib/ctdc_oob.py", "config/network/oob.json",
              "config/network/oob-pinned-fingerprints",
              "systemd/system/corporatetraveldc-oob-budget.service", "systemd/system/corporatetraveldc-oob-budget.timer",
              "systemd/system/corporatetraveldc-oob-breakglass.service", "systemd/system/corporatetraveldc-oob-breakglass.path"):
        assert f in inst, f
    assert "config/network/oob.json) f=/etc/ctdc-oob/oob.json" in chk
    timer = (ROOT / "systemd/system/corporatetraveldc-oob-budget.timer").read_text()
    assert "OnUnitActiveSec=1min" in timer
    for u in ("corporatetraveldc-oob-budget.service", "corporatetraveldc-oob-breakglass.service"):
        t = (ROOT / "systemd/system" / u).read_text()
        assert "ExecStartPre=/usr/local/libexec/ctdc/installed-check.sh" in t and "/usr/local/libexec/ctdc/ctdc-oob.sh" in t


def test_private_files_never_reach_the_public_mirror():
    scrub = (ROOT / "scripts" / ("scrub-public-" + "tree.py")).read_text()
    for p in ("config/network/oob.json", "config/network/oob-pinned-fingerprints", "docs/LOCKDOWN_OOB_DEPLOYMENT.md"):
        assert f'"{p}"' in scrub, p


@pytest.mark.parametrize("path", ["scripts/ctdc-oob.sh", "scripts/lib/ctdc_oob.py", "scripts/oob-pin-build.sh",
                                  "docs/LOCKDOWN_OOB.md", "config/network/oob.example.json",
                                  "systemd/system/corporatetraveldc-oob-budget.service"])
def test_no_box_specific_names_in_public_files(path):
    t = (ROOT / path).read_text()
    for word in ("enu1", "end0", "wld0", "Hockey Puck", "jorransgateway", "Corporate Traveler WiFi", "100.94.",
                 "5DA4A5A1", "Starlink", "Google Fi"):
        assert word not in t, f"{word} in {path}"


@pytest.mark.parametrize("script", ["scripts/ctdc-oob.sh", "scripts/oob-pin-build.sh", "scripts/lockdown.sh",
                                    "scripts/restore-network.sh", "scripts/ctdc-boot-guard.sh"])
def test_shell_syntax(script):
    assert subprocess.run(["bash", "-n", str(ROOT / script)]).returncode == 0


def test_nft_gives_tailscaled_the_priority_tin_and_everything_else_bulk():
    t = OOB_SH.read_text()
    assert "meta mark set meta mark or ${MARK} meta priority set 1:4" in t
    assert 'oifname "$1" meta priority != 1:4 meta priority set 1:1' in t


def test_shell_prove_floor_shapes_to_the_floor_and_restores(tmp_path):
    root = _seam(tmp_path)
    r = _sh(root, "prove-floor", "--seconds", "60")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "TEST: tc qdisc replace dev lte0 root handle 1: cake bandwidth 256kbit diffserv4" in out
    assert "ip -4 rule add priority 5200 fwmark 0xcdc/0xfff table 3313" in out
    assert "time " in out and "op@100.64.0.1 'echo ok'" in out
    assert out.index("cake bandwidth 256kbit") < out.index("TEST: tc qdisc replace dev lte0 root cake bandwidth 2mbit")
    assert "prove-floor: lte0 restored" in out


def test_shell_prove_floor_refuses_during_a_real_lockdown(tmp_path):
    root = _seam(tmp_path)
    assert _sh(root, "enter").returncode == 0
    r = _sh(root, "prove-floor")
    assert r.returncode == 1 and "ACTIVE" in r.stderr


def test_shell_other_usage_and_rebaseline_as_of(tmp_path):
    root = _seam(tmp_path)
    cs = oob.cycle_start(T0, 25, "America/New_York")
    hours = [(cs - 3600, 0)] + [(cs + 3600 * i, 1000) for i in range(int((T0 + 6 * 3600 - cs) // 3600) + 1)]
    (root / "vnstat/vnstat-lte0.json").write_text(json.dumps(vnstat(hours=hours)))
    r = _sh(root, "other-usage", "--candidate", "c1", "--used-gb", "30", "--as-of", "2026-11-18T04:00:00Z")
    assert r.returncode == 0, r.stderr
    r = _sh(root, "rebaseline", "--candidate", "c1", "--used-gb", "60.25", "--as-of", "2026-11-18T10:30:00Z")
    assert r.returncode == 0, r.stderr
    ov = json.loads((root / "etc/ctdc-oob/baseline-overrides.json").read_text())["c1"]
    assert ov == {"baseline_at": "2026-11-18T10:30:00Z", "baseline_used_gb": 60.25, "baseline_vnstat_month_bytes": None,
                  "other_usage_gb": 30.0, "other_usage_at": "2026-11-18T04:00:00Z"}
    r = _sh(root, "other-usage", "--candidate", "c1", "--used-gb", "1", "--as-of", "2026-11-18 04:00")
    assert r.returncode == 64
    r = _sh(root, "status", now=T0 + 6 * 3600)
    assert "carrier figure as of 2026-11-18T10:30:00Z, not live" in r.stdout
    assert "source other: 30.00 GB" in r.stdout and "remaining (most conservative)" in r.stdout


# -- the VPN is configurable (operator 2026-11-18: "independent and agnostic as a configurable thing") --------

def test_vpn_defaults_are_tailscale():
    v = cfg()["vpn"]
    assert v == {"service": "tailscaled.service", "table": 3313, "rule_priority": 5200, "mark": "0xcdc", "mask": "0xfff",
                 "port": 41641, "protocol": "udp", "interface": "tailscale0", "relay_tcp_port": 443}


def test_superseded_tailscale_block_maps_to_vpn():
    v = cfg(tailscale={"service": "tailscaled.service", "port": 41642})["vpn"]
    assert v["port"] == 41642 and v["interface"] == "tailscale0" and v["protocol"] == "udp"
    assert "tailscale" not in cfg(tailscale={"port": 41642})      # one canonical block after validation


def _corp_vpn():
    return {"service": "corp-vpn.service", "protocol": "tcp", "port": 1194, "interface": "tun0", "relay_tcp_port": None}


def test_tcp_only_vpn_shapes_its_own_port_and_no_relay_match():
    c = cfg(candidates=[{"interface": "lte0"}], vpn=_corp_vpn())
    st, acts = oob.enter(c, facts(), {}, {}, T0, "t")
    assert ["shape_oob", "lte0", "2048", "1194", "tcp", "-"] in acts
    p = oob.floor_plan(c, facts(), {})
    assert (p["port"], p["protocol"], p["relay_tcp_port"]) == (1194, "tcp", None)


def test_vpn_without_a_port_match():
    c = cfg(candidates=[{"interface": "lte0"}], vpn={"port": None})
    st, acts = oob.enter(c, facts(), {}, {}, T0, "t")
    assert ["shape_oob", "lte0", "2048", "-", "udp", "443"] in acts


def test_facts_read_the_configured_tunnel_interface(monkeypatch, tmp_path):
    seen = []
    def fake(argv):
        seen.append(argv)
        if argv[:5] == ["ip", "-j", "addr", "show", "dev"]:
            return [{"addr_info": [{"scope": "global", "local": "10.x.x.x"}, {"scope": "link", "local": "fe80::1"}]}]
        return []
    monkeypatch.setattr(oob, "_run_json", fake)
    monkeypatch.setattr(oob, "_nm_active", lambda: {})
    (tmp_path / "lo").mkdir()
    f = oob.gather_facts(str(tmp_path), vpn_if="tun0")
    assert ["ip", "-j", "addr", "show", "dev", "tun0"] in seen and f["tailnet"] == ["10.x.x.x"]
    oob.gather_facts(str(tmp_path))
    assert ["ip", "-j", "addr", "show", "dev", "tailscale0"] in seen   # the default stays Tailscale's


def test_shell_config_exports_the_vpn(tmp_path):
    p = tmp_path / "oob.json"
    p.write_text(json.dumps(cfg_raw(vpn=_corp_vpn())))
    r = subprocess.run([sys.executable, str(LIBP), "--config", str(p), "--default-account", "op", "shell-config"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = r.stdout.splitlines()
    assert "TS_PORT=1194" in out and "VPN_PROTO=tcp" in out and "VPN_IF=tun0" in out and "RELAY_TCP_PORT=-" in out
    assert "TS_SERVICE=corp-vpn.service" in out


def test_shell_shapes_a_tcp_vpn_without_the_relay_match(tmp_path):
    root = _seam(tmp_path, raw=cfg_raw(vpn=_corp_vpn()))
    r = _sh(root, "enter", "--reason", "seam")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "match ip protocol 6 0xff match ip dport 1194 0xffff classid 2:4" in out
    assert "match ip6 protocol 6 0xff match ip6 dport 1194 0xffff classid 2:4" in out
    assert "sport 443" not in out and "41641" not in out
    assert "matchall classid 2:1" in out
