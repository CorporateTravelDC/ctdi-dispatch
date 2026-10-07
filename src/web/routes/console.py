"""web.routes.console -- the operator's phone console (2026-10-05) for
Executive Standard reader access (common/es_invites.py) and the cloud-agent
gateway (common/agent_gateway.py). Same actions as scripts/es-invite.sh and
scripts/agent-gateway.sh, laid out for a phone.

Reach: the tailnet name only (nginx tailscale vhost, location ^~ /console ->
web :8000); any other Host gets 404 (CONSOLE_HOSTS).

Sign-in is a human SSH signature, not a password: "Sign in" creates an
approval (kind console-login) bound to a random nonce held ONLY in this
browser's cookie; the operator runs `approve.sh allow <id>` from the phone's
SSH app; reloading the page then mints an 8 h session for that browser and
no other (one approval -> at most one session). Nothing depends on a
background tab polling (mobile browsers pause them).

Inside a session the console can do what the CLIs do, with two exceptions
that stay signature-gated like everywhere else: re-opening the agent gateway
after a kill-all (gateway-thaw) and holding an agent link open
(connector-hold) -- the console creates the request and shows the command to
sign it. Linking a vendor stays the vendor-initiated signed connector-link.
Every POST carries a CSRF token derived from the session secret; cookies are __Host-, Secure,
HttpOnly, SameSite=Strict. Hashes only in the database.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from common import agent_gateway as gw
from common import db, es_invites as es, governance

router = APIRouter()
SESSION_TTL_S = 8 * 3600
LOGIN_TTL_S = 10 * 60
COOKIE = "__Host-ctdc_console"
PENDING = "__Host-ctdc_console_login"
HOSTS = {h.strip().lower() for h in os.environ.get(
    "CONSOLE_HOSTS", "corporatetraveldc-dispatch.tailxxxxxxx.ts.net").split(",") if h.strip()}
APPROVE_CMD = os.environ.get("CONSOLE_APPROVE_CMD",
                             "cd /opt/corporatetraveldc/private/ctdi-dispatch-internal && scripts/approve.sh allow")


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def ensure(c) -> None:
    from common import db_backend
    if db_backend.backend() == "postgres":
        return
    c.execute("""CREATE TABLE IF NOT EXISTS console_sessions (id_hash TEXT PRIMARY KEY, signer TEXT NOT NULL,
        approval_id TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, expires_at REAL NOT NULL,
        last_seen REAL, revoked_at REAL)""")


def _q(sql: str, args: tuple = (), one: bool = False):
    with db.conn() as c:
        ensure(c)
        cur = c.execute(sql, args)
        if sql.lstrip().upper().startswith("SELECT"):
            rows = [dict(r) for r in cur.fetchall()]
            return (rows[0] if rows else None) if one else rows
        return cur.rowcount


def _host_ok(request: Request) -> bool:
    return (request.headers.get("host") or "").split(":")[0].lower() in HOSTS


def _session(request: Request) -> dict | None:
    secret = request.cookies.get(COOKIE) or ""
    if not secret or len(secret) > 64:
        return None
    s = _q("SELECT * FROM console_sessions WHERE id_hash = ?", (_h(secret),), one=True)
    now = time.time()
    if not s or s["revoked_at"] or s["expires_at"] <= now:
        return None
    if not _signer_live(s["signer"]):                      # liveness chain: an inert human loses the console
        return None
    _q("UPDATE console_sessions SET last_seen = ? WHERE id_hash = ?", (now, s["id_hash"]))
    return s


def _signer_live(signer: str) -> bool:
    b = db.board_signer_get(signer)
    a = governance.approval_signer_get(signer)
    return bool(b and b.get("active") and b.get("kind") == "human" and a and a.get("active"))


def _csrf_for(request: Request) -> str:
    """Per-session CSRF token derived from the session secret (stable across tabs,
    uncomputable without the cookie)."""
    return _h("console-csrf:" + (request.cookies.get(COOKIE) or ""))[:40]


def _cookie(resp: Response, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, max_age=max_age, path="/", secure=True, httponly=True, samesite="strict")


# -- pages ---------------------------------------------------------------------------
# 2026-10-05 redesign (operator: "branded, not a geocities page"): the Dispatch
# Intelligence design system (runner/frontend/src/styles/main.css tokens -- navy
# #040812, cyan accent, GO / MARGINAL / NO-GO semantics, glass panels on the dot
# grid, uppercase letter-spaced labels) and the platform's SRU (screen reader
# user) conventions, after Tableau's "Azimuth: Designing Accessible Dashboards
# for Screen Reader Users" as used across the runner:
#   * skip link to <main>; banner / main landmarks; every panel a region
#     labelled by its heading;
#   * summary before detail: one polite live-region sentence states the whole
#     picture (readers, agents, approvals) before any panel;
#   * row data in real tables (caption, column + row headers); on a phone the
#     same table reflows to cards, so nothing is duplicated for SRUs;
#   * state never by colour alone -- every chip carries its word, and every
#     row action names its row ("Revoke r@example.com", not just "Revoke");
#   * results move focus to their heading; Copy / Share announce through a
#     live region; 44 px targets; visible focus; reduced motion honoured.

_CSS = """
:root{--font-ui:-apple-system,'SF Pro Display','Inter','Segoe UI Variable','Segoe UI',system-ui,sans-serif;
--font-data:'JetBrains Mono','SF Mono','Fira Code',ui-monospace,Menlo,monospace;
--bg:#040812;--panel:rgba(6,14,30,.82);--panel-2:rgba(9,18,40,.78);--glass:rgba(0,180,255,.13);
--border:rgba(0,140,220,.2);--border-2:rgba(0,200,255,.38);--grid:rgba(0,160,255,.05);
--cyan:#00d4ff;--cyan-dim:rgba(0,212,255,.11);--on-cyan:#03121e;--go:#00ff88;--go-dim:rgba(0,255,136,.1);
--marg:#ffd700;--marg-dim:rgba(255,215,0,.1);--nogo:#ff5560;--nogo-dim:rgba(255,48,64,.12);--nogo-solid:#c8202e;
--text:#ddeeff;--text-2:#8ab0d8;--muted:#5d7fa3;--radius:6px;--radius-lg:10px;color-scheme:dark}
@media (prefers-color-scheme:light){:root{--bg:#edf2fb;--panel:rgba(255,255,255,.86);--panel-2:rgba(240,246,255,.9);
--glass:rgba(0,100,200,.14);--border:rgba(0,80,190,.16);--border-2:rgba(0,120,220,.42);--grid:rgba(0,80,180,.05);
--cyan:#006dbb;--cyan-dim:rgba(0,109,187,.1);--on-cyan:#fff;--go:#00753a;--go-dim:rgba(0,136,64,.1);--marg:#8a5600;
--marg-dim:rgba(160,100,0,.1);--nogo:#b00f1e;--nogo-dim:rgba(192,16,32,.08);--nogo-solid:#b00f1e;--text:#09182c;
--text-2:#1e3c6e;--muted:#5a7698;color-scheme:light}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;min-height:100vh;color:var(--text);font:15px/1.55 var(--font-ui);
background:radial-gradient(ellipse 100% 45% at 50% -5%,rgba(0,70,200,.18),transparent),
radial-gradient(ellipse 55% 40% at 90% 95%,rgba(0,30,130,.12),transparent),var(--bg);
padding:0 max(16px,env(safe-area-inset-right)) calc(40px + env(safe-area-inset-bottom)) max(16px,env(safe-area-inset-left))}
body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
background-image:radial-gradient(var(--grid) 1px,transparent 1px);background-size:28px 28px}
.skip-nav{position:absolute;top:-999px;left:0;background:var(--cyan);color:var(--on-cyan);font-weight:700;
padding:.6rem 1rem;z-index:10;border-radius:0 0 var(--radius) var(--radius);text-decoration:none;letter-spacing:.06em}
.skip-nav:focus{top:0}
.sr-only{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;
overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
:focus-visible{outline:2px solid var(--cyan);outline-offset:3px;border-radius:4px}
header.bar,main{position:relative;z-index:1;max-width:60rem;margin:0 auto}
header.bar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;
padding:calc(12px + env(safe-area-inset-top)) 0 12px;border-bottom:1px solid var(--border);margin-bottom:18px}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.brand img{width:40px;height:40px;border-radius:10px;border:1px solid var(--glass);flex:none}
.brand-name{font-size:.68rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--cyan)}
.brand-sub{font-size:1.05rem;font-weight:600;letter-spacing:.02em;margin:0}
.who{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.who-id{font:500 .78rem/1.3 var(--font-data);color:var(--text-2)}
main{display:grid;gap:18px}
.panel{background:var(--panel);border:1px solid var(--glass);border-radius:var(--radius-lg);padding:18px;
-webkit-backdrop-filter:blur(18px) saturate(1.5);backdrop-filter:blur(18px) saturate(1.5);box-shadow:0 6px 28px rgba(0,0,0,.35)}
.panel-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:12px}
h1,h2{margin:0;text-wrap:balance}h2{font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--text-2)}
h3{font-size:.95rem;margin:0 0 .5rem}
.lede{color:var(--text-2);margin:.25rem 0 1rem;max-width:62ch}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.tile{background:var(--panel-2);border:1px solid var(--border);border-radius:var(--radius);padding:10px 12px}
.tile-v{font:600 1.45rem/1.1 var(--font-data);font-variant-numeric:tabular-nums}
.tile-k{font-size:.66rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-top:4px}
.chip{display:inline-flex;align-items:center;gap:.4em;padding:.22rem .65rem;border-radius:99px;font-size:.7rem;
font-weight:700;letter-spacing:.1em;text-transform:uppercase;border:1px solid currentColor;white-space:nowrap}
.chip::before{content:'';width:.5em;height:.5em;border-radius:50%;background:currentColor}
.chip.go{color:var(--go);background:var(--go-dim)}.chip.marg{color:var(--marg);background:var(--marg-dim)}
.chip.nogo{color:var(--nogo);background:var(--nogo-dim)}.chip.nogo::before{border-radius:1px}
.chip.idle{color:var(--muted)}.chip.marg::before{border-radius:0;transform:rotate(45deg)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr));gap:16px}
fieldset{border:1px solid var(--border);border-radius:var(--radius);padding:14px;margin:0;min-width:0}
legend{padding:0 .4rem;font-weight:600}
label{display:block;font-size:.8rem;color:var(--text-2);margin:.65rem 0 .25rem}
.hint{font-size:.78rem;color:var(--muted);margin:.25rem 0 0}
input,select,textarea{width:100%;font:inherit;font-size:16px;color:var(--text);background:var(--panel-2);
border:1px solid var(--border);border-radius:var(--radius);padding:.62rem .7rem;min-height:44px}
textarea{font-family:var(--font-data);font-size:14px;line-height:1.45}
input:focus,select:focus,textarea:focus{border-color:var(--border-2)}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.btn,button{display:inline-flex;align-items:center;justify-content:center;gap:.4em;font:600 .82rem/1.2 var(--font-ui);
letter-spacing:.06em;text-transform:uppercase;min-height:44px;padding:.6rem 1rem;border-radius:var(--radius);
border:1px solid var(--cyan);background:var(--cyan);color:var(--on-cyan);cursor:pointer;text-decoration:none}
.btn.ghost,button.ghost{background:transparent;color:var(--cyan);border-color:var(--border-2)}
.btn.ghost:hover,button.ghost:hover{background:var(--cyan-dim)}
button.danger{background:var(--nogo-solid);border-color:var(--nogo-solid);color:#fff}
button.danger.ghost{background:transparent;color:var(--nogo);border-color:var(--nogo)}
.full{width:100%;margin-top:.9rem}
form.inline{display:inline}
.acts{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.table-wrap{overflow-x:auto;margin-top:.4rem}
table{width:100%;border-collapse:collapse;font-size:.88rem}
caption{text-align:left;font-weight:600;padding:.2rem 0 .6rem}
th,td{text-align:left;padding:.6rem .55rem;border-bottom:1px solid var(--border);vertical-align:middle}
thead th{font-size:.66rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);font-weight:700}
tbody th{font-weight:600;word-break:break-word}
td.num{font-family:var(--font-data);font-variant-numeric:tabular-nums}
.flag{color:var(--marg);font-weight:700}
.empty{color:var(--muted);margin:.4rem 0}
.mono{font-family:var(--font-data);font-size:.85rem;word-break:break-all}
.secret{background:var(--panel-2);border:1px dashed var(--border-2);border-radius:var(--radius);padding:.75rem;margin:.4rem 0 .6rem}
.secret-k{font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-top:.8rem}
details{border:1px solid var(--border);border-radius:var(--radius);padding:.2rem .9rem;margin-top:.9rem}
summary{cursor:pointer;min-height:44px;display:flex;align-items:center;gap:.5rem;font-weight:600}
details[open] summary{margin-bottom:.4rem}
.danger-zone{border-color:var(--nogo);background:var(--nogo-dim)}
.danger-zone summary{color:var(--nogo)}
.check{display:flex;gap:.6rem;align-items:center;margin:.7rem 0;color:var(--text);font-size:.9rem}
.check input{width:22px;height:22px;min-height:0;accent-color:var(--nogo-solid)}
ol.steps{margin:.4rem 0 1rem;padding-left:1.3rem;color:var(--text-2)}ol.steps li{margin:.3rem 0}
ol.log{list-style:none;margin:0;padding:0}ol.log li{display:grid;grid-template-columns:auto 1fr;gap:.2rem .9rem;
padding:.5rem 0;border-bottom:1px solid var(--border);font-size:.86rem}
ol.log time{font-family:var(--font-data);color:var(--muted);font-size:.78rem}
.center{min-height:calc(100vh - 140px);display:grid;place-items:center}
.card-narrow{max-width:30rem;width:100%;text-align:left}
.card-narrow .logo{width:64px;height:64px;border-radius:14px;border:1px solid var(--glass);margin-bottom:12px}
a{color:var(--cyan)}
@media (max-width:640px){
 .row{grid-template-columns:1fr}
 table.cards thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
 table.cards,table.cards tbody,table.cards tr,table.cards th,table.cards td{display:block;width:100%}
 table.cards tr{border:1px solid var(--border);border-radius:var(--radius);padding:.4rem .2rem;margin-bottom:10px;background:var(--panel-2)}
 table.cards th,table.cards td{border:0;padding:.35rem .6rem}
 table.cards td[data-label]::before{content:attr(data-label);display:block;font-size:.62rem;letter-spacing:.12em;
 text-transform:uppercase;color:var(--muted)}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important;scroll-behavior:auto!important}}
