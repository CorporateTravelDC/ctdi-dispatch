"""
tests/web/test_withheld.py -- withheld categories (2026-11-02, operator directive):
LADD aircraft, privileged trains and privileged vessels are revealed only to a token
naming that category's grant (human or agent account alike) or to the master token,
which needs BOTH the operator's SSH approval and a GPG signature by the pinned operator
primary key, is admin-human only, covers every category (including ones added later),
and is wiped by any kill / lockdown. Kill switch, signed thaw, pairing, keyed SHA-512
audit. Identifiers are synthetic Q marks / test numbers; the GPG verifier is stubbed
(no gpg-agent or key material is used by the suite).
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import web.main as web_main  # noqa: E402
from auth.auth import _hash_token, generate_token  # noqa: E402
from common import agent_gateway as gw  # noqa: E402
from common import board_sign as bs  # noqa: E402
from common import db  # noqa: E402
from common import governance as gov  # noqa: E402
from common import withheld as w  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
KEY = "test-withheld-audit-key-" + "x" * 40
PIN = "3B29752DACA3544CEA60D01A7B81F49CD96C1631"          # the pinned operator primary key (public)
AGENT = "CC1509BD9278086F113EAF24E10F126919390B37"        # the agent delegate key (public)
LISTED = {"registration": "QLP01A", "hex": "abc9f0", "callsign": "QLP01A"}
PLAIN = {"registration": "QLP02B", "hex": "abc9f1", "callsign": "TST202"}
TRAIN_HELD, TRAIN_PLAIN = {"train_num": "9901"}, {"train_num": "171"}
VESSEL_HELD, VESSEL_PLAIN = {"mmsi": "999000001", "name": "TEST HELD"}, {"mmsi": "999000002", "name": "TEST OPEN"}


def _validsig(sig_fpr: str, primary: str) -> str:
    return f"[GNUPG:] NEWSIG\n[GNUPG:] VALIDSIG {sig_fpr} 2026-11-02 1793577600 0 4 0 22 10 00 {primary}\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    db.init_db_all()
    gov._ensure_approvals()
    with db.conn() as c:
        try:   # the SQLite test twin of auth_tokens predates pg_schema's department column
            c.execute("ALTER TABLE auth_tokens ADD COLUMN department TEXT")
        except Exception:  # noqa: BLE001
            pass
        w.ensure(c)
        gw.ensure(c)
        for t in ("withheld_settings", "withheld_identifiers", "withheld_pairings", "withheld_masters",
                  "withheld_audit", "auth_tokens", "approval_signers", "faa_ladd_aircraft"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM board_signers WHERE account IN ('opsadmin', 'ctdc-agent-x')")
        c.execute("INSERT INTO faa_ladd_aircraft (n_number, updated_at) VALUES (?, ?)", ("QLP01A", time.time()))
        c.execute("INSERT INTO faa_aircraft_registry (n_number, mode_s_hex, updated_at) VALUES (?, ?, ?)",
                  ("QLP01A", "ABC9F0", 0))
    monkeypatch.setenv(w.KEY_ENV, KEY)
    monkeypatch.setattr(w, "pinned_fingerprint", lambda: PIN)
    w._seen.update(minute=None, keys=set())
    keys = {}
    for name in ("appr", "op", "agent"):
        k = tmp_path / name
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{name}@x", "-f", str(k)], check=True)
        keys[name] = (str(k), (tmp_path / f"{name}.pub").read_text().strip())
    db.board_signer_upsert("opsadmin", keys["op"][1], "op@x", role="admin", kind="human")
    db.board_signer_upsert("ctdc-agent-x", keys["agent"][1], "agent@x", role="service", kind="service")
    gov.approval_signer_upsert("opsadmin", keys["appr"][1])
    w.list_add("rail", "train", "9901", "opsadmin")
    w.list_add("vessel", "mmsi", "999000001", "opsadmin")
    return {"key": keys["appr"][0], "client": TestClient(web_main.app)}


def _sign(env, approval_id, action="allow"):
    with db.conn() as c:
        row = dict(c.execute("SELECT * FROM approval_requests WHERE id = ?", (approval_id,)).fetchone())
    sig = bs.sign_with_ssh_keygen(env["key"], gov.approval_canonical(row, action), gov.NAMESPACE_APPROVAL)
    return gov.resolve_signed(approval_id, action, "opsadmin", sig)


def _token(actions: str | None, user="opsdesk", device="tablet", expires_in=3600.0) -> str:
    tok = generate_token(user, "cert", device, expires_at=time.time() + expires_in)
    with db.conn() as c:
        c.execute("UPDATE auth_tokens SET allowed_actions = ? WHERE token_hash = ?", (actions, _hash_token(tok)))
    return tok


def _req(headers: dict | None = None, path="/api/v1/swim/positions") -> Request:
    enc = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": enc, "method": "GET", "path": path, "client": ("100.64.0.1", 1),
                    "query_string": b"", "scheme": "http", "server": ("x", 80)})


def _view(tok: str | None, **extra) -> w.WithheldView:
    h = {w.PASS_HEADER: tok} if tok else {}
    h.update(extra)
    return w.resolve(_req(h))


def _master(env, device="desk", verify=None) -> dict:
    r = w.request_master(device, 4, "opsadmin")
    w.attach_gpg(r["approval_id"], "-----BEGIN PGP SIGNATURE-----", verify=verify or (lambda d, s: _validsig(
        "419A864CC29A09513039B6E03033FB4D01903159", PIN)))
    _sign(env, r["approval_id"])
    return w.pair(r["code"])


# -- categories and grants ------------------------------------------------------------

def test_each_category_has_its_own_explicit_grant():
    assert {c.grant for c in w.CATEGORIES.values()} == {"ladd.view", "rail.view", "vessel.view"}
    assert w.grants_of("ladd.view") == {"aircraft"} and w.grants_of("rail.view,vessel.view") == {"rail", "vessel"}
    for a in (None, "", "*", "admin.*", "ladd.*", "ladd.viewer", w.MASTER_ACTION):
        assert w.grants_of(a) == frozenset(), a


def test_sibling_passes_reveal_only_their_category(env):
    rail = _view(_token("rail.view"))
    assert w.screen(rail, "rail", [dict(TRAIN_HELD), dict(TRAIN_PLAIN)])[1] == 0
    assert w.screen(rail, "vessel", [dict(VESSEL_HELD), dict(VESSEL_PLAIN)]) == ([VESSEL_PLAIN], 1)
    assert w.screen(rail, "aircraft", [dict(LISTED), dict(PLAIN)]) == ([PLAIN], 1)
    anon = _view(None)
    for cat, held, plain in (("aircraft", LISTED, PLAIN), ("rail", TRAIN_HELD, TRAIN_PLAIN),
                             ("vessel", VESSEL_HELD, VESSEL_PLAIN)):
        assert w.screen(anon, cat, [dict(held), dict(plain)]) == ([plain], 1)
    assert w.category("rail").not_available == "No train available"
    assert w.category("vessel").not_available == "No vessel available"


def test_only_a_live_granted_token_reveals(env):
    assert not _view(_token(None)).grants                              # unrestricted admin-era token: no
    tok = _token("ladd.view")
    assert _view(tok).allowed("aircraft") and not _view(tok).allowed("rail")
    assert not _view(tok, **{"X-CTDI-Public": "1"}).grants             # tunnel-borne: never
    assert not _view(_token("ladd.view", expires_in=-5)).grants        # expired
    assert _view(_token("watchlist.flight.add,ladd.view", user="ctdc-agent-x")).allowed("aircraft")


def test_missing_audit_key_fails_closed(env, monkeypatch):
    tok = _token("ladd.view")
    monkeypatch.delenv(w.KEY_ENV)
    assert not _view(tok).grants


def test_lookup_failure_and_unauditable_reveal_withhold(env, monkeypatch):
    holder = _view(_token("ladd.view"))
    monkeypatch.setattr(w, "_insert", lambda rows: (_ for _ in ()).throw(RuntimeError("disk")))
    assert w.screen(holder, "aircraft", [dict(LISTED), dict(PLAIN)]) == ([PLAIN], 1)
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda x: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(w.WithheldUnavailable):
        w.screen(holder, "aircraft", [dict(PLAIN)])
    assert not w.visible_one(holder, "aircraft", dict(PLAIN))


@pytest.mark.parametrize("cs", ["DCM123", "FFL1234", "FWR9", "XAA12A", " xaa4402 "])
def test_pia_third_party_call_signs_are_withheld_like_ladd(env, cs):
    """2026-11-02: the four FAA-approved PIA call-sign providers (FltPlan DCM, ForeFlight
    FFL, FlightAware FWR, ARINCDirect XAA). A PIA aircraft's temporary hex is not in the
    registry, so the call sign is what identifies it; every LADD gate honours it."""
    item = {"callsign": cs, "hex": "abc9f2"}
    assert w.screen(_view(None), "aircraft", [dict(item), dict(PLAIN)]) == ([PLAIN], 1)
    assert w.screen(_view(_token("ladd.view")), "aircraft", [dict(item)])[1] == 0
    assert db.faa_is_ladd(cs) and db.faa_ladd_listed_batch([cs]) == {cs.strip().upper()}


@pytest.mark.parametrize("cs", ["DCMX", "FFL", "FWR12345", "XAAB12", "DAL1742", "FFLX12"])
def test_non_pia_call_signs_are_not_caught(env, cs):
    assert not db.is_pia_callsign(cs)


def test_a_vessel_listed_by_imo_alone_is_withheld(env):
    w.list_add("vessel", "imo", "9999991", "opsadmin")
    item = {"mmsi": "999000003", "imo": "9999991", "name": "TEST IMO ONLY"}
    assert w.screen(_view(None), "vessel", [dict(item), dict(VESSEL_PLAIN)]) == ([VESSEL_PLAIN], 1)
    assert w.screen(_view(_token("vessel.view")), "vessel", [dict(item)])[1] == 0


def test_country_default_and_opt_out_through_screen(env):
    german_private = {"registration": "D-AXYZ", "callsign": "DAXYZ", "hex": "3c1234"}
    canadian_private = {"registration": "C-GXYZ", "callsign": "CGXYZ", "hex": "c01234"}
    assert w.screen(_view(None), "aircraft", [dict(german_private), dict(PLAIN)]) == ([PLAIN], 1)
    assert w.screen(_view(_token("ladd.view")), "aircraft", [dict(german_private)])[1] == 0
    assert w.screen(_view(None), "aircraft", [dict(canadian_private)])[1] == 0
    w.list_add("aircraft", "reg", "C-GXYZ", "opsadmin")                  # an owner's opt-out request
    assert w.screen(_view(None), "aircraft", [dict(canadian_private)])[1] == 1


def test_lists_are_operator_kept_and_aircraft_takes_opt_outs(env):
    assert w.list_counts()["rail"] == 1 and w.list_counts()["vessel"] == 1
    assert w.list_add("aircraft", "reg", "QLP09Z", "opsadmin") == 1
    assert w.screen(_view(None), "aircraft", [{"registration": "QLP09Z", "hex": "abc9f3"}])[1] == 1
    with pytest.raises(w.WithheldError):
        w.list_add("rail", "mmsi", "1", "opsadmin")                   # wrong kind for the category
    assert w.list_remove("rail", "train", "9901", "opsadmin") == 1
    assert w.screen(_view(None), "rail", [dict(TRAIN_HELD)]) == ([TRAIN_HELD], 0)


# -- master token ------------------------------------------------------------------------

def test_master_reveals_every_category_including_future_ones(env, monkeypatch):
    out = _master(env)
    assert out["master"] is True and set(out["categories"]) == set(w.CATEGORIES)
    v = _view(out["token"])
    assert v.master and all(v.allowed(c) for c in w.CATEGORIES)
    future = w.Category("drone", "drone.view", "drone", (("uas", ("uas_id",), False),), lambda by_kind: set())
    monkeypatch.setitem(w.CATEGORIES, "drone", future)
    assert _view(out["token"]).allowed("drone")                         # a category added later is covered
    assert not _view(_token("ladd.view")).allowed("drone")


def test_master_needs_gpg_by_the_pinned_primary_key(env):
    r = w.request_master("desk", 4, "opsadmin")
    _sign(env, r["approval_id"])
    with pytest.raises(w.WithheldError) as e:
        w.pair(r["code"])                                               # SSH alone is not enough
    assert e.value.status == 403
    for status in ("", _validsig(AGENT, AGENT), _validsig("1" * 40, "2" * 40)):
        with pytest.raises(w.WithheldError):
            w.attach_gpg(r["approval_id"], "sig", verify=lambda d, s, st=status: st)
    seen = {}
    w.attach_gpg(r["approval_id"], "sig", verify=lambda d, s: seen.setdefault("data", d) and _validsig(PIN, PIN))
    assert seen["data"] == gov.approval_view(r["approval_id"])["command"].encode()   # signs the exact request
    assert w.pair(r["code"])["master"] is True


def test_master_needs_the_ssh_approval_too(env):
    r = w.request_master("desk", 4, "opsadmin")
    w.attach_gpg(r["approval_id"], "sig", verify=lambda d, s: _validsig(PIN, PIN))
    with pytest.raises(w.WithheldError) as e:
        w.pair(r["code"])
    assert e.value.status == 403


def test_master_is_admin_human_only(env):
    with pytest.raises(w.WithheldError) as e:
        w.request_master("desk", 4, "ctdc-agent-x")
    assert e.value.status == 403
    with pytest.raises(w.WithheldError):
        w.request_master("desk", 4, "opsdesk")                          # not a signer at all
    forged = _token(w.MASTER_ACTION, user="opsadmin")                   # a plain mint naming the action
    assert not _view(forged).grants
    out = _master(env)
    db.board_signer_upsert("opsadmin", "ssh-ed25519 AAAA x", "op@x", role="member", kind="human")
    assert not _view(out["token"]).grants                               # demoted holder: dead at once


def test_any_kill_wipes_the_master_and_thaw_does_not_restore_it(env):
    out = _master(env)
    th = _hash_token(out["token"])
    w.kill_all("opsadmin", "test")
    with db.conn() as c:
        assert c.execute("SELECT count(*) AS n FROM auth_tokens WHERE token_hash = ?", (th,)).fetchone()["n"] == 0
        assert c.execute("SELECT count(*) AS n FROM withheld_masters").fetchone()["n"] == 0
    with pytest.raises(w.WithheldError):
        w.request_master("desk", 4, "opsadmin")                         # no mint while frozen
    _sign(env, w.request_thaw()["approval_id"])
    assert not w.frozen() and not _view(out["token"]).grants            # thaw: still gone
    assert _master(env, device="desk2")["master"]                        # re-mint works


def test_gateway_kill_and_lockdown_wipe_the_master(env):
    out = _master(env)
    gw.kill_all("opsadmin", "test")
    assert w.frozen() and not _view(out["token"]).grants
    src = (REPO / "scripts/lockdown.sh").read_text()
    assert src.index("withheld.kill_all('lockdown'") < src.index("Already locked down since")


# -- kill switch, passes, pairing -------------------------------------------------------------

def test_kill_all_revokes_grant_tokens_only_and_thaw_needs_a_signature(env):
    p, agent, other = _token("ladd.view"), _token("watchlist.flight.add,vessel.view"), _token("admin.healthz")
    assert w.kill_all("opsadmin", "test") == 2
    assert db.lookup_token(_hash_token(p)) is None and db.lookup_token(_hash_token(agent)) is None
    assert db.lookup_token(_hash_token(other)) is not None
    fresh = _token("ladd.view")
    assert not _view(fresh).allowed("aircraft")
    t = w.request_thaw()
    _sign(env, t["approval_id"], "deny")
    assert w.frozen()
    _sign(env, w.request_thaw()["approval_id"])
    assert not w.frozen() and _view(fresh).allowed("aircraft")


def test_one_category_can_be_frozen_alone(env):
    a, r = _token("ladd.view"), _token("rail.view")
    w.kill_all("opsadmin", "rail only", ["rail"])
    assert w.frozen_set() == {"rail"}
    assert _view(a).allowed("aircraft") and db.lookup_token(_hash_token(r)) is None


def test_pairing_needs_the_signed_approval_and_works_once(env):
    r = w.request_pass("tablet", 4, "opsadmin", ["aircraft", "vessel"])
    with pytest.raises(w.WithheldError):
        w.pair(r["code"])
    _sign(env, r["approval_id"])
    out = w.pair(r["code"])
    v = _view(out["token"])
    assert v.allowed("aircraft") and v.allowed("vessel") and not v.allowed("rail") and not v.master
    with pytest.raises(w.WithheldError):
        w.pair(r["code"])
    for args in (("bad device!", 1, ["rail"]), ("tablet", 13, ["rail"]), ("tablet", 1, []), ("tablet", 1, ["nope"])):
        with pytest.raises(w.WithheldError):
            w.request_pass(args[0], args[1], "opsadmin", args[2])


# -- audit -----------------------------------------------------------------------------------

def test_freeze_logs_every_request_and_audit_check_finds_it(env):
    holder = _view(_token("rail.view", user="opsdesk", device="desk"))
    w.screen(holder, "rail", [dict(TRAIN_HELD)])
    w.kill_all("opsadmin", "test")
    for _ in range(2):
        w.screen(_view(None), "rail", [dict(TRAIN_HELD), dict(TRAIN_PLAIN)])
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM withheld_audit").fetchall()]
    assert {r["kind"] for r in rows} == {"reveal", "lockdown"} and all("9901" not in str(r) for r in rows)
    assert all(len(r["digest"]) == 128 for r in rows)
    hits = w.audit_check(["9901"])
    assert sum(h["kind"] == "lockdown" for h in hits) == 2 and any(h["user_label"] == "opsdesk" for h in hits)
    assert w.audit_check(["171"]) == []


# -- through the app -------------------------------------------------------------------

def test_routes_and_amtrak(env, monkeypatch):
    c = env["client"]
    svc = _token(None, user="runner")
    tok = _token("rail.view")
    auth = {"Authorization": f"Bearer {svc}"}
    assert c.get("/api/v1/withheld/whoami", headers={w.PASS_HEADER: tok}).json()["categories"] == ["rail"]
    body = {"category": "vessel", "items": [VESSEL_HELD, VESSEL_PLAIN]}
    assert c.post("/api/v1/withheld/screen", json=body, headers=auth).json()["withhold"] == [0]
    assert c.post("/api/v1/withheld/screen", json={"category": "nope", "items": []}, headers=auth).status_code == 400
    assert c.post("/api/v1/withheld/screen", json=body).status_code in (401, 403)
    monkeypatch.setattr(db, "get_latest_amtrak_status", lambda: {
        "delay_summary": "1 of 2 trains delayed. 9901 +30m", "fetched_at": 1.0,
        "trains_json": '[{"train_num": "9901", "delay_minutes": 30}, {"train_num": "171", "delay_minutes": 0}]'})
    pub = c.get("/api/v1/amtrak").json()
    assert [t["train_num"] for t in pub["trains"]] == ["171"] and "9901" not in pub["summary"]
    held = c.get("/api/v1/amtrak", headers={w.PASS_HEADER: tok}).json()
    assert len(held["trains"]) == 2 and "9901" in held["summary"]
    assert c.post("/api/v1/withheld/forget", headers={w.PASS_HEADER: tok}).json()["revoked"] == 1


def test_pinned_fingerprint_reads_only_the_operator_key():
    import importlib
    real = importlib.reload(w)
    assert real.pinned_fingerprint() == PIN                              # the tree's security/signing.env
    assert real.parse_validsig(_validsig("A" * 40, PIN)) == ("A" * 40, PIN)
    assert real.parse_validsig("[GNUPG:] BADSIG x") == ("", "")
