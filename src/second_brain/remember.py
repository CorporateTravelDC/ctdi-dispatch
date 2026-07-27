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
import sqlite3
import sys
from datetime import datetime, timezone

from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate


def remember_text(text: str, tags: str = "") -> str:
    """
    Scrub-gate, write, and index a manual note. Returns the vault-relative
    path written. Raises ScrubGateBlocked if the text trips the CUI/PII
    gate -- callers (CLI, REST route) are responsible for turning that
    into their own appropriate error surface.
    """
    if not text or not text.strip():
        raise ValueError("no text provided")

    text = gate(text, source="remember-manual")

    now = datetime.now(timezone.utc)
    slug = now.strftime("%Y%m%dT%H%M%SZ")
    tags = tags or "manual,high-priority"

    frontmatter = (
        "---\n"
        f"captured_at: {now.isoformat()}\n"
        "ingest_method: manual\n"
        f"tags: {tags}\n"
        "---\n\n"
    )
    note = frontmatter + text.strip() + "\n"

    rel_path = f"{webdav_client.BUSINESS_ROOT}/01-Sources/manual/{slug}.md"
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
    args = parser.parse_args()

    text = sys.stdin.read() if args.stdin else args.text

    try:
        rel_path = remember_text(text, tags=args.tags)
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
