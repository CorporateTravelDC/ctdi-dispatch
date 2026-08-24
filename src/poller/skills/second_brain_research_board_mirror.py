#!/usr/bin/env python3
"""
second_brain_research_board_mirror -- mirror vault personal-research items onto
the coordination board's `research` thread so Cowork (Tier-0, no vault/tailnet
access) can actually reach them. This is the discoverable-surface half of the
two-bucket design: the Notes-import valve routes Research-category notes to the
vault's personal-research bucket; this skill surfaces them to Cowork.

ONE-WAY: reads the corporatetraveldc vault, writes the board; never the reverse,
never deletes. Idempotent via a source-content-hash state file, so re-runs (and
the inbox processor relocating an item) don't repost. Every mirrored body passes
the CUI/PII scrub gate first -- the board is a Tier-0 surface -- and is truncated
to a sane length with a pointer back to the vault copy.

Source: 00-Inbox/personal-research/ (incl. subfolders). Sink: db.board_insert
(from=dispatch, to=cowork, thread=research) -- internal DB path, no HTTP/auth.
No Anthropic call, so no SR-1/SR-2 (same as the RSS/notes skills).
"""
import hashlib
import json
import logging
import os
import urllib.parse
from xml.etree import ElementTree as ET

import requests

from common import db
from second_brain import webdav_client
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL = "second-brain-research-board-mirror"
_DAV = "{DAV:}"
SRC_REL = os.getenv("RESEARCH_MIRROR_SRC", "00-Inbox/personal-research").strip("/")
STATE = os.getenv("RESEARCH_MIRROR_STATE",
                  "/var/lib/corporatetraveldc/research_board_mirror_state.json")
MAX_BODY = int(os.getenv("RESEARCH_MIRROR_MAX_BODY", "6000"))


def _root() -> str:
    return f"{webdav_client._base_url()}/{webdav_client.BUSINESS_ROOT}/{SRC_REL}"


def _walk(rel: str = "") -> list[str]:
    """Recursive Depth-1 walk of the personal-research bucket (NC disallows
    Depth: infinity). Returns .md paths relative to the bucket root. Empty if
    the bucket doesn't exist yet."""
    cur = rel.strip("/")
    url = f"{_root()}/{urllib.parse.quote(cur)}".rstrip("/") + "/"
    body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
            '<d:resourcetype/></d:prop></d:propfind>')
    r = requests.request("PROPFIND", url, auth=webdav_client._auth(), data=body,
                         headers={"Host": webdav_client.HOST_HEADER, "Depth": "1",
                                  "Content-Type": "application/xml"}, timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    marker = urllib.parse.urlparse(_root()).path.rstrip("/") + "/"
    out: list[str] = []
    for resp in ET.fromstring(r.content).findall(f"{_DAV}response"):
        href_el = resp.find(f"{_DAV}href")
        if href_el is None or not href_el.text:
            continue
        href = urllib.parse.unquote(href_el.text)
        if marker not in href:
            continue
        rp = href.split(marker, 1)[1].strip("/")
        if not rp or rp == cur:
            continue
        rt = resp.find(f"{_DAV}propstat/{_DAV}prop/{_DAV}resourcetype")
        is_dir = rt is not None and rt.find(f"{_DAV}collection") is not None
        if is_dir:
            out.extend(_walk(rp))
        elif rp.lower().endswith(".md"):
            out.append(rp)
    return out


def _load_state() -> dict:
    try:
        with open(STATE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(s: dict) -> None:
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, sort_keys=True)
    os.replace(tmp, STATE)


def main() -> None:
    state = _load_state()
    posted = skipped = blocked = 0
    try:
        items = _walk("")
    except Exception as e:
        log.warning("%s: cannot list personal-research (%s) -- no-op", SKILL, e)
        return
    for rp in items:
        content = webdav_client.get(f"{webdav_client.BUSINESS_ROOT}/{SRC_REL}/{rp}")
        if content is None:
            continue
        h = hashlib.sha256(content).hexdigest()
        if state.get(rp) == h:
            skipped += 1
            continue
        text = content.decode("utf-8", "replace")
        try:
            gate(text, source=SKILL)  # Tier-0 surface -- CUI/PII block gate
        except ScrubGateBlocked as e:
            log.error("%s: %s BLOCKED by scrub gate, NOT mirrored: %s", SKILL, rp, e)
            blocked += 1
            continue
        title = rp.rsplit("/", 1)[-1]
        body = f"[auto-mirrored from vault personal-research: {rp}]\n\n{text}"
        if len(body) > MAX_BODY:
            body = body[:MAX_BODY] + (
                f"\n\n[...truncated; full copy in vault 00-Inbox/personal-research/{rp}]")
        db.board_insert("dispatch", "cowork", "research",
                        f"personal-research: {title}", body)
        state[rp] = h
        posted += 1
        log.info("%s: mirrored %s -> board research thread", SKILL, rp)
    _save_state(state)
    log.info("%s: %d mirrored, %d unchanged, %d blocked (of %d items)",
             SKILL, posted, skipped, blocked, len(items))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
