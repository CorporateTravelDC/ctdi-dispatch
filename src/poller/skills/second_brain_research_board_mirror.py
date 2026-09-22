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

Extra sources (2026-09-06, operator: "keep the evergreen and first-mover
content fresh" for Cowork): RESEARCH_MIRROR_EXTRA_SRCS lists further vault
folders mirrored to the same thread, default 04-Syntheses/entity-tracking.
Two differences from the primary bucket, because that folder is a 6-hourly
digest archive (43 files and counting) rather than a curated inbox:
  - only the RESEARCH_MIRROR_EXTRA_NEWEST lexically-newest files (the
    digests are named by UTC timestamp) are considered, so the first run
    does not dump the whole archive on the board and later runs post one
    item per new digest;
  - an entity-tracking digest is a list of POINTERS into
    00-Inbox/cross-link-findings/<entity>.md; Cowork cannot follow vault
    paths, so each referenced finding note is fetched, scrub-gated, and
    inlined (truncated per note) under its pointer line. One board post per
    digest carrying the actual first-mover/novel-finding content.
"""
import hashlib
import json
import logging
import os
import re
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
EXTRA_SRCS = tuple(
    p.strip().strip("/") for p in
    os.getenv("RESEARCH_MIRROR_EXTRA_SRCS", "04-Syntheses/entity-tracking").split(",")
    if p.strip())
EXTRA_NEWEST = int(os.getenv("RESEARCH_MIRROR_EXTRA_NEWEST", "2"))
STATE = os.getenv("RESEARCH_MIRROR_STATE",
                  "/var/lib/corporatetraveldc/research_board_mirror_state.json")
MAX_BODY = int(os.getenv("RESEARCH_MIRROR_MAX_BODY", "6000"))
# A digest with its ~40 finding notes inlined is legitimately large; this is
# the ceiling for that expanded form, per-note truncation keeps it honest.
MAX_BODY_EXPANDED = int(os.getenv("RESEARCH_MIRROR_MAX_BODY_EXPANDED", "80000"))
MAX_INLINE_NOTE = int(os.getenv("RESEARCH_MIRROR_MAX_INLINE_NOTE", "1200"))
# Pointer lines look like: ... -- `corporatetraveldc/00-Inbox/cross-link-findings/x.md`
_POINTER_RE = re.compile(
    r"`(" + re.escape(webdav_client.BUSINESS_ROOT) + r"/00-Inbox/cross-link-findings/[^`]+\.md)`")


def _root(src_rel: str = SRC_REL) -> str:
    return f"{webdav_client._base_url()}/{webdav_client.BUSINESS_ROOT}/{src_rel}"


def _walk(rel: str = "", src_rel: str = SRC_REL) -> list[str]:
    """Recursive Depth-1 walk of a vault folder (NC disallows Depth:
    infinity). Returns .md paths relative to that folder. Empty if the
    folder doesn't exist yet."""
    cur = rel.strip("/")
    url = f"{_root(src_rel)}/{urllib.parse.quote(cur)}".rstrip("/") + "/"
    body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
            '<d:resourcetype/></d:prop></d:propfind>')
    r = requests.request("PROPFIND", url, auth=webdav_client._auth(), data=body,
                         headers={"Host": webdav_client.HOST_HEADER, "Depth": "1",
                                  "Content-Type": "application/xml"}, timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    marker = urllib.parse.urlparse(_root(src_rel)).path.rstrip("/") + "/"
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
            out.extend(_walk(rp, src_rel))
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


def _expand_pointers(text: str) -> tuple[str, int, int]:
    """Inline the cross-link-findings notes an entity-tracking digest points
    at. Returns (expanded_text, inlined, blocked). A note that fails the
    scrub gate is replaced by a one-line marker, never partially shown."""
    inlined = blocked = 0
    seen: set[str] = set()

    def _sub(m: re.Match) -> str:
        nonlocal inlined, blocked
        path = m.group(1)
        if path in seen:
            return m.group(0)
        seen.add(path)
        note = webdav_client.get(path)
        if note is None:
            return m.group(0) + "\n  (note not found in vault)"
        body = note.decode("utf-8", "replace")
        try:
            gate(body, source=SKILL)
        except ScrubGateBlocked:
            blocked += 1
            return m.group(0) + "\n  (finding note withheld by scrub gate)"
        if len(body) > MAX_INLINE_NOTE:
            body = body[:MAX_INLINE_NOTE] + "\n  [...truncated]"
        inlined += 1
        indented = "\n".join("    " + ln for ln in body.splitlines())
        return m.group(0) + "\n" + indented
    return _POINTER_RE.sub(_sub, text), inlined, blocked


def _mirror(src_rel: str, rp: str, state: dict, *, expand: bool) -> str:
    """Mirror one vault file to the board. Returns 'posted'|'skipped'|'blocked'."""
    key = rp if src_rel == SRC_REL else f"{src_rel}/{rp}"  # legacy keys are bare
    content = webdav_client.get(f"{webdav_client.BUSINESS_ROOT}/{src_rel}/{rp}")
    if content is None:
        return "skipped"
    h = hashlib.sha256(content).hexdigest()
    if state.get(key) == h:
        return "skipped"
    text = content.decode("utf-8", "replace")
    try:
        gate(text, source=SKILL)  # Tier-0 surface -- CUI/PII block gate
    except ScrubGateBlocked as e:
        log.error("%s: %s/%s BLOCKED by scrub gate, NOT mirrored: %s", SKILL, src_rel, rp, e)
        return "blocked"
    label = src_rel.rsplit("/", 1)[-1] if src_rel != SRC_REL else "personal-research"
    max_body = MAX_BODY
    if expand:
        text, n_in, n_blk = _expand_pointers(text)
        max_body = MAX_BODY_EXPANDED
        log.info("%s: %s/%s -- inlined %d finding notes, %d withheld", SKILL, src_rel, rp, n_in, n_blk)
    title = rp.rsplit("/", 1)[-1]
    body = f"[auto-mirrored from vault {src_rel}: {rp}]\n\n{text}"
    if len(body) > max_body:
        body = body[:max_body] + f"\n\n[...truncated; full copy in vault {src_rel}/{rp}]"
    db.board_insert("dispatch", "cowork", "research", f"{label}: {title}", body)
    state[key] = h
    log.info("%s: mirrored %s/%s -> board research thread", SKILL, src_rel, rp)
    return "posted"


def main() -> None:
    state = _load_state()
    counts = {"posted": 0, "skipped": 0, "blocked": 0}
    total = 0
    # (src_rel, items, expand-pointers?)
    jobs: list[tuple[str, list[str], bool]] = []
    try:
        jobs.append((SRC_REL, _walk("", SRC_REL), False))
    except Exception as e:
        log.warning("%s: cannot list %s (%s) -- skipping it", SKILL, SRC_REL, e)
    for extra in EXTRA_SRCS:
        try:
            items = sorted(_walk("", extra))[-EXTRA_NEWEST:] if EXTRA_NEWEST > 0 else []
        except Exception as e:
            log.warning("%s: cannot list %s (%s) -- skipping it", SKILL, extra, e)
            continue
        jobs.append((extra, items, extra.endswith("entity-tracking")))
    for src_rel, items, expand in jobs:
        total += len(items)
        for rp in items:
            counts[_mirror(src_rel, rp, state, expand=expand)] += 1
    _save_state(state)
    log.info("%s: %d mirrored, %d unchanged, %d blocked (of %d items)",
             SKILL, counts["posted"], counts["skipped"], counts["blocked"], total)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
