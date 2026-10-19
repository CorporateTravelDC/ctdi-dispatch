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

2026-10-19 (lean-out L2, F7): the draft now holds the box-wide long-runner
generation lock (/var/lib/corporatetraveldc/llama-pool/long-runner.lock, the
same flock common/llama_pool.long_runner_lock() takes for every report
persona) instead of posting straight to the server beside a running report.
The wait is bounded (LLAMA_COUNCIL_LOCK_WAIT_S, default 120 s): a busy lane
means skip this hour, never queue into the unit's 20-min start timeout. A
failed draft is recorded by a hash of its inputs (mode, subject, brief,
source) in the unit's StateDirectory; the identical draft is not retried for
LLAMA_COUNCIL_RETRY_AFTER_S (default 24 h) -- it used to be retried, and fail
the same way, every hour. A changed brief or source retries at once.
"""
from __future__ import annotations

import fcntl
import hashlib
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
LOCK_PATH = os.environ.get("LLAMA_LONG_RUNNER_LOCK", "/var/lib/corporatetraveldc/llama-pool/long-runner.lock")
LOCK_WAIT_S = float(os.environ.get("LLAMA_COUNCIL_LOCK_WAIT_S", "120"))
RETRY_AFTER_S = float(os.environ.get("LLAMA_COUNCIL_RETRY_AFTER_S", "86400"))
STATE_DIR = Path(os.environ.get("STATE_DIRECTORY") or (Path.home() / ".cache" / "llama-council"))
FAILED_FILE = STATE_DIR / "failed-drafts.json"


def draft_key(mode: str, subject: str, brief: str, source: str) -> str:
    """Content key of a draft's INPUTS (never a timestamp)."""
    return hashlib.sha256(json.dumps([mode, subject, brief or "", source or ""]).encode()).hexdigest()


def _load_failed() -> dict:
    try:
        return json.loads(FAILED_FILE.read_text())
    except Exception:
        return {}


def recently_failed(key: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    at = _load_failed().get(key)
    return at is not None and 0 <= now - float(at) < RETRY_AFTER_S


def record_failure(key: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    failed = {k: v for k, v in _load_failed().items() if now - float(v) < RETRY_AFTER_S}
    failed[key] = now
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = FAILED_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(failed))
        os.replace(tmp, FAILED_FILE)
    except OSError as e:
        print(f"could not record the failed draft: {e}", file=sys.stderr)


def acquire_long_runner_lock(wait_s: float = LOCK_WAIT_S):
    """Return an fd holding the shared long-runner flock, or None if it stayed
    busy for wait_s (or cannot be opened). Read-only open: flock needs no write
    access, and this account only reads /var/lib/corporatetraveldc."""
    try:
        fd = os.open(LOCK_PATH, os.O_RDONLY)
    except OSError as e:
        print(f"long-runner lock unavailable ({type(e).__name__}): skipping this hour", file=sys.stderr)
        return None
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(2.0)


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
        key = draft_key(cs["mode"], cs["subject"], cs.get("brief", ""), source)
        if recently_failed(key):
            print(f"council-{cs['id']}: the identical draft failed within {RETRY_AFTER_S / 3600:.0f} h -- "
                  "not retrying until its brief or source changes")
            continue
        fd = acquire_long_runner_lock()
        if fd is None:
            print(f"long-runner lane busy for {LOCK_WAIT_S:.0f}s: skipping this hour")
            return 0
        try:
            text = draft(cs["mode"], cs["subject"], cs.get("brief", ""), source)
        finally:
            os.close(fd)
        if not text:
            if not dry:
                record_failure(key)
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