"""

_JS = """<script>
(function(){
var say=document.getElementById('announce');
document.addEventListener('click',async function(e){
 var b=e.target.closest('[data-copy],[data-share]');if(!b)return;
 var el=document.getElementById(b.dataset.copy||b.dataset.share);var t=el.textContent.trim();
 var what=b.getAttribute('data-what')||'text';
 try{if(b.dataset.share&&navigator.share){await navigator.share({text:t});return}
  await navigator.clipboard.writeText(t);b.textContent='Copied';if(say)say.textContent='Copied '+what+'.';}
 catch(_){if(say)say.textContent='Could not copy. Select the '+what+' and copy it by hand.';}
});
var h=document.querySelector('[data-focus]');if(h){h.focus();}
})();
</script>"""


_HEAD = ("<link rel='manifest' href='/console/manifest.webmanifest'>"
         "<link rel='icon' type='image/png' sizes='32x32' href='/console/icons/favicon-32.png'>"
         "<link rel='apple-touch-icon' sizes='180x180' href='/console/icons/apple-touch-icon.png'>"
         "<meta name='theme-color' content='#040812'>"
         "<meta name='mobile-web-app-capable' content='yes'><meta name='apple-mobile-web-app-capable' content='yes'>"
         "<meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'>"
         "<meta name='apple-mobile-web-app-title' content='Dispatch Ops'>")


def _banner(right: str = "") -> str:
    return ("<header class='bar'><div class='brand'><img src='/console/icons/icon-192.png' alt=''>"
            "<div><div class='brand-name'>Corporate Travel Dispatch Intelligence</div>"
            "<p class='brand-sub'>Operator console</p></div></div>"
            f"{right}</header>")


def _page(title: str, body: str, status: int = 200, right: str = "") -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name='robots' content='noindex,nofollow'><meta name='referrer' content='no-referrer'>"
        f"{_HEAD}<title>{html.escape(title)} -- Dispatch Ops</title><style>{_CSS}</style></head><body>"
        "<a class='skip-nav' href='#main'>Skip to main content</a>"
        f"{_banner(right)}<main id='main' tabindex='-1'>{body}</main>"
        "<div id='announce' class='sr-only' role='status' aria-live='polite'></div>"
        f"{_JS}</body></html>",
        status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                     "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                     "script-src 'unsafe-inline'; img-src 'self'; manifest-src 'self'; "
                                     "form-action 'self'; frame-ancestors 'none'"})


def _e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _when(t) -> str:
    return time.strftime("%b %d %H:%M", time.localtime(t)) if t else "-"


def _chip(word: str, tone: str) -> str:
    """State in words, never by colour alone (tone only reinforces it)."""
    return f"<span class='chip {tone}'>{_e(word)}</span>"


def _secret_box(label: str, value: str, n: int) -> str:
    return (f"<div class='secret-k' id='k{n}'>{_e(label)}</div>"
            f"<div class='secret mono' id='s{n}' aria-labelledby='k{n}'>{_e(value)}</div>"
            f"<div class='acts'><button class='ghost' type='button' data-copy='s{n}' data-what='{_e(label)}'>Copy"
            f"<span class='sr-only'> {_e(label)}</span></button>"
            f"<button class='ghost' type='button' data-share='s{n}' data-what='{_e(label)}'>Share"
            f"<span class='sr-only'> {_e(label)}</span></button></div>")


def _approve_box(approval_id: str, what: str) -> str:
    return (f"<p>{_e(what)} needs your signature. On your phone's SSH app run:</p>"
            + _secret_box("command", f"{APPROVE_CMD} {approval_id}", 0))


def _form(csrf: str, action: str, inner: str, button: str, cls: str = "", label: str = "", **hidden) -> str:
    """label: what the button acts on, read to SRUs ("Revoke" -> "Revoke r@example.com")."""
    h = "".join(f"<input type='hidden' name='{_e(k)}' value='{_e(v)}'>" for k, v in hidden.items())
    sr = f"<span class='sr-only'> {_e(label)}</span>" if label else ""
    return (f"<form method='post' action='/console/act' class='inline'><input type='hidden' name='csrf' "
            f"value='{_e(csrf)}'><input type='hidden' name='action' value='{_e(action)}'>{h}{inner}"
            f"<button class='{cls}' type='submit'>{_e(button)}{sr}</button></form>")


def _signin_page(pending: dict | None = None) -> HTMLResponse:
    if pending:
        body = ("<div class='center'><section class='panel card-narrow' aria-labelledby='t'>"
                "<h1 id='t' tabindex='-1' data-focus>Waiting for your signature</h1>"
                + _approve_box(pending["id"], "Signing in") +
                "<p class='lede'>Then come back here and reload. This request expires in 10 minutes and only "
                "this browser can use it.</p><a class='btn full' href='/console'>I signed it -- reload</a>"
                "</section></div>")
        return _page("Waiting for your signature", body)
    return _page("Sign in", "<div class='center'><section class='panel card-narrow' aria-labelledby='t'>"
                 "<img class='logo' src='/console/icons/icon-192.png' alt=''>"
                 "<h1 id='t'>Operator console</h1>"
                 "<p class='lede'>Sign in with your approval key over SSH -- no password.</p>"
                 "<ol class='steps'><li>Tap <strong>Sign in</strong>.</li><li>Run the <code>approve.sh</code> "
                 "command it shows, from your phone's SSH app.</li><li>Come back and reload.</li></ol>"
                 "<form method='post' action='/console/login'><button class='full' type='submit'>Sign in</button>"
                 "</form></section></div>")


def _tile(value, key: str) -> str:
    return f"<div class='tile'><div class='tile-v'>{_e(value)}</div><div class='tile-k'>{_e(key)}</div></div>"


def _dashboard(s: dict, csrf: str) -> HTMLResponse:
    sm = es.summary()
    gfz = gw.frozen()
    readers = es.grants()
    promos = es.promos()
    conns = gw.status()
    connectors = gw.connector_list()
    linked = sum(1 for c in connectors if any(x["slug"] == c["slug"] and x["status"] != "revoked" for x in conns))
    pend = governance.approvals_pending(20)

    right = (f"<div class='who'><span class='who-id'>{_e(s['signer'])} &middot; until {_when(s['expires_at'])}</span>"
             f"<form method='post' action='/console/logout'><input type='hidden' name='csrf' value='{_e(csrf)}'>"
             "<button class='ghost' type='submit'>Sign out</button></form></div>")

    # -- summary before detail (Azimuth): one sentence for SRUs, tiles for the eye
    sentence = (f"Readers: sign-ins {'paused' if sm['frozen'] else 'open'}, {sm['invites']} invites, "
                f"{sm['devices']} devices signed in, {sm['live_promos']} live promo codes. "
                f"Agents gateway {'frozen' if gfz else 'open'}: {len(connectors)} connectors, {linked} linked. "
                f"{len(pend)} approval{'s' if len(pend) != 1 else ''} waiting for your signature.")
    out = [f"<section class='panel' aria-labelledby='sum-h'><div class='panel-head'><h2 id='sum-h'>Status</h2>"
           f"<div class='acts'>{_chip('Readers paused' if sm['frozen'] else 'Readers open', 'nogo' if sm['frozen'] else 'go')}"
           f"{_chip('Agents frozen' if gfz else 'Agents open', 'nogo' if gfz else 'go')}</div></div>"
           f"<p class='sr-only' role='status' aria-live='polite'>{_e(sentence)}</p>"
           "<div class='tiles' aria-hidden='true'>"
           + _tile(sm["invites"], "Invites") + _tile(sm["devices"], "Devices") + _tile(sm["promo_readers"], "Promo readers")
           + _tile(sm["live_promos"], "Live codes") + _tile(f"{linked}/{len(connectors)}", "Agents linked")
           + _tile(len(pend), "Awaiting signature") + "</div></section>"]

    # -- The Executive Standard
    r_rows = []
    for g in readers:
        who = g["email"] or g["label"] or g["id"]
        tone = {"active": "go", "unclaimed": "marg"}.get(g["status"], "nogo")
        shared = (" <span class='flag'>shared?</span>" if g["claims"] > g["max_devices"] else "")
        acts = _form(csrf, "es-signout", "", "Sign out", "ghost", label=f"devices of {who}", ref=g["id"])
        if g["kind"] == "invite":
            acts += _form(csrf, "es-reissue", "", "New link", "ghost", label=f"for {who}", ref=g["id"])
        acts += _form(csrf, "es-revoke", "", "Revoke", "danger ghost", label=who, ref=g["id"])
        r_rows.append(
            f"<tr><th scope='row'>{_e(who)}<div class='hint'>{_e(g['kind'])}</div></th>"
            f"<td data-label='Status'>{_chip(g['status'], tone)}</td>"
            f"<td data-label='Devices' class='num'>{g['devices']} of {g['max_devices']}"
            f"<span class='hint'> &middot; {g['claims']} sign-ins</span>{shared}</td>"
            f"<td data-label='Last seen' class='num'>{_when(g['last_seen'])}</td>"
            f"<td data-label='Expires' class='num'>{_when(g['expires_at']) if g['expires_at'] else 'Permanent'}</td>"
            f"<td data-label='Actions'><div class='acts'>{acts}</div></td></tr>")
    readers_table = (
        "<div class='table-wrap'><table class='cards'><caption>Readers "
        f"<span class='hint'>({len(readers)})</span></caption><thead><tr><th scope='col'>Reader</th>"
        "<th scope='col'>Status</th><th scope='col'>Devices</th><th scope='col'>Last seen</th>"
        "<th scope='col'>Expires</th><th scope='col'><span class='sr-only'>Actions</span></th></tr></thead>"
        f"<tbody>{''.join(r_rows)}</tbody></table></div>" if r_rows else "<p class='empty'>No readers yet.</p>")
    p_rows = []
    for p in promos:
        tone = {"live": "go", "used up": "marg"}.get(p["status"], "nogo")
        acts = (_form(csrf, "es-promo-stop", "", "Stop code", "ghost", label=p["label"], ref=p["id"]) +
                _form(csrf, "es-promo-stop-all", "", "Stop + end readers", "danger ghost", label=p["label"], ref=p["id"]))
        p_rows.append(
            f"<tr><th scope='row'>{_e(p['label'])}</th><td data-label='Status'>{_chip(p['status'], tone)}</td>"
            f"<td data-label='Used' class='num'>{p['uses']} of {p['max_uses']}</td>"
            f"<td data-label='Code works until' class='num'>{_when(p['expires_at'])}</td>"
            f"<td data-label='Each reader gets' class='num'>{p['access_days']} days</td>"
            f"<td data-label='Actions'><div class='acts'>{acts}</div></td></tr>")
    promos_table = (
        "<div class='table-wrap'><table class='cards'><caption>Promo codes "
        f"<span class='hint'>({len(promos)})</span></caption><thead><tr><th scope='col'>Where</th>"
        "<th scope='col'>Status</th><th scope='col'>Used</th><th scope='col'>Code works until</th>"
        "<th scope='col'>Each reader gets</th><th scope='col'><span class='sr-only'>Actions</span></th></tr></thead>"
        f"<tbody>{''.join(p_rows)}</tbody></table></div>" if p_rows else "<p class='empty'>No live codes.</p>")
    switch = (_form(csrf, "es-thaw", "", "Resume sign-ins") if sm["frozen"]
              else _form(csrf, "es-freeze", "", "Pause new sign-ins", "ghost"))
    out.append(
        "<section class='panel' aria-labelledby='es-h'><div class='panel-head'><h2 id='es-h'>The Executive Standard</h2>"
        f"{_chip('Sign-ins paused' if sm['frozen'] else 'Sign-ins open', 'nogo' if sm['frozen'] else 'go')}</div>"
        "<div class='grid2'>"
        f"<form method='post' action='/console/act'><input type='hidden' name='csrf' value='{_e(csrf)}'>"
        "<input type='hidden' name='action' value='es-invite'><fieldset><legend>Invite a reader</legend>"
        "<label for='em'>Email</label><input id='em' name='email' type='email' autocomplete='off' "
        "aria-describedby='em-h'><p class='hint' id='em-h'>Or leave it blank and give a name below.</p>"
        "<label for='lb'>Name or note</label><input id='lb' name='label' autocomplete='off'>"
        "<div class='row'><div><label for='dy'>Lasts</label><select id='dy' name='days'><option value=''>Until I revoke it"
        "</option><option value='7'>7 days</option><option value='30'>30 days</option><option value='90'>90 days"
        "</option></select></div><div><label for='dv'>Devices</label><select id='dv' name='devices'><option>3</option>"
        "<option>1</option><option>2</option><option>5</option></select></div></div>"
        "<button class='full' type='submit'>Create invite link</button></fieldset></form>"
        f"<form method='post' action='/console/act'><input type='hidden' name='csrf' value='{_e(csrf)}'>"
        "<input type='hidden' name='action' value='es-promo'><fieldset><legend>Promo code</legend>"
        "<label for='pl'>Where it's going</label><input id='pl' name='label' required autocomplete='off' "
        "placeholder='e.g. conference booth'><div class='row'><div><label for='pu'>Uses</label>"
        "<input id='pu' name='uses' type='number' min='1' max='10000' value='25' inputmode='numeric'></div>"
        "<div><label for='pc'>Code works for</label><select id='pc' name='code_days'><option value='3'>3 days</option>"
        "<option value='7' selected>7 days</option><option value='14'>14 days</option><option value='30'>30 days"
        "</option></select></div></div><label for='pa'>Each reader gets</label><select id='pa' name='access_days'>"
        "<option value='3'>3 days</option><option value='7' selected>7 days</option><option value='14'>14 days</option>"
        "<option value='30'>30 days</option></select><p class='hint'>Never permanent; codes stop working on their own."
        "</p><button class='full' type='submit'>Create promo code</button></fieldset></form></div>"
        f"<form method='post' action='/console/act'><input type='hidden' name='csrf' value='{_e(csrf)}'>"
        "<input type='hidden' name='action' value='es-batch'><details><summary>Invite a list</summary>"
        "<label for='bl'>One per line: email, name</label>"
        "<textarea id='bl' name='batch' rows='6' required autocomplete='off' spellcheck='false' "
        "aria-describedby='bl-h'></textarea><p class='hint' id='bl-h'>Name optional. Nothing is issued if any line is "
        "invalid; repeats and readers who already have a live invite are skipped.</p>"
        "<div class='row'><div><label for='bd'>Lasts</label><select id='bd' name='days'><option value=''>Until I revoke it"
        "</option><option value='30'>30 days</option><option value='90'>90 days</option></select></div>"
        "<div><label for='bc'>Campaign tag</label><input id='bc' name='campaign' autocomplete='off' "
        "placeholder='e.g. core-subscribers'></div></div>"
        "<button class='full' type='submit'>Create invite links</button></details></form>"
        f"{readers_table}{promos_table}"
        f"<details class='danger-zone'><summary>Switches</summary><div class='acts'>{switch}</div>"
        + _kill_form(csrf, "es-kill-all", "Sign out every reader", "Pauses sign-ins and signs out every device. "
                     "Invite links survive; resume to let readers back in.")
        + "</details></section>")

    # -- agents gateway
    c_rows = []
    for c in connectors:
        live = [x for x in conns if x["slug"] == c["slug"] and x["status"] != "revoked"]
        st = live[0]["status"] if live else ("disabled" if c.get("disabled_at") else "not linked")
        tone = {"active": "go", "held": "go", "dormant": "marg", "not linked": "idle"}.get(st, "nogo")
        slug = f"/mcp/{c['slug']}"
        acts = ""
        if live:
            acts += _form(csrf, "gw-revoke", "", "Revoke link", "danger ghost", label=slug, ref=c["slug"])
            acts += (_form(csrf, "gw-release", "", "Release hold", "ghost", label=slug, ref=c["slug"]) if st == "held" else
                     _form(csrf, "gw-hold", f"<label class='sr-only' for='hd-{_e(c['slug'])}'>Hold {_e(slug)} open for"
                           f"</label><select id='hd-{_e(c['slug'])}' name='days' style='width:auto;min-width:5rem'>"
                           "<option value='7'>7 days</option><option value='30'>30 days</option>"
                           "<option value='90'>90 days</option></select>", "Hold open", "ghost", label=slug,
                           ref=c["slug"]))
        acts += (_form(csrf, "gw-enable", "", "Enable", "", label=slug, ref=c["slug"]) if c.get("disabled_at")
                 else _form(csrf, "gw-disable", "", "Disable", "ghost", label=slug, ref=c["slug"]))
        last = live[0] if live else {}
        c_rows.append(
            f"<tr><th scope='row'><span class='mono'>{_e(slug)}</span></th><td data-label='Account' class='mono'>"
            f"{_e(c['account'])}<div class='hint'>{_e(c['vendor'])}</div></td>"
            f"<td data-label='Status'>{_chip(st, tone)}</td>"
            f"<td data-label='Last renewal' class='num'>{_when(last.get('last_renewal_at'))}"
            f"{'<div class=hint>held until ' + _when(last.get('hold_until')) + '</div>' if last.get('hold_until') else ''}</td>"
            f"<td data-label='Actions'><div class='acts'>{acts}</div></td></tr>")
    conn_table = (
        "<div class='table-wrap'><table class='cards'><caption>Connectors "
        f"<span class='hint'>({len(connectors)})</span></caption><thead><tr><th scope='col'>Address</th>"
        "<th scope='col'>Account</th><th scope='col'>Status</th><th scope='col'>Last renewal</th>"
        "<th scope='col'><span class='sr-only'>Actions</span></th></tr></thead>"
        f"<tbody>{''.join(c_rows)}</tbody></table></div>" if c_rows else "<p class='empty'>No connectors.</p>")
    thaw = _form(csrf, "gw-thaw", "", "Re-open gateway (needs signature)") if gfz else ""
    out.append("<section class='panel' aria-labelledby='gw-h'><div class='panel-head'><h2 id='gw-h'>Agents gateway</h2>"
               f"{_chip('Frozen' if gfz else 'Open', 'nogo' if gfz else 'go')}</div>{conn_table}"
               + (f"<div class='acts' style='margin-top:.9rem'>{thaw}</div>" if thaw else
                  "<details class='danger-zone'><summary>Switches</summary>"
                  + _kill_form(csrf, "gw-kill-all", "Kill all agents", "Freezes the gateway, revokes every agent link "
                               "and disables every connector. Re-opening needs your signature; each vendor re-links.")
                  + "</details>")
               + "</section>")

    # -- pending approvals (signing happens over SSH only)
    a_rows = "".join(
        f"<li><details><summary>{_e(p.get('kind'))}: {_e(p.get('command_pattern'))}"
        f"<span class='hint'>&nbsp;&middot; {max(0, int((p['expires_at'] - time.time()) // 60))} min left</span></summary>"
        f"<p class='hint'>Requested by {_e(p.get('requester') or '-')}</p>"
        f"{_secret_box('sign with', APPROVE_CMD + ' ' + p['id'], 100 + i)}</details></li>" for i, p in enumerate(pend))
    out.append("<section class='panel' aria-labelledby='ap-h'><div class='panel-head'><h2 id='ap-h'>"
               "Waiting for your signature</h2>"
               f"{_chip(f'{len(pend)} pending', 'marg' if pend else 'idle')}</div>"
               + (f"<ul style='list-style:none;margin:0;padding:0'>{a_rows}</ul>" if a_rows else
                  "<p class='empty'>Nothing pending.</p>")
               + "<p class='hint'>Signing only ever happens over SSH with your approval key.</p></section>")

    ev = "".join(f"<li><time>{_when(e['ts'])}</time><span>{_e(e['action'])} {_e(e['target'] or '')} "
                 f"<span class='hint'>{_e(e['detail'] or '')} &middot; {_e(e['actor'])}</span></span></li>"
                 for e in es.events(12))
    out.append("<section class='panel' aria-labelledby='ev-h'><div class='panel-head'><h2 id='ev-h'>"
               "Recent reader activity</h2></div>"
               + (f"<ol class='log'>{ev}</ol>" if ev else "<p class='empty'>None yet.</p>") + "</section>")
    return _page("Operator console", "".join(out), right=right)


def _kill_form(csrf: str, action: str, button: str, text: str) -> str:
    return (f"<form method='post' action='/console/act'><input type='hidden' name='csrf' value='{_e(csrf)}'>"
            f"<input type='hidden' name='action' value='{_e(action)}'><fieldset style='margin-top:.8rem'>"
            f"<legend>{_e(button)}</legend><p class='hint'>{_e(text)}</p><label class='check'><input type='checkbox' "
            f"name='confirm' value='yes' required> I mean it</label><button class='danger full' type='submit'>"
            f"{_e(button)}</button></fieldset></form>")


def _result(title: str, body: str) -> HTMLResponse:
    return _page(title, "<section class='panel' aria-labelledby='rt'>"
                 f"<h1 id='rt' tabindex='-1' data-focus>{_e(title)}</h1>{body}"
                 "<a class='btn full' href='/console'>Back to console</a></section>")


# -- routes ----------------------------------------------------------------------------

# -- installable app (2026-10-05): manifest + icons, no service worker (nothing
#    here should be cached on the phone). Public bytes, but still tailnet Host only.
_ICONS = Path(__file__).resolve().parent.parent / "static" / "console"
_ICON_FILES = {"icon-192.png", "icon-512.png", "icon-512-maskable.png", "apple-touch-icon.png", "favicon-32.png"}
_MANIFEST = {
    "name": "Dispatch Operator Console", "short_name": "Dispatch Ops", "id": "/console",
    "description": "Executive Standard readers and the agents gateway",
    "start_url": "/console", "scope": "/console", "display": "standalone", "orientation": "portrait",
    "background_color": "#040812", "theme_color": "#040812",
    "icons": [
        {"src": "/console/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/console/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/console/icons/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}


@router.get("/console/manifest.webmanifest")
async def console_manifest(request: Request):
    if not _host_ok(request):
        return Response(status_code=404)
    return JSONResponse(_MANIFEST, media_type="application/manifest+json", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/console/icons/{name}")
async def console_icon(name: str, request: Request):
    if not _host_ok(request) or name not in _ICON_FILES:
        return Response(status_code=404)
    return Response((_ICONS / name).read_bytes(), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/console")
async def console(request: Request):
    if not _host_ok(request):
        return Response(status_code=404)
    s = _session(request)
    if s:
        return _dashboard(s, _csrf_for(request))
    pend = request.cookies.get(PENDING) or ""
    if "." in pend:
        aid, nonce = pend.split(".", 1)
        row = governance.approval_view(aid)
        ok = (row and row.get("kind") == "console-login" and
              json.loads(row["command"]).get("nonce_sha256") == _h(nonce))
        if ok and row["status"] == "allowed" and _signer_live(row.get("resolved_by") or ""):
            secret = secrets.token_urlsafe(32)
            now = time.time()
            try:
                _q("INSERT INTO console_sessions (id_hash, signer, approval_id, created_at, expires_at) "
                   "VALUES (?, ?, ?, ?, ?)", (_h(secret), row["resolved_by"], aid, now, now + SESSION_TTL_S))
            except Exception:                              # this approval already minted its one session
                resp = _signin_page()
                resp.delete_cookie(PENDING, path="/")
                return resp
            resp = RedirectResponse("/console", status_code=303)
            _cookie(resp, COOKIE, secret, SESSION_TTL_S)
            resp.delete_cookie(PENDING, path="/")
            return resp
        if ok and row["status"] == "pending":
            return _signin_page(row)
    resp = _signin_page()
    if pend:
        resp.delete_cookie(PENDING, path="/")
    return resp


@router.post("/console/login")
async def console_login(request: Request):
    if not _host_ok(request):
        return Response(status_code=404)
    nonce = secrets.token_urlsafe(24)
    spec = json.dumps({"type": "console-login", "nonce_sha256": _h(nonce), "ts": int(time.time()),
                       "ua": (request.headers.get("user-agent") or "")[:80]}, sort_keys=True, separators=(",", ":"))
    appr = governance.create_approval("console-login", spec, kind="console-login", requester="console",
                                      reasoning="operator console sign-in", ttl_seconds=LOGIN_TTL_S)
    resp = RedirectResponse("/console", status_code=303)
    _cookie(resp, PENDING, f"{appr['id']}.{nonce}", LOGIN_TTL_S)
    return resp


async def _csrf_session(request: Request):
    if not _host_ok(request):
        return None, None
    s = _session(request)
    form = await request.form()
    if not s or not secrets.compare_digest(str(form.get("csrf") or ""), _csrf_for(request)):
        return None, form
    return s, form


@router.post("/console/logout")
async def console_logout(request: Request):
    s, _ = await _csrf_session(request)
    if s:
        _q("UPDATE console_sessions SET revoked_at = ? WHERE id_hash = ?", (time.time(), s["id_hash"]))
    resp = RedirectResponse("/console", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.post("/console/act")
async def console_act(request: Request):
    s, f = await _csrf_session(request)
    if not s:
        if not _host_ok(request):
            return Response(status_code=404)
        return _page("Session expired", "<section class='panel' aria-labelledby='rt'><h1 id='rt' tabindex='-1' "
                     "data-focus>Session expired</h1><p class='lede'>Reload the console and try again.</p>"
                     "<a class='btn full' href='/console'>Open console</a></section>", 403)
    actor = f"{s['signer']}@console"
    a, ref = str(f.get("action") or ""), str(f.get("ref") or "")
    killing = a in ("es-kill-all", "gw-kill-all")
    if killing and f.get("confirm") != "yes":
        return _result("Not done", "<p>Tick “I mean it” to confirm.</p>")
    try:
        if a == "es-invite":
            days = str(f.get("days") or "")
            r = es.invite_issue(actor, str(f.get("email") or "") or None, str(f.get("label") or "") or None,
                                days=float(days) if days else None, max_devices=int(f.get("devices") or 3))
            return _result("Invite created", "<p>Send this link to the reader. It is shown only now.</p>"
                           + _secret_box("invite link", r["url"], 1) +
                           f"<p class='sub'>{'Lasts until ' + _when(r['expires_at']) if r['expires_at'] else 'Permanent until you revoke it'}.</p>")
        if a == "es-batch":
            days = str(f.get("days") or "")
            r = es.invite_batch(actor, str(f.get("batch") or ""), campaign=str(f.get("campaign") or "") or None,
                                days=float(days) if days else None)
            rows = "\n".join(f"{i['email']},{i['name']},{i['url']}" for i in r["issued"])
            skipped = "".join(f"<div class='sub'>skipped {_e(k['email'])}: {_e(k['why'])}</div>" for k in r["skipped"])
            per = "".join(f"<details><summary><span>{_e(i['name'] or i['email'])}</span></summary>"
                          + _secret_box(i["email"], i["url"], 10 + n) + "</details>"
                          for n, i in enumerate(r["issued"]))
            return _result(f"{len(r['issued'])} invite(s) created",
                           "<p>Shown only now. Send each reader their own link.</p>" + per
                           + (_secret_box("all links (email,name,link)", rows, 1) if r["issued"] else "") + skipped)
        if a == "es-promo":
            r = es.promo_create(actor, str(f.get("label") or ""), uses=int(f.get("uses") or 0),
                                code_days=float(f.get("code_days") or 7), access_days=int(f.get("access_days") or 7))
            return _result("Promo code created", "<p>Shown only now.</p>" + _secret_box("code", r["code"], 1)
                           + _secret_box("link", r["url"], 2) + f"<p class='sub'>Works until {_when(r['expires_at'])}.</p>")
        if a == "es-reissue":
            r = es.invite_reissue(actor, ref)
            return _result("New invite link", "<p>The old link no longer works; signed-in devices stay signed in.</p>"
                           + _secret_box("invite link", r["url"], 1))
        if a == "es-revoke":
            r = es.revoke_grant(actor, ref)
            return _result("Reader revoked", f"<p>{r['devices_signed_out']} device(s) signed out. The link is dead.</p>")
        if a == "es-signout":
            return _result("Signed out", f"<p>{es.sign_out(actor, ref)} device(s) signed out.</p>")
        if a in ("es-promo-stop", "es-promo-stop-all"):
            r = es.revoke_promo(actor, ref, readers=a.endswith("-all"))
            return _result("Promo stopped", f"<p>The code no longer works. {r['readers_ended']} reader(s) ended.</p>")
        if a == "es-freeze":
            es.freeze(actor)
            return _result("Sign-ins paused", "<p>Readers already signed in keep reading.</p>")
        if a == "es-thaw":
            es.thaw(actor)
            return _result("Sign-ins resumed", "")
        if a == "es-kill-all":
            return _result("Every reader signed out", f"<p>{es.kill_all(actor)} device(s) signed out; sign-ins paused.</p>")
        if a == "gw-revoke":
            n = 0
            for x in gw.status(ref):
                if x["status"] != "revoked":
                    gw.revoke_connection(x["id"], f"revoked by {actor}")
                    n += 1
            return _result("Agent link revoked", f"<p>{n} link(s) on /mcp/{_e(ref)} ended. Re-linking needs your signature.</p>")
        if a == "gw-hold":
            r = gw.request_hold(ref, float(f.get("days") or 7), requester="console")
            return _result("Hold requested", _approve_box(r["approval_id"], "Holding this link open"))
        if a == "gw-release":
            return _result("Hold released", f"<p>{gw.release_hold(ref)} link(s).</p>")
        if a in ("gw-disable", "gw-enable"):
            gw.connector_set_disabled(ref, a == "gw-disable")
            return _result(f"Connector {'disabled' if a == 'gw-disable' else 'enabled'}", "")
        if a == "gw-kill-all":
            return _result("All agents killed", f"<p>{gw.kill_all(actor)} link(s) revoked; every connector disabled; "
                           "gateway frozen.</p>")
        if a == "gw-thaw":
            r = gw.request_thaw(requester="console")
            return _result("Re-open requested", _approve_box(r["approval_id"], "Re-opening the agents gateway"))
    except (es.InviteError, gw.GatewayError) as e:
        return _result("Not done", f"<p>{_e(e.detail)}</p>")
    except ValueError:
        return _result("Not done", "<p>Check the numbers and try again.</p>")
    return _result("Unknown action", "")
