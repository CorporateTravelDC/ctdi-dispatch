"""
second_brain.remember -- manual "remember this" capture, writing directly
into corporatetraveldc/01-Sources/manual/ in the vault. Runs through the
same CUI/PII scrub gate as the automated ingest paths.

CLI usage:
    python3 -m second_brain.remember "some fact or quote to remember" --tags a,b,c
    echo "longer text piped in" | python3 -m second_brain.remember --stdin

Also callable as a library function (remember_text) -- web/routes/remember.py
wraps this in a REST endpoint so it can be driven from a Cowork skill instead
of only from a shell on the Pi. Both entry points share this one code path
(scrub gate -> WebDAV write -> index) rather than duplicating the logic.
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate


def remember_text(text: str, tags: str = "", author_kind: str = "human",
                  author_name: str | None = None, source: str | None = None,
                  dest_subdir: str = "manual") -> str:
    """
    Scrub-gate, write, and index a manual note. Returns the vault-relative
    path written. Raises ScrubGateBlocked if the text trips the CUI/PII
    gate -- callers (CLI, REST route) are responsible for turning that
    into their own appropriate error surface.

    author_kind/author_name/source, added 2026-08-24 per operator
    directive ("the metadata itself determines who created it, who
    brought it, and where it was done"): every note written through this
    ONE code path used to be indistinguishable -- ingest_method='manual'
    and tag 'manual' fired for the operator typing a note by hand AND
    for an agent session calling this same function, and the ontology's
    human_authored concept matched on exactly that ingest_method, so
    every agent-written note in the vault was silently misclassified as
    human-authored. author_kind is now a real, explicit signal (default
    'human' -- a CLI tool being run interactively is the traditional
    case when the caller doesn't say otherwise; the REST route below
    defaults to 'agent' instead, since its own docstring says it exists
    for automation) that becomes a real emitted tag
    ('agent-authored'/'human-authored'), which is what the corrected
    ontology's agent_authored/human_authored concepts now key on instead
    of the ambiguous ingest_method. Deliberately still deterministic --
    no inference from the text content, only from what the caller states.

    dest_subdir, added 2026-08-24: which 01-Sources/ subfolder to write
    into (default 'manual', preserving every existing caller's behavior
    unchanged). Added for personal-data-analysis findings (Uber/LinkedIn
    export work, see .claude/skills/personal-export-analysis/) that must
    never land in the general business-content area of the vault per
    operator directive -- 01-Sources/personal-notes/ already exists as a
    precedent (second_brain_personal_notes_import.py's own sanctioned
    personal-content import), this just lets remember_text() target it
    too instead of a caller reaching for webdav_client.put() directly and
    bypassing the scrub gate / index_note call this function guarantees."""
    if not text or not text.strip():
        raise ValueError("no text provided")
    if author_kind not in ("human", "agent"):
        raise ValueError("author_kind must be 'human' or 'agent'")

    text = gate(text, source="remember-manual")

    now = datetime.now(timezone.utc)
    slug = now.strftime("%Y%m%dT%H%M%SZ")
    author_tag = f"{author_kind}-authored"
    base_tags = tags or "manual,high-priority"
    tags = f"{base_tags},{author_tag}" if author_tag not in base_tags else base_tags
    author_name = author_name or ("operator" if author_kind == "human" else "claude-agent-session")
    source = source or os.getcwd()

    frontmatter = (
        "---\n"
        f"captured_at: {now.isoformat()}\n"
        "ingest_method: manual\n"
        f"author_kind: {author_kind}\n"
        f"author: {author_name}\n"
        f"source: {source}\n"
        f"tags: {tags}\n"
        "---\n\n"
    )
    note = frontmatter + text.strip() + "\n"

    rel_path = f"{webdav_client.BUSINESS_ROOT}/01-Sources/{dest_subdir}/{slug}.md"
    webdav_client.put(rel_path, note)

    conn = sqlite3.connect(INDEX_DB)
    init_vault_db(conn)
    index_note(conn, rel_path, title=text.strip()[:60], content=note,
               tags=tags, ingest_method="manual")
    conn.close()

    return rel_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", nargs="?", help="the fact/quote/note to remember")
    parser.add_argument("--stdin", action="store_true", help="read text from stdin instead")
    parser.add_argument("--tags", default="", help="comma-separated tags")
    parser.add_argument("--author-kind", default="human", choices=["human", "agent"],
                        help="who actually wrote this note (default: human -- an agent "
                             "session calling this MUST pass --author-kind agent)")
    parser.add_argument("--author-name", default=None,
                        help="optional specific identity (default: 'operator' or "
                             "'claude-agent-session' based on --author-kind)")
    parser.add_argument("--dest-subdir", default="manual",
                        help="01-Sources/ subfolder to write into (default: manual). "
                             "Use 'personal-notes' for personal-data findings that must "
                             "never land in the general business-content area.")
    args = parser.parse_args()

    text = sys.stdin.read() if args.stdin else args.text

    try:
        rel_path = remember_text(text, tags=args.tags, author_kind=args.author_kind,
                                 author_name=args.author_name, dest_subdir=args.dest_subdir)
    except ValueError as e:
        print(f"error: {e} (pass as argument or use --stdin)", file=sys.stderr)
        return 1
    except ScrubGateBlocked as e:
        print(f"BLOCKED by CUI/PII scrub gate: {e}", file=sys.stderr)
        return 2

    print(f"saved: {rel_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
