#!/usr/bin/env python3
"""
second_brain_personal_notes_import -- ONE-WAY valve: copy the operator's PERSONAL
Nextcloud Notes into the corporatetraveldc business vault.

Direction is enforced structurally: this skill only ever READS the personal
("operator") account and only ever WRITES the corporatetraveldc vault. There is no
code path from vault -> personal, and it never deletes anything on either side.
Originals in the operator's Notes are left untouched (he keeps his working copy); a
deleted/renamed source note does NOT propagate a deletion into the vault.

Two-bucket routing by Notes CATEGORY (a Notes category == a top-level subfolder
under Notes/), so the operator chooses the bucket purely by where he files the note --
no extra tooling on his end:

    Notes/Research*/... , Notes/Series*/...  ->  vault 00-Inbox/personal-research/<category>/  (AI-processed, inbox)
    everything else under Notes/             ->  vault 01-Sources/personal-notes/              (stable, untouched)

(Category PREFIX match, e.g. "Research - Uber Series" -- see RESEARCH_PREFIXES.)

Idempotency uses a SOURCE-content-hash state file (not the vault copy), so the
inbox processor relocating/summarising a personal-research note never triggers a
re-import loop: we re-import only when the *source* note's content actually
changes.

Auth: the read side uses the operator's OWN app-password (PERSONAL_NOTES_SRC_APP_PASSWORD
in dispatch-secrets.env, which the operator mints and drops in himself -- never printed
through the agent). The write side reuses the shared second_brain.webdav_client
in its normal corporatetraveldc account context. Both reach Nextcloud through the
internal nginx ingress (host.containers.internal:80 + Host-header spoofing) -- the
read side spoofs the dav. host (cloud. is vault-only and won't serve operator).

No Anthropic API call is made here (pure file copy), so SR-1/SR-2 don't apply;
the CUI/PII scrub gate DOES (same non-negotiable as every other ingest path).
"""
import hashlib
import json
import logging
import os
import urllib.parse
from xml.etree import ElementTree as ET

import requests

from second_brain import webdav_client
from second_brain.index_db import get_conn, index_note
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "second-brain-personal-notes-import"
_DAV = "{DAV:}"

SRC_USER = os.getenv("PERSONAL_NOTES_SRC_USER", "operator")
SRC_PATH = os.getenv("PERSONAL_NOTES_SRC_PATH", "Notes").strip("/")
# Reach Nextcloud through the same internal nginx ingress the write side uses,
# but with the dav. Host header -- cloud. is the vault-only automation endpoint
# and (by design) 404s operator's paths, so reads must go via dav.
SRC_BASE = os.getenv(
    "PERSONAL_NOTES_SRC_WEBDAV_BASE",
    "http://host.containers.internal:80/remote.php/dav/files",
).rstrip("/")
SRC_HOST = os.getenv("PERSONAL_NOTES_SRC_HOST", "dav.example.com")
SRC_PW = os.getenv("PERSONAL_NOTES_SRC_APP_PASSWORD", "").strip()

# Comma-separated category PREFIXES (2026-09-06). the operator files research as
# "Research - Uber Series" / "Series-<topic>" -- never a bare "Research"
# folder -- and the old exact-name match routed every one of those to the
# stable personal-notes bucket instead. Result: 00-Inbox/personal-research/
# never existed and the research board mirror (Cowork's ONLY view of this
# material) reported "0 items" on every run since 2026-08-24. A category
# matches when it equals a prefix or starts with it followed by a
# non-alphanumeric ("Research - X", "Research-X", "Series_X"); "Uber Series"
# does NOT match -- prefix only, by design, so an ordinary note whose name
# happens to contain the word stays in personal-notes.
RESEARCH_CATEGORY = os.getenv("PERSONAL_NOTES_RESEARCH_CATEGORY", "Research,Series")
RESEARCH_PREFIXES = tuple(
    p.strip().strip("/").lower() for p in RESEARCH_CATEGORY.split(",") if p.strip())
