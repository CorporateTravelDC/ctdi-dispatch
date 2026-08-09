"""
second_brain.index_db — persistent index over the Nextcloud vault.

The vault (Nextcloud, cloud.example.com) is the source of
truth for documents; this module maintains a small, fast, queryable
SQLite index alongside it so other code (skills, the demo-archiver,
future cross-referencing work) doesn't have to re-walk WebDAV on every
lookup.

Deliberately separate from src/common/db.py (the main
corporatetraveldc.db schema authority) -- this is a distinct database
for a distinct concern (vault content, not live operational feed data),
so it gets its own schema versioning rather than growing the operational
schema's surface area.

Access is over WebDAV (standard protocol, matches the "no vendor
lock-in" architecture principle) rather than reaching into the
nextcloud-app container's filesystem directly -- this index can run
from any host that can reach the Nextcloud instance, not just one
sharing its Podman volumes.

Usage:
    python3 -m second_brain.index_db --scan            # rebuild index
    python3 -m second_brain.index_db --summary          # print counts
"""
import argparse
import hashlib
import re
import os
import sqlite3
import sys
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import requests

INDEX_DB     = os.environ.get("SECOND_BRAIN_INDEX_DB", "/var/lib/corporatetraveldc/second_brain_index.db")
WEBDAV_BASE  = os.environ.get("NEXTCLOUD_WEBDAV_BASE", "http://127.0.0.1:8090/remote.php/dav/files")
NEXTCLOUD_USER = os.environ.get("NEXTCLOUD_ADMIN_USER", "operator")
# Password is read from the same secrets file the Nextcloud Quadlet uses --
# never hardcoded, never logged, never printed.
_SECRETS_FILE = os.environ.get(
    "NEXTCLOUD_SECRETS_FILE",
    os.path.expanduser("~/.config/nextcloud/nextcloud-secrets.env"),
)

_DAV_NS = "{DAV:}"

# Category inferred from the vault folder a document lives in.
# Extend this as the vault's folder structure grows -- it's a lookup,
# not a constraint on what folders can exist.
#
# BUSINESS_ROOT holds all corporatetraveldc/[operator LLC] business
# and second-brain content, kept as a separate top-level folder from
# the operator's personal Nextcloud folders (Photos, Documents, InstantUpload,
# Templates) -- see docs/SECOND_BRAIN_STATUS.md, "Nextcloud file layout".
# Reorganized 2026-07-22: business content used to live directly under a
# bare top-level Docs/ folder, indistinguishable from personal use of a
# folder with the same generic name. Category lookup now checks one level
# deeper for anything under BUSINESS_ROOT.
#
# 2026-08-06: a dedicated-account/flattened-root redesign was drafted and
# staged elsewhere in this repo's history but never actually deployed --
# see webdav_client.py's module docstring. Reverted to match live reality.
BUSINESS_ROOT = "corporatetraveldc"

_FOLDER_CATEGORY = {
    "Docs":         "reference",
    "Photos":       "media",
    "Templates":    "template",
    "Contacts":     "contacts",
    # PARA ("Karpathy method") second-brain folders, scaffolded 2026-07-22 --
    # see docs/SECOND_BRAIN_STATUS.md and corporatetraveldc/TOC.md in the vault.
    "00-Inbox":     "inbox",
    "01-Sources":   "sources",
    "02-Concepts":  "concepts",
    "03-Entities":  "entities",
    "04-Syntheses": "syntheses",
    "05-Skills":    "skills-persona",
    "99-Archive":   "archive",
}


