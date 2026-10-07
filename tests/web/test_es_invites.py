"""
tests/web/test_es_invites.py -- Executive Standard reader invites + promo codes
(2026-10-05): permanent-by-default personal invites revocable only by the
publisher, short-lived capped promo codes, single-use hand-offs, hashed
storage, device cap, freeze / kill-all.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from common import db  # noqa: E402
from common import es_invites as es  # noqa: E402

ME = "operator"


@pytest.fixture(autouse=True)
def clean():
    db.init_db()
    with db.conn() as c:
        es.ensure(c)
        for t in ("es_grants", "es_promos", "es_exchanges", "es_sessions", "es_invite_settings", "es_invite_events"):
            c.execute(f"DELETE FROM {t}")
        c.execute("UPDATE exec_standard_sources SET enabled = 1, cap = NULL")


def _code(url):
    return url.rsplit("/i/", 1)[1]


def _x(handoff):
    return parse_qs(urlparse(handoff).query)["x"][0]


def _sign_in(url):
    return es.exchange(_x(es.claim_invite(_code(url))))[0]


def test_invite_is_permanent_by_default_and_only_hashes_are_stored():
    r = es.invite_issue(ME, "Reader@Example.com")
    assert r["expires_at"] is None and r["url"].startswith(es.INVITE_BASE + "/i/")
    code = _code(r["url"])
    with db.conn() as c:
        dump = repr([dict(x) for x in c.execute("SELECT * FROM es_grants").fetchall()])
    assert code not in dump and "reader@example.com" in dump
    sid = _sign_in(r["url"])
    far = time.time() + 5 * 365 * 86400
    assert es.session_check(sid) == r["grant"]
    # still claimable years later: permanent until the publisher revokes it
    assert es.claim_invite(code, now=far).startswith(es.MEMBERS_BASE + "/redeem?x=")


def test_short_invite_expires():
    r = es.invite_issue(ME, label="panel guest", days=2)
    with pytest.raises(es.InviteError):
        es.claim_invite(_code(r["url"]), now=time.time() + 3 * 86400)


def test_handoff_is_single_use_and_short():
    r = es.invite_issue(ME, "a@example.com")
    x = _x(es.claim_invite(_code(r["url"])))
    es.exchange(x)
    with pytest.raises(es.InviteError):
        es.exchange(x)                                         # replay
    x2 = _x(es.claim_invite(_code(r["url"])))
    with pytest.raises(es.InviteError):
        es.exchange(x2, now=time.time() + es.EXCHANGE_TTL_S + 1)


def test_revoke_ends_every_device_at_once_and_the_link():
    r = es.invite_issue(ME, "a@example.com")
    s1, s2 = _sign_in(r["url"]), _sign_in(r["url"])
    out = es.revoke_grant(ME, "a@example.com", "left the firm")
    assert out["devices_signed_out"] == 2
    assert es.session_check(s1) is None and es.session_check(s2) is None
    with pytest.raises(es.InviteError):
        es.claim_invite(_code(r["url"]))


def test_device_cap_signs_out_the_oldest():
    r = es.invite_issue(ME, "a@example.com", max_devices=2)
    s1 = _sign_in(r["url"])
    time.sleep(0.01)
    s2 = _sign_in(r["url"])
    with db.conn() as c:                                       # s2 used more recently than s1
        c.execute("UPDATE es_sessions SET last_seen = ? WHERE id_hash = ?", (time.time() - 100, es._h(s1)))
    s3 = _sign_in(r["url"])
    assert es.session_check(s1) is None and es.session_check(s2) and es.session_check(s3)
    g = [x for x in es.grants() if x["id"] == r["grant"]][0]
    assert g["claims"] == 3 and g["devices"] == 2              # claims > devices = a shared link, visible


def test_reissue_kills_the_old_link_but_keeps_devices():
    r = es.invite_issue(ME, "a@example.com")
    sid = _sign_in(r["url"])
    new = es.invite_reissue(ME, r["grant"])
    with pytest.raises(es.InviteError):
        es.claim_invite(_code(r["url"]))
    assert es.session_check(sid) == r["grant"]
    assert _sign_in(new["url"])


def test_promo_is_short_lived_capped_and_never_permanent():
    with pytest.raises(es.InviteError):
        es.promo_create(ME, "too long", uses=5, code_days=31)
    with pytest.raises(es.InviteError):
        es.promo_create(ME, "forever", uses=5, access_days=365)
    p = es.promo_create(ME, "conference booth", uses=2, code_days=3, access_days=5)
    assert p["code"].startswith("ES-") and len(p["code"]) == 15
    typed = p["code"].lower().replace("-", " ").replace("0", "o")   # sloppy typing still works
    sid, gid = es.exchange(_x(es.redeem_promo(typed)))
    assert es.session_check(sid) == gid
    assert es.session_check(sid, now=time.time() + 6 * 86400) is None   # access ended after 5 days
    es.redeem_promo(p["code"])
    with pytest.raises(es.InviteError) as e:
        es.redeem_promo(p["code"])
    assert "fully redeemed" in e.value.detail
    p2 = es.promo_create(ME, "flyer", uses=50, code_days=1)
    with pytest.raises(es.InviteError) as e:
        es.redeem_promo(p2["code"], now=time.time() + 2 * 86400)
    assert "expired" in e.value.detail


def test_revoke_promo_optionally_ends_its_readers():
    p = es.promo_create(ME, "podcast", uses=10)
    sid, _ = es.exchange(_x(es.redeem_promo(p["code"])))
    es.revoke_promo(ME, p["promo"])
    with pytest.raises(es.InviteError):
        es.redeem_promo(p["code"])
    assert es.session_check(sid)                               # stop the code only
    assert es.revoke_promo(ME, p["promo"], readers=True)["readers_ended"] == 1
    assert es.session_check(sid) is None


def test_freeze_and_kill_all():
    r = es.invite_issue(ME, "a@example.com")
    sid = _sign_in(r["url"])
    es.freeze(ME)
    assert es.session_check(sid)                               # readers keep reading
    with pytest.raises(es.InviteError):
        es.claim_invite(_code(r["url"]))
    es.thaw(ME)
    assert es.kill_all(ME) == 1 and es.frozen()
    assert es.session_check(sid) is None
    es.thaw(ME)
    assert _sign_in(r["url"])                                  # the permanent link still works after a thaw
    assert [e["action"] for e in es.events()][:3] == ["thaw", "kill-all", "freeze"]


def test_disabled_source_stops_its_readers():
    r = es.invite_issue(ME, "a@example.com", source="direct")
    sid = _sign_in(r["url"])
    with db.conn() as c:
        c.execute("UPDATE exec_standard_sources SET enabled = 0 WHERE source = 'direct'")
    assert es.session_check(sid) is None
    with pytest.raises(es.InviteError):
        es.invite_issue(ME, "c@example.com")


def test_bad_codes_are_refused_without_leaking():
    for bad in ("", "x" * 100, "not-a-code"):
        with pytest.raises(es.InviteError):
            es.claim_invite(bad)
    assert es.normalize_promo("ES-1234") == ""
    assert es.session_check("") is None and es.session_check("nope") is None


# -- the members gate / invite host verifier (executivestandard-website/verifier/verify.py) --

_VERIFY = Path(__file__).resolve().parents[3] / "executivestandard-website" / "verifier" / "verify.py"


@pytest.fixture
def gate():
    import http.client
    import importlib.util
    import threading
    if not _VERIFY.exists():
        pytest.skip("executivestandard-website checkout not alongside")
    spec = importlib.util.spec_from_file_location("es_verify", _VERIFY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    srv = mod.http.server.ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def call(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode()
    yield call
    srv.shutdown()


def test_gate_full_browser_flow(gate):
    r = es.invite_issue(ME, "a@example.com")
    code = _code(r["url"])
    st, _, body = gate("GET", f"/_invite/i/{code}")             # a link preview: spends nothing
    assert st == 200 and "method='post'" in body
    with db.conn() as c:
        assert c.execute("SELECT count(*) FROM es_exchanges").fetchone()[0] == 0
    st, h, _ = gate("POST", f"/_invite/i/{code}", body="", headers={"Content-Length": "0"})
    assert st == 303 and h["Location"].startswith(es.MEMBERS_BASE + "/redeem?x=")
    redeem = "/redeem?" + h["Location"].split("?", 1)[1]
    st, h, _ = gate("GET", "/redeem", headers={"X-Original-URI": redeem})
    assert st == 302 and h["Location"] == "/"
    cookie = h["Set-Cookie"].split(";", 1)[0]
    assert cookie.startswith("__Host-es_s=") and "HttpOnly" in h["Set-Cookie"] and "Secure" in h["Set-Cookie"]
    assert gate("GET", "/auth", headers={"Cookie": cookie})[0] == 200
    st, h, _ = gate("GET", "/redeem", headers={"X-Original-URI": redeem})   # replayed hand-off
    assert st == 302 and "Set-Cookie" not in h
    es.revoke_grant(ME, r["grant"])
    assert gate("GET", "/auth", headers={"Cookie": cookie})[0] == 401


def test_gate_cannot_be_opened_by_headers_or_odd_paths(gate):
    for hdrs in ({}, {"X-ES-Site": "invite"}, {"Cookie": "__Host-es_s=nope; es_auth=nope"}):
        assert gate("GET", "/auth", headers=hdrs)[0] == 401
    assert gate("GET", "/anything")[0] == 401
    assert gate("POST", "/auth", body="", headers={"Content-Length": "0"})[0] == 405


def test_gate_promo_form(gate):
    p = es.promo_create(ME, "booth", uses=1)
    st, _, body = gate("GET", f"/_invite/p?code={p['code']}")
    assert st == 200 and p["code"] in body
    form = f"code={p['code']}"
    st, h, _ = gate("POST", "/_invite/p", body=form,
                    headers={"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(form))})
    assert st == 303 and "/redeem?x=" in h["Location"]
    st, _, body = gate("POST", "/_invite/p", body=form,
                       headers={"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(form))})
    assert st == 410 and "fully redeemed" in body
    st, _, body = gate("GET", "/_invite/p?code=%27%3E%3Cscript%3E")
    assert "<script>" not in body


def test_batch_issues_one_each_skips_repeats_and_is_all_or_nothing():
    es.invite_issue(ME, "already@example.com")
    good = "# core subscribers\nA@example.com, Alice\n\nb@example.com\na@example.com,Alice again\nalready@example.com\n"
    r = es.invite_batch(ME, good, campaign="core", days=None)
    assert [i["email"] for i in r["issued"]] == ["a@example.com", "b@example.com"]
    assert {k["email"]: k["why"] for k in r["skipped"]} == {"a@example.com": "repeated in the list",
                                                            "already@example.com": "already has a live invite"}
    assert r["issued"][0]["name"] == "Alice" and r["issued"][0]["expires_at"] is None
    before = len(es.grants())
    with pytest.raises(es.InviteError) as e:
        es.invite_batch(ME, "c@example.com\nnot-an-email\nd@example.com")
    assert "line 2" in e.value.detail and "c@example.com" not in e.value.detail
    assert len(es.grants()) == before                                 # nothing issued
    with pytest.raises(es.InviteError):
        es.invite_batch(ME, "\n".join(f"u{i}@example.com" for i in range(es.BATCH_MAX + 1)))
