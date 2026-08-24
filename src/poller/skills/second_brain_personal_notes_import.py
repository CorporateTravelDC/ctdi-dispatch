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

    Notes/<RESEARCH_CATEGORY>/...  ->  vault 00-Inbox/personal-research/   (AI-processed, inbox)
    everything else under Notes/   ->  vault 01-Sources/personal-notes/    (stable, untouched)

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
import sqlite3
import urllib.parse
from xml.etree import ElementTree as ET

import requests

from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
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

RESEARCH_CATEGORY = os.getenv("PERSONAL_NOTES_RESEARCH_CATEGORY", "Research").strip("/")
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


def _propfind(url: str) -> ET.Element:
    body = (
        '<?xml version="1.0"?>'
        '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>'
    )
    r = requests.request(
        "PROPFIND", url, auth=_src_auth(), data=body,
        headers={"Host": SRC_HOST, "Depth": "1", "Content-Type": "application/xml"},
        timeout=30,
    )
    r.raise_for_status()
    return ET.fromstring(r.content)


def _is_collection(resp_el: ET.Element) -> bool:
    rt = resp_el.find(f"{_DAV}propstat/{_DAV}prop/{_DAV}resourcetype")
    return rt is not None and rt.find(f"{_DAV}collection") is not None


def _walk(rel: str = "") -> list[str]:
    """Recursive Depth-1 walk of operator's Notes/ (Nextcloud disallows Depth:
    infinity). Return .md paths relative to the Notes root."""
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
            files.extend(_walk(relpath))
        elif relpath.lower().endswith(".md"):
            files.append(relpath)
    return files


def _src_get(relpath: str) -> bytes:
    url = f"{_src_root_url()}/{_quote(relpath)}"
    r = requests.get(url, auth=_src_auth(), headers={"Host": SRC_HOST}, timeout=30)
    r.raise_for_status()
    return r.content


def _route(relpath: str) -> tuple[str, str]:
    """Map a source relpath to (vault rel_path, bucket)."""
    top = relpath.split("/", 1)[0]
    if top.lower() == RESEARCH_CATEGORY.lower():
        sub = relpath[len(top) + 1:]  # strip "<RESEARCH_CATEGORY>/"
        return f"{webdav_client.BUSINESS_ROOT}/{DEST_RESEARCH}/{sub}", "personal-research"
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
    conn = sqlite3.connect(INDEX_DB)
    init_vault_db(conn)
    written = skipped = blocked = 0

    try:
        sources = _walk("")
        for relpath in sources:
            content = _src_get(relpath)
            h = hashlib.sha256(content).hexdigest()
            if state.get(relpath) == h:
                skipped += 1
                continue  # source unchanged since last import

            text = content.decode("utf-8", errors="replace")
            try:
                gate(text, source=SKILL_NAME)  # CUI/PII block gate
            except ScrubGateBlocked as e:
                log.error("%s: %s BLOCKED by scrub gate: %s", SKILL_NAME, relpath, e)
                blocked += 1
                continue

            dest, bucket = _route(relpath)
            webdav_client.put(dest, content)  # verbatim; webdav_client encodes str, passes bytes through
            index_note(
                conn, dest,
                title=relpath.rsplit("/", 1)[-1][:-3],  # filename sans .md
                content=text,
                tags="personal,research,untriaged" if bucket == "personal-research" else "personal,authored",
                ingest_method=bucket,
            )
            state[relpath] = h
            written += 1
            log.info("%s: imported %s -> %s (%s)", SKILL_NAME, relpath, dest, bucket)

        _save_state(state)
        log.info(
            "%s: %d imported, %d unchanged, %d blocked (from %d source notes)",
            SKILL_NAME, written, skipped, blocked, len(sources),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