def _load_password() -> str | None:
    """Reads the WebDAV credential for the index scanner.

    Uses an app password (NEXTCLOUD_APP_PASSWORD), never the account
    login password -- Nextcloud rejects raw account-password Basic Auth
    on WebDAV by design (PasswordLoginForbidden) once brute-force
    protection / 2FA-aware policy is active, and an app password is the
    correct, revocable, least-privilege credential for an automated
    reader anyway (occ user:auth-tokens:add / user:auth-tokens:delete
    to rotate or revoke without touching the account password).

    Prefers the env var (threaded via EnvironmentFile=dispatch-secrets.env
    for container-invoked callers, added 2026-07-22 -- see
    second_brain.webdav_client for the sibling implementation), falling
    back to the secrets file directly for host-direct invocation."""
    env_pw = os.environ.get("NEXTCLOUD_APP_PASSWORD")
    if env_pw:
        return env_pw
    if not os.path.exists(_SECRETS_FILE):
        return None
    with open(_SECRETS_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("NEXTCLOUD_APP_PASSWORD="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vault_documents (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            path         TEXT UNIQUE NOT NULL,
            filename     TEXT NOT NULL,
            category     TEXT,
            size_bytes   INTEGER,
            mtime        TEXT,
            etag         TEXT,
            indexed_at   TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_category ON vault_documents(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_filename ON vault_documents(filename)")

    # Schema extension 2026-07-22 -- tags/ingest_method/compile_status,
    # matching the fields the original 2026-07-18 plan called for
    # ("tracks every note's source, tags, links, ingest method, and
    # compile status"). Added via ALTER rather than baked into the
    # CREATE TABLE above so existing installs migrate in place.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(vault_documents)")}
    for col, ddl in (
        ("tags", "ALTER TABLE vault_documents ADD COLUMN tags TEXT"),
        ("ingest_method", "ALTER TABLE vault_documents ADD COLUMN ingest_method TEXT"),
        ("compile_status", "ALTER TABLE vault_documents ADD COLUMN compile_status TEXT DEFAULT 'raw'"),
    ):
        if col not in existing_cols:
            conn.execute(ddl)

    # Full-text search over vault *notes* (markdown content written by
    # second_brain_daily/weekly/remember/rss) -- deliberately NOT populated
    # for the full vault scan (388 files, mostly personal photos and
    # Nextcloud template boilerplate -- not useful for FTS and expensive to
    # fetch over WebDAV just to index). Populated incrementally by
    # index_note() as real notes get written.
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS vault_notes_fts USING fts5(
            path UNINDEXED, title, content, tags, tokenize='porter'
        )
    """)

    # Links/backlinks -- 2026-07-23, closes the gap flagged in
    # docs/SECOND_BRAIN_STATUS.md ("the original plan's 'links' field is
    # not represented in the index schema at all"). Obsidian-native
    # [[wiki-link]] syntax, parsed out of note content at index_note()
    # time (not during the full vault --scan, which only PROPFINDs
    # metadata and never fetches file bytes -- same cost rationale as
    # the FTS table above not being backfilled against the full scan).
    # Stores the raw link target verbatim rather than trying to resolve
    # it to a real path at write time -- a link can legitimately point
    # at a note that doesn't exist yet (Obsidian treats this as normal,
    # not an error), so resolution happens at query time in
    # get_backlinks() instead.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vault_links (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT NOT NULL,
            target      TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_links_source ON vault_links(source_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_links_target ON vault_links(target)")
    conn.commit()


_WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _extract_links(content: str) -> list[str]:
    """Pull [[target]] and [[target|alias]] wiki-links out of note
    content. Returns unique targets, whitespace-trimmed, in first-seen
    order. Deliberately simple regex, not a markdown parser -- matches
    Obsidian's own link syntax exactly, no need for more."""
    seen: list[str] = []
    for m in _WIKI_LINK_RE.finditer(content):
        target = m.group(1).strip()
        if target and target not in seen:
            seen.append(target)
    return seen


def index_note(
    conn: sqlite3.Connection,
    path: str,
    title: str,
    content: str,
    tags: str = "",
    ingest_method: str = "",
    compile_status: str = "raw",
) -> None:
    """Register a single note immediately after writing it to the vault --
    so it's queryable (including via FTS) before the next full --scan,
    rather than waiting on the vault index DB's normal refresh cadence
    (which is itself not on a timer yet -- see docs/SECOND_BRAIN_STATUS.md).
    """
    now = datetime.now(timezone.utc).isoformat()
    filename = path.rsplit("/", 1)[-1]
    category = _category_for(path)
    size_bytes = len(content.encode("utf-8"))

    existing = conn.execute("SELECT id FROM vault_documents WHERE path=?", (path,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE vault_documents SET size_bytes=?, mtime=?, indexed_at=?, "
            "tags=?, ingest_method=?, compile_status=? WHERE path=?",
            (size_bytes, now, now, tags, ingest_method, compile_status, path),
        )
    else:
        conn.execute(
            "INSERT INTO vault_documents(path, filename, category, size_bytes, mtime, "
            "etag, indexed_at, tags, ingest_method, compile_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (path, filename, category, size_bytes, now, None, now, tags, ingest_method, compile_status),
        )

    conn.execute("DELETE FROM vault_notes_fts WHERE path=?", (path,))
    conn.execute(
        "INSERT INTO vault_notes_fts(path, title, content, tags) VALUES (?, ?, ?, ?)",
        (path, title, content, tags),
    )

    conn.execute("DELETE FROM vault_links WHERE source_path=?", (path,))
    for target in _extract_links(content):
        conn.execute(
            "INSERT INTO vault_links(source_path, target, created_at) VALUES (?, ?, ?)",
            (path, target, now),
        )

    conn.commit()


def search_notes(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[dict]:
    """Full-text search over indexed vault notes. Returns path/title/snippet."""
    rows = conn.execute(
        "SELECT path, title, snippet(vault_notes_fts, 2, '**', '**', '...', 20) "
        "FROM vault_notes_fts WHERE vault_notes_fts MATCH ? ORDER BY rank LIMIT ?",
        (query, limit),
    ).fetchall()
    return [{"path": r[0], "title": r[1], "snippet": r[2]} for r in rows]


def get_links(conn: sqlite3.Connection, path: str) -> list[str]:
    """Outgoing [[wiki-links]] from a given note, in the order they were
    extracted."""
    rows = conn.execute(
        "SELECT target FROM vault_links WHERE source_path=? ORDER BY id", (path,)
    ).fetchall()
    return [r[0] for r in rows]


def get_backlinks(conn: sqlite3.Connection, target: str) -> list[dict]:
    """Notes that link TO target. Two-pass: exact match first (case-
    insensitive, since [[Entity Name]] casing can drift between authors/
    runs), then a fallback substring match so a query like "Marine One"
    still finds a link written as "Marine One TFR" -- forward/partial
    links are normal in a wiki-style vault, not something to hide."""
    exact = conn.execute(
        "SELECT DISTINCT source_path FROM vault_links WHERE target = ? COLLATE NOCASE",
        (target,),
    ).fetchall()
    if exact:
        return [{"source_path": r[0], "match": "exact"} for r in exact]
    fuzzy = conn.execute(
        "SELECT DISTINCT source_path, target FROM vault_links WHERE target LIKE ? COLLATE NOCASE",
        (f"%{target}%",),
    ).fetchall()
    return [{"source_path": r[0], "match": f"fuzzy ({r[1]})"} for r in fuzzy]


def _category_for(path: str) -> str:
    parts = path.strip("/").split("/") if path.strip("/") else []
    if not parts:
        return "uncategorized"
    if parts[0] == BUSINESS_ROOT:
        # one level deeper: corporatetraveldc/Docs/... -> "reference", etc.
        if len(parts) > 1:
            return _FOLDER_CATEGORY.get(parts[1], "business-uncategorized")
        return "business-uncategorized"
    return _FOLDER_CATEGORY.get(parts[0], "uncategorized")


def _propfind(url: str, auth: tuple[str, str], depth: str = "1") -> ET.Element:
    body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:getcontentlength/>
    <d:getlastmodified/>
    <d:getetag/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>"""
    resp = requests.request(
        "PROPFIND", url, auth=auth, data=body,
        headers={
            "Depth": depth,
            "Content-Type": "application/xml",
            # Nextcloud validates Host against trusted_domains even for
            # loopback requests; the container is only configured to
            # trust its real public hostname, not 127.0.0.1.
            "Host": "cloud.example.com",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def _walk_webdav(base_url: str, auth: tuple[str, str], rel_path: str = "") -> list[dict]:
    """Recursively enumerate files under rel_path via WebDAV PROPFIND.
    Returns a flat list of {path, size, mtime, etag} for files (not directories)."""
    results: list[dict] = []
    url = f"{base_url}/{rel_path}".rstrip("/")
    try:
        root = _propfind(url, auth)
    except requests.exceptions.RequestException as exc:
        print(f"  ! propfind failed for {rel_path or '/'}: {exc}", file=sys.stderr)
        return results

    for resp in root.findall(f"{_DAV_NS}response"):
        href_el = resp.find(f"{_DAV_NS}href")
        if href_el is None or href_el.text is None:
            continue
        href = requests.utils.unquote(href_el.text)
        # href includes the full dav path prefix -- strip down to the
        # path relative to the user's files root.
        marker = f"/remote.php/dav/files/{NEXTCLOUD_USER}/"
        if marker not in href:
            continue
        item_path = href.split(marker, 1)[1]
        if not item_path or item_path.rstrip("/") == rel_path.rstrip("/"):
            continue  # the collection's own self-entry

        propstat = resp.find(f"{_DAV_NS}propstat")
        if propstat is None:
            continue
        prop = propstat.find(f"{_DAV_NS}prop")
        resourcetype = prop.find(f"{_DAV_NS}resourcetype")
        is_dir = resourcetype is not None and resourcetype.find(f"{_DAV_NS}collection") is not None

        if is_dir:
            results.extend(_walk_webdav(base_url, auth, item_path.rstrip("/")))
        else:
            size_el = prop.find(f"{_DAV_NS}getcontentlength")
            mtime_el = prop.find(f"{_DAV_NS}getlastmodified")
            etag_el = prop.find(f"{_DAV_NS}getetag")
            results.append({
                "path": item_path,
                "size": int(size_el.text) if size_el is not None and size_el.text else 0,
                "mtime": mtime_el.text if mtime_el is not None else None,
                "etag": etag_el.text if etag_el is not None else None,
            })
    return results


def scan_vault(conn: sqlite3.Connection) -> dict:
    """Walk the whole vault via WebDAV and upsert every file into the index.
    Returns a summary dict. Never deletes -- a vault document that
    disappears server-side is left in the index (stale-but-visible is
    safer than silently forgetting something existed; pruning is a
    separate, explicit operation if ever needed)."""
    password = _load_password()
    if not password:
        return {"error": f"could not read NEXTCLOUD_APP_PASSWORD from {_SECRETS_FILE}"}

    auth = (NEXTCLOUD_USER, password)
    base_url = f"{WEBDAV_BASE}/{NEXTCLOUD_USER}"
    files = _walk_webdav(base_url, auth)

    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    updated_count = 0
    for f in files:
        filename = f["path"].rsplit("/", 1)[-1]
        category = _category_for(f["path"])
        existing = conn.execute(
            "SELECT etag FROM vault_documents WHERE path=?", (f["path"],)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO vault_documents(path, filename, category, size_bytes, mtime, etag, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f["path"], filename, category, f["size"], f["mtime"], f["etag"], now),
            )
            new_count += 1
        elif existing[0] != f["etag"]:
            conn.execute(
                "UPDATE vault_documents SET size_bytes=?, mtime=?, etag=?, indexed_at=? WHERE path=?",
                (f["size"], f["mtime"], f["etag"], now, f["path"]),
            )
            updated_count += 1
    conn.commit()
    return {
        "total_seen": len(files),
        "new": new_count,
        "updated": updated_count,
        "unchanged": len(files) - new_count - updated_count,
    }


def summary(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM vault_documents").fetchone()[0]
    by_category = dict(conn.execute(
        "SELECT category, COUNT(*) FROM vault_documents GROUP BY category ORDER BY 2 DESC"
    ).fetchall())
    last_indexed = conn.execute(
        "SELECT MAX(indexed_at) FROM vault_documents"
    ).fetchone()[0]
    return {"total_documents": total, "by_category": by_category, "last_indexed": last_indexed}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", action="store_true", help="rescan the vault and update the index")
    ap.add_argument("--summary", action="store_true", help="print index summary")
    ap.add_argument("--search", metavar="QUERY", help="full-text search indexed vault notes")
    ap.add_argument("--backlinks", metavar="TARGET", help="show notes that link to TARGET")
    args = ap.parse_args()

    conn = sqlite3.connect(INDEX_DB)
    init_db(conn)

    if args.scan:
        result = scan_vault(conn)
        print(f"scan complete: {result}")
    if args.search:
        for r in search_notes(conn, args.search):
            print(f"{r['path']}\n  {r['title']}\n  {r['snippet']}\n")
    if args.backlinks:
        hits = get_backlinks(conn, args.backlinks)
        if not hits:
            print(f"no backlinks found for {args.backlinks!r}")
        for h in hits:
            print(f"{h['source_path']}  ({h['match']})")
    if args.summary or not (args.scan or args.summary or args.search or args.backlinks):
        print(f"summary: {summary(conn)}")

    conn.close()


if __name__ == "__main__":
    main()
