"""web.routes.agent_gateway -- OAuth 2.1 authorization server + remote MCP
endpoint for cloud agents (2026-10-05). Logic: common/agent_gateway.py.

Served on its own public hostname (GATEWAY_BASE_URL, nginx vhost
agents.example.com) because the dispatch hostname's Cloudflare
path strips the Authorization header OAuth/MCP clients must send.

  /.well-known/oauth-authorization-server   RFC 8414 metadata
  /.well-known/oauth-protected-resource[/mcp/<slug>]   RFC 9728 metadata
  POST /oauth/register      Dynamic Client Registration (public clients, PKCE)
  GET  /oauth/authorize     consent page: shows the approval id the operator signs
  GET  /oauth/authorize/status?req=   polled by that page; redirects once signed
  POST /oauth/token         authorization_code (PKCE S256) | refresh_token
  POST /oauth/revoke        RFC 7009
  POST /mcp/<slug>          MCP streamable HTTP (JSON responses), Bearer access token

Tools reach exactly what a board-token agent reaches today -- board read/post,
the vault research scope, workspace contributions, council requests -- and
are attributed to the connector's account.
"""
from __future__ import annotations

import html
import json
import os
import posixpath
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from common import agent_gateway as gw
from common import db, governance

router = APIRouter()
BASE = os.environ.get("GATEWAY_BASE_URL", "https://agents.example.com").rstrip("/")
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")


def _oauth_err(e: gw.GatewayError) -> JSONResponse:
    return JSONResponse({"error": e.error, "error_description": e.detail}, status_code=e.status)


@router.get("/.well-known/oauth-authorization-server")
async def as_metadata() -> JSONResponse:
    return JSONResponse({
        "issuer": BASE,
        "authorization_endpoint": f"{BASE}/oauth/authorize",
        "token_endpoint": f"{BASE}/oauth/token",
        "registration_endpoint": f"{BASE}/oauth/register",
        "revocation_endpoint": f"{BASE}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["dispatch", "offline_access"],
        "client_id_metadata_document_supported": True,
    })


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp/{slug}")
async def pr_metadata(slug: str | None = None) -> JSONResponse:
    return JSONResponse({"resource": f"{BASE}/mcp/{slug}" if slug else f"{BASE}/mcp",
                         "authorization_servers": [BASE], "scopes_supported": ["dispatch", "offline_access"],
                         "bearer_methods_supported": ["header"]})


@router.post("/oauth/register", status_code=201)
async def oauth_register(request: Request) -> JSONResponse:
    try:
        meta = json.loads((await request.body())[:65536] or b"{}")
        return JSONResponse(gw.register_client(meta), status_code=201)
    except gw.GatewayError as e:
        return _oauth_err(e)
    except ValueError:
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)


def _slug_from_resource(resource: str | None) -> str | None:
    if resource and resource.startswith(f"{BASE}/mcp/"):
        return resource[len(f"{BASE}/mcp/"):].strip("/") or None
    return None


_PAGE = """<!doctype html><meta name=viewport content="width=device-width"><title>Link {name}</title>
<body style="font:16px system-ui;max-width:34rem;margin:2rem auto;padding:0 1rem">
<h2>Link {client} to {account}</h2>
<p>This link is approved only by the operator's SSH signature. On the dispatch Pi run:</p>
<pre style="background:#eee;padding:.6rem;overflow-x:auto">scripts/approve.sh allow {aid}</pre>
<p id=s role=status aria-live=polite>Waiting for the signature... (expires in 15 minutes)</p>
<div id=cb hidden><p><strong>Signed.</strong> Copy this address and paste it at the prompt in your terminal
(the command-line sign-in is waiting for it):</p>
<pre id=u style="background:#eee;padding:.6rem;white-space:pre-wrap;word-break:break-all"></pre>
<button id=c type=button style="font:inherit;padding:.5rem 1rem">Copy</button></div>
<script>
// 2026-10-05: a loopback callback (Claude Code CLI, RFC 8252) lives on the machine running the CLI -- often
// not this one (claude mcp login --no-browser over SSH). Navigating there just shows "can't be reached", so
// show the address to paste instead. https callbacks (claude.ai, ChatGPT) still redirect as before.
function loopback(u){{return /^http:\\/\\/(localhost|127\\.0\\.0\\.1|\\[::1\\])(:|\\/)/.test(u)}}
async function poll(){{const r=await fetch('/oauth/authorize/status?req={req}');const j=await r.json();
if(j.state==='redirect'&&loopback(j.location)){{document.getElementById('s').textContent='Signed. Code ready below (valid 10 minutes).';
document.getElementById('u').textContent=j.location;document.getElementById('cb').hidden=false;
document.getElementById('c').onclick=async()=>{{try{{await navigator.clipboard.writeText(j.location);
document.getElementById('c').textContent='Copied'}}catch(_){{}}}};return}}
if(j.state==='redirect'){{location.href=j.location;return}}
if(j.state==='done'){{document.getElementById('s').textContent='Already completed.';return}}
setTimeout(poll,3000)}}poll();
</script>"""


