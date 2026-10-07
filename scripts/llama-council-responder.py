#!/usr/bin/env python3
"""scripts/llama-council-responder.py -- the local llama model as a council /
arena participant, running AS the service account ctdc-agent-llama
(corporatetraveldc-llama-council.{service,timer}, hourly).

Operator, 2026-10-04: "make sure that guardrail is in all of the agents,
including the local llama server, such that I can interchangeably use any of
those for the ghostwriting." So llama takes part the same way every other
account does: it reads the convene on the API, drafts with the one local model
server, and contributes signed with ITS OWN key -- attributed, create-only,
never published. It holds no vault, admin or ntfy credential.

Per run: at most ONE active convene that names ctdc-agent-llama and has no
contribution from it yet. Skips entirely when load1 >= LOAD_MAX (the box sheds
work above ~12) or the model server is unreachable. Dry run: --dry-run prints
what it would contribute and posts nothing.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from common import board_sign  # noqa: E402

ME = os.environ.get("LLAMA_ACCOUNT", "ctdc-agent-llama")
API = os.environ.get("BOARD_API_BASE", "http://127.0.0.1:8000")
LLAMA = os.environ.get("LLAMA_SERVER_URL", "http://100.x.x.x:8093")
KEY = os.environ.get("BOARD_SIGN_KEY", str(Path.home() / ".ssh" / f"{ME}_ed25519"))
LOAD_MAX = float(os.environ.get("LLAMA_COUNCIL_LOAD_MAX", "12"))
MAX_TOKENS = int(os.environ.get("LLAMA_COUNCIL_MAX_TOKENS", "900"))
CONTRIB_ROOT = "01-Sources/personal-notes/Series/contributions"


def signed(method: str, path_q: str, body: bytes = b"") -> tuple[int, dict]:
    path, _, query = path_q.partition("?")
    ts = int(time.time())
    sig = board_sign.sign_with_ssh_keygen(KEY, board_sign.canonical_message(method, path, ts, body, query))
    req = urllib.request.Request(API + path_q, data=body or None, method=method, headers={
        "X-Board-Signer": ME, "X-Board-Timestamp": str(ts), "X-Board-Signature": sig,
        **({"Content-Type": "application/json"} if body else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def draft(mode: str, subject: str, brief: str, source: str) -> str | None:
    stance = ("Work independently and adversarially: find what is wrong, unsupported or overstated."
              if mode == "arena" else
              "Work collaboratively: add evidence, gaps and concrete improvements.")
    system = (f"You are {ME}, the local model on a team of drafting agents. You draft; you never publish. "
              f"{stance} Be specific, cite the passage you refer to, and say plainly when you are unsure. "
              "Never include credentials or personal data.")
    user = f"Subject: {subject}\n\nBrief:\n{brief or '(none)'}\n\nSource material:\n{source[:12000] or '(none provided)'}"
    body = json.dumps({"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                       "max_tokens": MAX_TOKENS, "temperature": 0.3}).encode()
    req = urllib.request.Request(LLAMA + "/v1/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            out = json.loads(r.read())
        return (out["choices"][0]["message"]["content"] or "").strip() or None
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as e:
        print(f"llama server: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def main() -> int:
    dry = "--dry-run" in sys.argv
    load1 = os.getloadavg()[0]
    if load1 >= LOAD_MAX:
        print(f"load1 {load1:.1f} >= {LOAD_MAX}: skipping this hour")
        return 0
    code, data = signed("GET", "/api/v1/council?status=active")
    if code != 200:
        print(f"council list: HTTP {code} {data.get('detail', '')}", file=sys.stderr)
        return 1
    for cs in data.get("councils", []):
        if ME not in {p["account"] for p in cs["participants"]}:
            continue
        mine = f"{CONTRIB_ROOT}/council-{cs['id']}/{ME}"
        code, lst = signed("GET", "/api/v1/vault/research/list?path=" + urllib.parse.quote(mine, safe="/"))
        if code == 200 and lst.get("files"):
            continue
        source = ""
        if cs["subject"].startswith("01-Sources/") or cs["subject"].startswith("04-Syntheses/") or "/" in cs["subject"]:
            c2, doc = signed("GET", "/api/v1/vault/research?path=" + urllib.parse.quote(cs["subject"], safe="/"))
            source = doc.get("content", "") if c2 == 200 else ""
        text = draft(cs["mode"], cs["subject"], cs.get("brief", ""), source)
        if not text:
            return 1
        if dry:
            print(f"[dry-run] would contribute to council-{cs['id']}:\n{text[:2000]}")
            return 0
        body = json.dumps({"task": f"council-{cs['id']}", "title": f"{ME} {cs['mode']} pass",
                           "content": text}).encode()
        code, out = signed("POST", "/api/v1/workspace/contribute", body)
        print(f"contribute council-{cs['id']}: HTTP {code} {out.get('path') or out.get('detail', '')}")
        return 0 if code == 201 else 1
    print("no active convene waiting on " + ME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
