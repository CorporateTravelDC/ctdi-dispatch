"""
second_brain.knowledge_graph.retrofit_links -- retrofit real [[wikilinks]]
into the existing vault so the wiki half of Karpathy's LLM-Wiki method
actually exists.

The finding that motivates this (2026-08-11): the vault's ingestion pipeline
implemented only the filed-markdown half of the LLM-Wiki pattern. Notes are
filed into PARA folders, but no automated writer emits [[wikilink]]
cross-references (index_db.py grew a vault_links table 2026-07-23 and its
own status doc notes population "depends on future content actually using
[[...]] syntax -- none of the auto-generated notes emit it"). Obsidian's
Graph View -- the native visualization for this method -- therefore renders
the vault as disconnected dots. This module fixes the content, not just the
report: it adds genuine links wherever a note clearly references an entity
that recurs in the vault.

How it edits (deliberately conservative -- this touches the live vault):
1. Entity hub notes. For every lexicon entity mentioned in >= MIN_NOTES
   curated notes, ensure a hub note exists at 03-Entities/<Label>.md (the
   PARA folder that was always designated "people / clients / aircraft /
   vendors / orgs -- link hub" and has sat empty since 2026-07-22). Existing
   files are never overwritten.
2. Per-note link footers. For each curated note that mentions hub entities,
   append ONE clearly-marked footer block:

       <!-- auto-wikilinks v1 ... -->
       **Linked:** [[SWIM]] · [[DCA]] · ...

   Appending (rather than rewriting terms inline) is a deliberate safety
   choice: automated inline substitution across YAML frontmatter, tables,
   URLs, and code blocks risks corrupting content; a footer is additive,
   obvious, reversible, and Obsidian's graph/backlink machinery counts a
   link anywhere in the file. Notes that already carry an organic [[link]]
   to an entity don't get that entity duplicated in the footer.
3. Idempotent. The marker comment means re-runs skip already-retrofitted
   notes (so an incremental weekly pass only touches new content). Never
   edits: 00-Inbox/rss/ (untriaged machine noise), notepad/processed/
   (archived copies), .internal-backups/, non-markdown files.

01-Sources "immutable after ingest" note: the footer does not alter ingested
content, it annotates below it -- same spirit as Obsidian users adding
links to literature notes. The marker makes every retrofitted byte
attributable and strippable.

Access: same read pattern as build_graph.py -- Nextcloud direct on
127.0.0.1:8090 via webdav_client's auth/Host-header pattern; writes go
through webdav_client.put() like every other vault writer.

Usage:
    python3 -m second_brain.knowledge_graph.retrofit_links --dry-run
    python3 -m second_brain.knowledge_graph.retrofit_links
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

from second_brain import webdav_client
from second_brain.knowledge_graph.build_graph import (
    MD_EXT, RSS_PREFIX, WIKILINK_RE, _session, enumerate_vault, fetch_all,
)
from second_brain.knowledge_graph.lexicon import LEXICON, SUBTYPE_OF

MARKER = "<!-- auto-wikilinks"
HUB_DIR = "03-Entities"
MIN_NOTES = 3          # entity must recur in >= this many notes to get a hub
MAX_FOOTER_LINKS = 12  # keep footers scannable

SKIP_PREFIXES = (
    ".internal-backups/",
    RSS_PREFIX,
    "06-AI-Memory/notepad/processed/",
)

_HUB_DESCRIPTIONS = {
    "person": "Person connected to CorporateTravelDC operations.",
    "agent": "AI agent/provider working with this platform.",
    "org": "Organization relevant to CorporateTravelDC's operating picture.",
    "place": "Location in the platform's DC-area operating footprint.",
    "system": "System/data source in or around the dispatch platform.",
    "project": "CorporateTravelDC project/workstream.",
    "topic": "Recurring topic across vault notes.",
}


def curated_paths(paths: list[str]) -> list[str]:
    return [p for p in paths if p.endswith(MD_EXT)
            and not p.startswith(SKIP_PREFIXES)]


def entity_mentions(text: str) -> Counter:
    c: Counter = Counter()
    for label, _st, rx in LEXICON:
        n = len(rx.findall(text))
        if n:
            c[label] = n
    return c


def hub_path(label: str) -> str:
    return f"{HUB_DIR}/{label}.md"


def hub_body(label: str, note_count: int) -> str:
    st = SUBTYPE_OF[label]
    now = datetime.now(timezone.utc)
    return (
        "---\n"
        f"created: {now.isoformat()}\n"
        "ingest_method: wikilink-retrofit\n"
        f"entity_type: {st}\n"
        f"tags: entity,{st}\n"
        "---\n\n"
        f"# {label}\n\n"
        f"{_HUB_DESCRIPTIONS[st]} Hub note created by the 2026-08-11\n"
        f"wikilink retrofit (mentioned in {note_count} vault notes at\n"
        "creation time). Backlinks to every mentioning note appear in\n"
        "Obsidian's backlinks pane / graph view; add durable facts about\n"
        "this entity here.\n"
    )


def build_footer(labels: list[str]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    links = " · ".join(f"[[{lbl}]]" for lbl in labels)
    return (
        f"\n\n{MARKER} v1 {now} second_brain.knowledge_graph.retrofit_links -->\n"
        f"**Linked:** {links}\n"
    )


def existing_link_targets(text: str) -> set:
    return {t.strip().lower() for t in WIKILINK_RE.findall(text)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report planned edits, write nothing")
    parser.add_argument("--limit", type=int, default=0,
                        help="only retrofit the first N eligible notes "
                             "(testing aid; hubs are still computed from "
                             "the full corpus)")
    args = parser.parse_args()

    sess = _session()
    all_paths = enumerate_vault(sess)
    notes = curated_paths(all_paths)
    print(f"curated notes in scope: {len(notes)}", flush=True)
    contents = fetch_all(sess, notes)

    # pass 1: census -- who mentions what, and how linked is the vault today
    per_note: dict[str, Counter] = {}
    entity_notes: dict[str, set] = defaultdict(set)
    organic_links = 0
    notes_with_links = 0
    for p, text in contents.items():
        found = WIKILINK_RE.findall(text)
        organic_links += len(found)
        if found:
            notes_with_links += 1
        ents = entity_mentions(text)
        if ents:
            per_note[p] = ents
            for lbl in ents:
                entity_notes[lbl].add(p)
    print(f"pre-retrofit census: {organic_links} [[wikilinks]] across "
          f"{notes_with_links}/{len(contents)} notes")

    hub_labels = sorted(lbl for lbl, ns in entity_notes.items()
                        if len(ns) >= MIN_NOTES)
    print(f"entities recurring in >={MIN_NOTES} notes: {len(hub_labels)}")

    # pass 2: entity hub notes (create-if-missing only).
    # PATH CONVENTION (bug caught 2026-08-11 on the first live run):
    # enumerate_vault()/fetch_all() speak BUSINESS_ROOT-relative paths, but
    # webdav_client.get/put expect ACCOUNT-root-relative paths -- every I/O
    # call below must prefix webdav_client.BUSINESS_ROOT, or writes land in
    # a stray parallel tree at the account root instead of inside the vault
    # (which is exactly what the first run did; strays flagged for operator
    # cleanup, same hand-recovery precedent as the 2026-08-09 postmortem).
    root = webdav_client.BUSINESS_ROOT
    hubs_created = 0
    existing_hub_files = {p for p in notes if p.startswith(f"{HUB_DIR}/")}
    for lbl in hub_labels:
        hp = hub_path(lbl)
        if (hp in existing_hub_files
                or webdav_client.get(f"{root}/{hp}") is not None):
            continue
        if not args.dry_run:
            webdav_client.put(f"{root}/{hp}",
                              hub_body(lbl, len(entity_notes[lbl])))
        hubs_created += 1
        print(f"{'would create' if args.dry_run else 'created'} hub: {hp}")

    # pass 3: per-note footers
    notes_edited = 0
    links_added = 0
    eligible = [p for p in sorted(per_note) if not p.startswith(f"{HUB_DIR}/")]
    for p in eligible:
        if args.limit and notes_edited >= args.limit:
            break
        text = contents[p]
        if MARKER in text:
            continue  # already retrofitted (idempotency)
        already = existing_link_targets(text)
        labels = [lbl for lbl, _n in per_note[p].most_common()
                  if lbl in hub_labels and lbl.lower() not in already]
        labels = labels[:MAX_FOOTER_LINKS]
        if not labels:
            continue
        if not args.dry_run:
            # account-root-relative path -- see PATH CONVENTION note above
            webdav_client.put(f"{root}/{p}",
                              text.rstrip("\n") + build_footer(labels))
        notes_edited += 1
        links_added += len(labels)

    verb = "would edit" if args.dry_run else "edited"
    print(f"{verb} {notes_edited} notes (+{links_added} links), "
          f"{'would create' if args.dry_run else 'created'} "
          f"{hubs_created} entity hubs")
    print("post-retrofit expectation: "
          f"{organic_links + links_added} total wikilinks; rebuild the graph "
          "with: python3 -m second_brain.knowledge_graph.build_graph")
    return 0


if __name__ == "__main__":
    sys.exit(main())