@router.get("/oauth/authorize")
async def oauth_authorize(request: Request):
    q = request.query_params
    slug = _slug_from_resource(q.get("resource")) or q.get("connector")
    if q.get("response_type") != "code" or not slug:
        return JSONResponse({"error": "invalid_request",
                             "error_description": "response_type=code and resource=<gateway>/mcp/<connector> required"},
                            status_code=400)
    try:
        r = gw.authorize_start(slug, q.get("client_id", ""), q.get("redirect_uri", ""), q.get("code_challenge", ""),
                               q.get("code_challenge_method", ""), q.get("state"), q.get("scope"))
    except gw.GatewayError as e:
        return _oauth_err(e)
    try:
        from common import ntfy_push
        ntfy_push.send("approval-gate", f"{r['client_name']} wants to link as {r['account']}.\n"
                       f"Approve over SSH: scripts/approve.sh allow {r['approval_id']}",
                       title="Agent connector link", priority=3, tags="link")
    except Exception:  # noqa: BLE001
        pass
    return HTMLResponse(_PAGE.format(name=html.escape(r["account"]), client=html.escape(str(r["client_name"])),
                                     account=html.escape(r["account"]), aid=html.escape(r["approval_id"]),
                                     req=html.escape(r["req_id"])))


@router.get("/oauth/authorize/status")
async def oauth_authorize_status(req: str) -> JSONResponse:
    try:
        return JSONResponse(gw.authorize_status(req))
    except gw.GatewayError as e:
        return _oauth_err(e)


@router.post("/oauth/token")
async def oauth_token(request: Request) -> JSONResponse:
    form = await request.form()
    try:
        if form.get("grant_type") == "authorization_code":
            out = gw.exchange_code(form.get("code", ""), form.get("client_id", ""), form.get("redirect_uri", ""),
                                   form.get("code_verifier", ""))
        elif form.get("grant_type") == "refresh_token":
            out = gw.refresh(form.get("refresh_token", ""), form.get("client_id", ""))
        else:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    except gw.GatewayError as e:
        return _oauth_err(e)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.post("/oauth/revoke")
async def oauth_revoke(request: Request) -> Response:
    form = await request.form()
    tok = form.get("token", "")
    row = gw._q("SELECT connection_id FROM oauth_tokens WHERE token_hash = ?", (gw._h(tok),), one=True)
    if row:
        gw.revoke_connection(row["connection_id"], "revoked by the client")
    return Response(status_code=200)


# -- MCP ------------------------------------------------------------------------------------------

TOOLS = [
    {"name": "status", "description": "Your identity and connection state on the dispatch platform.",
     "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
    {"name": "board_read", "description": "Read a coordination-board thread (coord, research, council, ...).",
     "inputSchema": {"type": "object", "properties": {"thread": {"type": "string"}, "since": {"type": "string"},
                                                       "limit": {"type": "integer"}}, "required": ["thread"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "board_post", "description": "Post to the coordination board as yourself.",
     "inputSchema": {"type": "object", "properties": {"to": {"type": "string"}, "thread": {"type": "string"},
                                                       "subject": {"type": "string"}, "body": {"type": "string"}},
                     "required": ["to", "thread", "subject", "body"]}},
    {"name": "research_list", "description": "List files in the second-brain research scope (Series/, 04-Syntheses/, ...).",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}, "annotations": {"readOnlyHint": True}},
    {"name": "research_read", "description": "Read one file in the second-brain research scope.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "workspace_contribute", "description": "Add a draft to the shared workspace (create-only, attributed; you never publish).",
     "inputSchema": {"type": "object", "properties": {"task": {"type": "string"}, "title": {"type": "string"},
                                                       "content": {"type": "string"}}, "required": ["content"]}},
    {"name": "council_request", "description": "Request a council/arena convene; nothing happens until a human signs it.",
     "inputSchema": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["council", "arena"]},
                                                       "subject": {"type": "string"}, "brief": {"type": "string"},
                                                       "participants": {"type": "array", "items": {"type": "object"}},
                                                       "deadline_hours": {"type": "number"}},
                     "required": ["mode", "subject", "participants"]}},
]


def _text(obj) -> dict:
    return {"content": [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, indent=1, default=str)}]}


def _research_path_ok(path: str, account: str) -> str:
    from web import main as wm
    if not wm._vault_path_is_safe(path):
        raise ValueError("invalid path")
    root = wm._VAULT_RESEARCH_ROOT
    if not (path == root or wm._vault_research_path_allowed(path)
            or any(path == p.rstrip("/") for p in wm._VAULT_RESEARCH_EXTRA_PREFIXES)):
        raise ValueError("path is outside the research scope")
    ok, why = governance.arena_read_allowed(wm._vault_path_decoded(path), account)
    if not ok:
        raise ValueError(why)
    return path