DEST_NOTES = os.getenv("PERSONAL_NOTES_DEST", "01-Sources/personal-notes").strip("/")
DEST_RESEARCH = os.getenv("PERSONAL_RESEARCH_DEST", "00-Inbox/personal-research").strip("/")
STATE_FILE = os.getenv(
    "PERSONAL_NOTES_STATE_FILE",
    "/var/lib/corporatetraveldc/personal_notes_import_state.json",
)


def _src_auth() -> tuple[str, str]:
    return (SRC_USER, SRC_PW)


def _src_root_url() -> str:
    return f"{SRC_BASE}/{SRC_USER}/{SRC_PATH}"


def _quote(rel: str) -> str:
    return urllib.parse.quote(rel, safe="/")


def _propfind(url: str, depth: str = "1") -> ET.Element:
    # getetag (lean-out L2 / F12, 2026-10-19): Nextcloud changes a file's ETag
    # when its content changes and propagates a change up to every parent
    # collection, so the Notes/ root ETag says whether anything below changed.
    body = (
        '<?xml version="1.0"?>'
        '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getetag/></d:prop></d:propfind>'
    )
    r = requests.request(
        "PROPFIND", url, auth=_src_auth(), data=body,
        headers={"Host": SRC_HOST, "Depth": depth, "Content-Type": "application/xml"},
        timeout=30,
    )
    r.raise_for_status()
    return ET.fromstring(r.content)


def _etag(resp_el: ET.Element) -> str:
    el = resp_el.find(f"{_DAV}propstat/{_DAV}prop/{_DAV}getetag")
    return (el.text or "").strip() if el is not None else ""


def _root_etag() -> str:
    """ETag of the Notes/ root collection (one Depth-0 PROPFIND); "" if absent."""
    url = _src_root_url().rstrip("/") + "/"
    for resp in _propfind(url, depth="0").findall(f"{_DAV}response"):
        return _etag(resp)
    return ""


def _config_fingerprint() -> str:
    """Routing inputs: a change here must re-walk even when no note changed."""
    raw = json.dumps([SRC_USER, SRC_PATH, list(RESEARCH_PREFIXES), DEST_NOTES,
                      DEST_RESEARCH, webdav_client.BUSINESS_ROOT])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _is_collection(resp_el: ET.Element) -> bool:
    rt = resp_el.find(f"{_DAV}propstat/{_DAV}prop/{_DAV}resourcetype")
    return rt is not None and rt.find(f"{_DAV}collection") is not None


def _walk(rel: str = "", etags: dict | None = None) -> list[str]:
    """Recursive Depth-1 walk of operator's Notes/ (Nextcloud disallows Depth:
    infinity). Return .md paths relative to the Notes root; when `etags` is
    given, each file's ETag is recorded in it (relpath -> etag)."""
    cur = rel.strip("/")
    url = f"{_src_root_url()}/{_quote(cur)}".rstrip("/") + "/"
    marker = urllib.parse.urlparse(_src_root_url()).path.rstrip("/") + "/"
    files: list[str] = []
    for resp in _propfind(url).findall(f"{_DAV}response"):
        href_el = resp.find(f"{_DAV}href")
        if href_el is None or not href_el.text:
            continue
        href = urllib.parse.unquote(href_el.text)
        if marker not in href:
            continue
        relpath = href.split(marker, 1)[1].strip("/")
        if not relpath or relpath == cur:
            continue  # the collection being listed (itself)
        if _is_collection(resp):
            files.extend(_walk(relpath, etags))
        elif relpath.lower().endswith(".md"):
            files.append(relpath)
            if etags is not None:
                etags[relpath] = _etag(resp)
    return files


def _src_get(relpath: str) -> bytes:
    url = f"{_src_root_url()}/{_quote(relpath)}"
    r = requests.get(url, auth=_src_auth(), headers={"Host": SRC_HOST}, timeout=30)
    r.raise_for_status()
    return r.content


def _is_research_category(top: str) -> bool:
    t = top.lower()
    for p in RESEARCH_PREFIXES:
        if t == p or (t.startswith(p) and not t[len(p)].isalnum()):
            return True
    return False


