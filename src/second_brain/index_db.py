"""
second_brain.index_db — persistent index over the Nextcloud vault.

The vault (Nextcloud, cloud.example.com) is the source of
truth for documents; this module maintains a small, fast, queryable
index alongside it so other code (skills, the demo-archiver, future
cross-referencing work) doesn't have to re-walk WebDAV on every lookup.

Deliberately separate from src/common/db.py (the main corporatetraveldc.db
schema authority) -- this is a distinct database for a distinct concern
(vault content, not live operational feed data), so it gets its own
schema versioning rather than growing the operational schema's surface
area.

2026-09-19: moved from a standalone SQLite file
(/var/lib/corporatetraveldc/second_brain_index.db) to the same
corporatetraveldc-pgsql Postgres instance the dispatch write path uses
(pg_schema/0055_second_brain_index.sql) -- docs/POSTGRES_MIGRATION.md
sec2's JOIN-exception reversal (same reason cifp_fixes/faa/opensky moved):
geometric reasoning's Phase 0 backfill is a sustained writer against
semantic_note_derivations while second-brain poller skills read/write the
same tables concurrently on their own timers -- SQLite WAL only allows
one writer at a time, which would have stalled every skill during that
backfill. get_conn() is the shared accessor every caller should use now
(replaces each site's own `sqlite3.connect(INDEX_DB)`); it is a
context manager, same as db_backend.pg_conn() which it wraps directly.

vault_notes_fts (SQLite FTS5) has no Postgres equivalent -- replaced by
vault_notes_fulltext, a plain table with a generated tsvector column
(title/tags/content weighted A/B/C) + a GIN index; search_notes() below
uses plainto_tsquery/ts_rank/ts_headline instead of FTS5's MATCH/rank/
snippet(). ts_headline was chosen over ts_rank alone specifically because
it's Postgres's native highlighted-excerpt function -- the direct
equivalent of what snippet() did, not just "the closest thing available."

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
import re
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import requests

from common import db_backend

def _require_nextcloud_user() -> str:
    """No silent "operator" fallback -- see second_brain.webdav_client's identical
    helper for the 2026-08-09 incident this avoids (a vault write silently
    landing in the retired operator account with no error anywhere)."""
    user = os.environ.get("NEXTCLOUD_ADMIN_USER")
    if not user:
        raise RuntimeError(
            "NEXTCLOUD_ADMIN_USER is not set. Source "
            "/etc/corporatetraveldc/dispatch.env, or export "
            "NEXTCLOUD_ADMIN_USER=corporatetraveldc explicitly, before importing "
            "second_brain.index_db."
        )
    return user


# Retained only as a legacy/back-compat name -- nothing here still opens
# this path directly (get_conn() below is Postgres now). A handful of
# callers historically imported INDEX_DB just to pass it to their own
# sqlite3.connect(); those call sites were converted to get_conn() in the
# same pass that added this module's 2026-09-19 header.
INDEX_DB     = os.environ.get("SECOND_BRAIN_INDEX_DB", "/var/lib/corporatetraveldc/second_brain_index.db")
WEBDAV_BASE  = os.environ.get("NEXTCLOUD_WEBDAV_BASE", "http://127.0.0.1:8090/remote.php/dav/files")
NEXTCLOUD_USER = _require_nextcloud_user()
# Password is read from the same secrets file the Nextcloud Quadlet uses --
# never hardcoded, never logged, never printed.
_SECRETS_FILE = os.environ.get(
    "NEXTCLOUD_SECRETS_FILE",
    "/etc/corporatetraveldc/dispatch-secrets.env",
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


@contextmanager
def get_conn():
    """The shared connection accessor every caller should use now --
    replaces each site's own `sqlite3.connect(INDEX_DB)`. Thin wrapper
    around db_backend.pg_conn() (same translating/pooled connection the
    rest of the dispatch write path uses) so this module has exactly one
    place that knows the second-brain tables live in Postgres now."""
    with db_backend.pg_conn() as conn:
        yield conn


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


def init_db(conn) -> None:
    """No-op on Postgres -- schema is created by pg_schema/0055 + Phase 1's
    pg_migrate.py (see docs/POSTGRES_MIGRATION.md sec4), same pattern
    ingest/poller/web/pusher's main.py already uses for the dispatch write
    path. Kept as a callable (rather than removed) so every existing
    caller's `init_db(conn)` line keeps working unchanged."""
    return None


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
    conn,
    path: str,
    title: str,
    content: str,
    tags: str = "",
    ingest_method: str = "",
    compile_status: str = "raw",
) -> None:
    """Register a single note immediately after writing it to the vault --
    so it's queryable (including via full-text search) before the next
    full --scan, rather than waiting on the vault index DB's normal
    refresh cadence (which is itself not on a timer yet -- see
    docs/SECOND_BRAIN_STATUS.md).
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

    # vault_notes_fulltext.path FKs to vault_documents.path -- the
    # insert/update above must land first, which it does (this is a
    # synchronous sequence, not two separate transactions).
    conn.execute(
        "INSERT INTO vault_notes_fulltext(path, title, content, tags) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (path) DO UPDATE SET title=excluded.title, content=excluded.content, tags=excluded.tags",
        (path, title, content, tags),
    )

    conn.execute("DELETE FROM vault_links WHERE source_path=?", (path,))
    for target in _extract_links(content):
        conn.execute(
            "INSERT INTO vault_links(source_path, target, created_at) VALUES (?, ?, ?)",
            (path, target, now),
        )

    conn.commit()


_FTS_BOOLEAN_KEYWORDS = frozenset({"AND", "OR", "NOT"})
_TS_BOOLEAN_MAP = {"AND": "&", "OR": "|", "NOT": "!"}


def _quote_hyphenated_raw_terms(query: str) -> str:
    """raw=True mode passes something close to a boolean query straight
    through, translated from FTS5's AND/OR/NOT keyword syntax to
    Postgres's to_tsquery operators (&/|/!) -- NOT the same grammar
    (to_tsquery has no direct "col:term" filter equivalent; any such
    token here is passed through as a plain lexeme, which to_tsquery
    will just treat literally). A bareword with a hyphen is otherwise
    exactly the kind of token that trips up query parsers expecting a
    single lexeme -- quoted here as `'term'` (to_tsquery's own phrase-safe
    form) so it can't be misread as an operator, mirroring this
    function's original FTS5-hyphen-bareword purpose.
    """
    out = []
    for tok in query.split():
        upper = tok.upper()
        if upper in _FTS_BOOLEAN_KEYWORDS:
            out.append(_TS_BOOLEAN_MAP[upper])
        elif tok in ("&", "|", "!", "("):
            out.append(tok)
        else:
            out.append(f"'{tok}'")
    return " ".join(out)


def search_notes(
    conn, query: str, limit: int = 10, raw: bool = False
) -> list[dict]:
    """Full-text search over indexed vault notes. Returns path/title/snippet.

    Default (raw=False): plainto_tsquery treats the whole input as plain
    literal terms ANDed together -- no operator-parsing at all, so a
    hyphenated bareword like "ep-advance" just matches literally with no
    special-casing needed (unlike FTS5's MATCH, which parsed bare hyphens
    as column-filter/NOT operators and needed phrase-quoting to work
    around it). Pass raw=True for boolean queries (AND/OR/NOT) via
    to_tsquery, translated by _quote_hyphenated_raw_terms above.
    """
    if raw:
        tsquery_sql = "to_tsquery('english', ?)"
        fts_query = _quote_hyphenated_raw_terms(query)
    else:
        tsquery_sql = "plainto_tsquery('english', ?)"
        fts_query = query
    rows = conn.execute(
        f"SELECT path, title, "
        f"ts_headline('english', content, {tsquery_sql}, "
        f"'StartSel=**, StopSel=**, MaxFragments=1, MaxWords=20, MinWords=5') AS snippet "
        f"FROM vault_notes_fulltext "
        f"WHERE search_vector @@ {tsquery_sql} "
        f"ORDER BY ts_rank(search_vector, {tsquery_sql}) DESC LIMIT ?",
        (fts_query, fts_query, fts_query, limit),
    ).fetchall()
    return [{"path": r["path"], "title": r["title"], "snippet": r["snippet"]} for r in rows]


def get_links(conn, path: str) -> list[str]:
    """Outgoing [[wiki-links]] from a given note, in the order they were
    extracted."""
    rows = conn.execute(
        "SELECT target FROM vault_links WHERE source_path=? ORDER BY id", (path,)
    ).fetchall()
    return [r["target"] for r in rows]


def get_backlinks(conn, target: str) -> list[dict]:
    """Notes that link TO target. Two-pass: exact match first (case-
    insensitive, since [[Entity Name]] casing can drift between authors/
    runs), then a fallback substring match so a query like "Marine One"
    still finds a link written as "Marine One TFR" -- forward/partial
    links are normal in a wiki-style vault, not something to hide."""
    exact = conn.execute(
        "SELECT DISTINCT source_path FROM vault_links WHERE LOWER(target) = LOWER(?)",
        (target,),
    ).fetchall()
    if exact:
        return [{"source_path": r["source_path"], "match": "exact"} for r in exact]
    fuzzy = conn.execute(
        "SELECT DISTINCT source_path, target FROM vault_links WHERE target ILIKE ?",
        (f"%{target}%",),
    ).fetchall()
    return [{"source_path": r["source_path"], "match": f"fuzzy ({r['target']})"} for r in fuzzy]


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


def scan_vault(conn) -> dict:
    """Walk BUSINESS_ROOT ("corporatetraveldc/") via WebDAV and upsert every
    file into the index. Returns a summary dict. Never deletes -- a vault
    document that disappears server-side is left in the index
    (stale-but-visible is safer than silently forgetting something
    existed; pruning is a separate, explicit operation if ever needed).

    FIXED 2026-08-24: this used to call _walk_webdav(base_url, auth) with
    no rel_path, i.e. it walked the operator's ENTIRE Nextcloud account
    root -- despite BUSINESS_ROOT being defined above specifically to
    keep business/second-brain content separate from the operator's personal
    Nextcloud folders (Photos, Documents, InstantUpload, Templates).
    Operator directive, live-checked: "My instant upload should not be
    in that at all. That should be in my personal side." Confirmed live
    before fixing -- 766 InstantUpload/ files (phone camera uploads,
    screenshots, Canva boards) had been swept into vault_documents this
    way, none of them business content, none of them meant to be
    reachable by the semantic layer or --trace/--search at all. Scoping
    the walk to BUSINESS_ROOT means personal files are never even
    discovered, not merely filtered after the fact."""
    password = _load_password()
    if not password:
        return {"error": f"could not read NEXTCLOUD_APP_PASSWORD from {_SECRETS_FILE}"}

    auth = (NEXTCLOUD_USER, password)
    base_url = f"{WEBDAV_BASE}/{NEXTCLOUD_USER}"
    files = _walk_webdav(base_url, auth, rel_path=BUSINESS_ROOT)

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
        elif existing["etag"] != f["etag"]:
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


def summary(conn) -> dict:
    total = conn.execute("SELECT COUNT(*) AS n FROM vault_documents").fetchone()["n"]
    by_category = {
        r["category"]: r["n"] for r in conn.execute(
            "SELECT category, COUNT(*) AS n FROM vault_documents GROUP BY category ORDER BY 2 DESC"
        ).fetchall()
    }
    last_indexed = conn.execute(
        "SELECT MAX(indexed_at) AS n FROM vault_documents"
    ).fetchone()["n"]
    return {"total_documents": total, "by_category": by_category, "last_indexed": last_indexed}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", action="store_true", help="rescan the vault and update the index")
    ap.add_argument("--summary", action="store_true", help="print index summary")
    ap.add_argument("--search", metavar="QUERY", help="full-text search indexed vault notes")
    ap.add_argument(
        "--raw", action="store_true",
        help="don't phrase-wrap --search QUERY; use to_tsquery boolean syntax (AND/OR/NOT) as-is",
    )
    ap.add_argument("--backlinks", metavar="TARGET", help="show notes that link to TARGET")
    args = ap.parse_args()

    with get_conn() as conn:
        if args.scan:
            result = scan_vault(conn)
            print(f"scan complete: {result}")
        if args.search:
            for r in search_notes(conn, args.search, raw=args.raw):
                print(f"{r['path']}\n  {r['title']}\n  {r['snippet']}\n")
        if args.backlinks:
            hits = get_backlinks(conn, args.backlinks)
            if not hits:
                print(f"no backlinks found for {args.backlinks!r}")
            for h in hits:
                print(f"{h['source_path']}  ({h['match']})")
        if args.summary or not (args.scan or args.summary or args.search or args.backlinks):
            print(f"summary: {summary(conn)}")


if __name__ == "__main__":
    main()