def _call(name: str, args: dict, cx: dict):
    acct = cx["account"]
    if name == "status":
        return {"account": acct, "connector": cx["slug"], "connection": cx["id"], "state": "active"}
    if name == "board_read":
        msgs, cursor = db.board_query(thread=str(args.get("thread", "coord")), since=args.get("since"),
                                      limit=int(args.get("limit") or 50))
        return {"messages": msgs, "cursor": cursor}
    if name == "board_post":
        from web import main as wm
        text = "\n".join([str(args.get("subject", "")), str(args.get("body", ""))])
        wm._scrub_gate(text, source="agent-gateway-board-post")
        return db.board_insert(acct, str(args["to"])[:64], str(args["thread"])[:64],
                               str(args["subject"])[:300], str(args["body"])[:20000])
    if name in ("research_list", "research_read"):
        from second_brain import webdav_client
        from web import main as wm
        path = _research_path_ok(str(args.get("path") or wm._VAULT_RESEARCH_ROOT), acct)
        if name == "research_list":
            pre = f"{webdav_client.BUSINESS_ROOT}/"
            files = webdav_client.list_files(pre + path)
            return {"path": path, "files": [{**f, "path": f["path"][len(pre):] if f["path"].startswith(pre) else f["path"]}
                                            for f in files]}
        content = webdav_client.get(f"{webdav_client.BUSINESS_ROOT}/{path}")
        if content is None:
            raise ValueError("not found")
        text = content.decode("utf-8", "replace")
        wm._scrub_gate(text, source="agent-gateway-research-read")
        return text
    if name == "workspace_contribute":
        from second_brain import webdav_client
        from web import main as wm
        task = str(args.get("task") or "general")
        governance.check_contribution(acct, task, str(args.get("content", "")))
        wm._scrub_gate("\n".join([str(args.get("title", "")), str(args["content"])]), source="agent-gateway-contribute")
        path = governance.contribution_path(acct, task, str(args.get("title") or task))
        doc = governance.render_contribution(acct, task, str(args.get("title", "")), str(args["content"]),
                                             f"agent-gateway:{cx['id']}")
        webdav_client.put_create_only(f"{webdav_client.BUSINESS_ROOT}/{path}", doc)
        return {"path": path}
    if name == "council_request":
        return governance.council_create(acct, str(args["mode"]), str(args["subject"]), str(args.get("brief", "")),
                                         args.get("participants") or [], float(args.get("deadline_hours") or 48))
    raise KeyError(name)


def _rpc_result(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _rpc_error(mid, code: int, msg: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": msg}}


@router.get("/mcp/{slug}")
async def mcp_get(slug: str) -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp/{slug}")
async def mcp_post(slug: str, request: Request):
    auth = request.headers.get("authorization", "")
    tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    try:
        cx = gw.authenticate(tok, slug)
    except gw.GatewayError as e:
        hdr = {"WWW-Authenticate": f'Bearer resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp/{slug}"'}
        return JSONResponse({"error": e.error, "error_description": e.detail}, status_code=e.status, headers=hdr)
    try:
        msg = json.loads((await request.body())[:1_000_000] or b"{}")
    except ValueError:
        return JSONResponse(_rpc_error(None, -32700, "parse error"), status_code=400)
    if isinstance(msg, list):
        return JSONResponse(_rpc_error(None, -32600, "batching not supported"), status_code=400)
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:                               # notification (e.g. notifications/initialized)
        return Response(status_code=202)
    if method == "initialize":
        want = params.get("protocolVersion")
        return JSONResponse(_rpc_result(mid, {
            "protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "ctdi-dispatch-agent-gateway", "version": "1"},
            "instructions": (f"You are {cx['account']} on the CTDI dispatch platform. Read your pamphlet first: "
                             f"research_read 01-Sources/personal-notes/Series/agents/{cx['account']}/PAMPHLET.md. "
                             "You draft; you never publish; you never approve anything.")}))
    if method == "ping":
        return JSONResponse(_rpc_result(mid, {}))
    if method == "tools/list":
        return JSONResponse(_rpc_result(mid, {"tools": TOOLS}))
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        try:
            out = _call(name, args, cx)
            return JSONResponse(_rpc_result(mid, _text(out)))
        except KeyError:
            return JSONResponse(_rpc_error(mid, -32602, f"unknown tool {name}"))
        except (ValueError, governance.GovernanceError) as e:
            detail = getattr(e, "detail", str(e))
            return JSONResponse(_rpc_result(mid, {**_text(f"refused: {detail}"), "isError": True}))
        except Exception as e:  # noqa: BLE001 -- e.g. the scrub gate, WebDAV
            return JSONResponse(_rpc_result(mid, {**_text(f"failed: {type(e).__name__}: {str(e)[:200]}"), "isError": True}))
    return JSONResponse(_rpc_error(mid, -32601, f"method not found: {method}"))