def _route(relpath: str) -> tuple[str, str]:
    """Map a source relpath to (vault rel_path, bucket). Research categories
    keep their full category name as the subfolder ("Research - Uber Series/
    note.md"), so distinct research topics stay distinct in the inbox."""
    top = relpath.split("/", 1)[0]
    if "/" in relpath and _is_research_category(top):
        return f"{webdav_client.BUSINESS_ROOT}/{DEST_RESEARCH}/{relpath}", "personal-research"
    return f"{webdav_client.BUSINESS_ROOT}/{DEST_NOTES}/{relpath}", "personal-notes"


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def main() -> None:
    if not SRC_PW:
        log.info(
            "%s: PERSONAL_NOTES_SRC_APP_PASSWORD not set -- awaiting the operator's "
            "app-password in dispatch-secrets.env, no-op", SKILL_NAME,
        )
        return

    state = _load_state()
    written = skipped = blocked = 0
    # Lean-out L2 / F12 (2026-10-19): this ran every 2 min and downloaded and
    # hashed EVERY note each time (~715 full walks + downloads/day). Now:
    #   1. one Depth-0 PROPFIND of the Notes/ root; if its ETag and the routing
    #      config are what the last clean run saw, nothing changed -- exit
    #      before any walk, download or DB connection;
    #   2. otherwise walk, and download only notes whose ETag differs from the
    #      one recorded at their last import (no ETag -> download, as before).
    # The content hash stays the import decision for every downloaded note, and
    # a run that blocked a note never records the root ETag, so it is retried.
    # State keys "_root" / "_etags" sit beside the per-note entries.
    meta_root = state.get("_root") or {}
    root_tag = _root_etag()
    cfg = _config_fingerprint()
    if root_tag and meta_root.get("etag") == root_tag and meta_root.get("cfg") == cfg:
        log.info("%s: Notes root unchanged since the last import -- nothing to do", SKILL_NAME)
        return
    known_etags: dict = state.get("_etags") or {}

    with get_conn() as conn:
        etags: dict = {}
        sources = _walk("", etags)
        for relpath in sources:
            dest, bucket = _route(relpath)
            tag = etags.get(relpath, "")
            if (tag and known_etags.get(relpath) == tag
                    and str(state.get(relpath, "")).endswith(f" {dest}")):
                skipped += 1
                continue  # same ETag as at its last import, same destination
            content = _src_get(relpath)
            h = hashlib.sha256(content).hexdigest()
            # State value is "<sha256>" (pre-2026-09-06) or "<sha256> <dest>".
            # Re-import when the source changed OR its routed destination did
            # (the prefix-routing fix moved existing research notes); a
            # legacy hash-only entry counts as "destination unknown" so each
            # note is re-PUT exactly once, verbatim, to wherever it now
            # routes. Nothing is ever deleted from the old location.
            if state.get(relpath) == f"{h} {dest}":
                skipped += 1
                if tag:
                    known_etags[relpath] = tag
                continue  # source unchanged since last import

            text = content.decode("utf-8", errors="replace")
            try:
                gate(text, source=SKILL_NAME)  # CUI/PII block gate
            except ScrubGateBlocked as e:
                log.error("%s: %s BLOCKED by scrub gate: %s", SKILL_NAME, relpath, e)
                blocked += 1
                continue

            webdav_client.put(dest, content)  # verbatim; webdav_client encodes str, passes bytes through
            index_note(
                conn, dest,
                title=relpath.rsplit("/", 1)[-1][:-3],  # filename sans .md
                content=text,
                tags="personal,research,untriaged" if bucket == "personal-research" else "personal,authored",
                ingest_method=bucket,
            )
            state[relpath] = f"{h} {dest}"
            if tag:
                known_etags[relpath] = tag
            written += 1
            log.info("%s: imported %s -> %s (%s)", SKILL_NAME, relpath, dest, bucket)

        state["_etags"] = {k: v for k, v in known_etags.items() if k in etags}
        if blocked == 0 and root_tag:
            state["_root"] = {"etag": root_tag, "cfg": cfg}
        else:
            state.pop("_root", None)
        _save_state(state)
        log.info(
            "%s: %d imported, %d unchanged, %d blocked (from %d source notes)",
            SKILL_NAME, written, skipped, blocked, len(sources),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
