"""
second_brain.semantic.compile -- materialise the semantic layer into the live
vault index, and assign concepts to real notes.

WHY MATERIALISE AT ALL
----------------------
model.py already answers every semantic question in Python. Compiling the same
answers into SQLite tables inside /var/lib/corporatetraveldc/second_brain_index.db
buys the one thing a Python API cannot: consumption by things that are not
Python. A shell script, a future Rust rewrite, a Grafana panel, a different
vendor's agent, or a plain `sqlite3` one-liner from an SSH session can all join
`semantic_note_concepts` against `vault_notes_fts` with no import, no
dependency, and no knowledge that this package exists. That is what makes the
layer interoperable rather than merely shared.

It also makes the metric definitions in ontology.json executable: several of
them are SQL over these tables, so "notes by domain" stops being a thing each
consumer re-derives (differently) and becomes one governed query.

TABLES WRITTEN (all prefixed semantic_, all DROP/CREATE on every compile)
------------------------------------------------------------------------
  semantic_meta             one row per compile: version, timestamp, counts
  semantic_facets           the concept schemes
  semantic_concepts         id, facet, pref_label, definition, broader,
                            in_domain, source, entity_subtype
  semantic_labels           concept_id -> surface form (pref/alt), normalized
  semantic_relations        concept_id -> related concept (typed edges)
  semantic_agents           producing skills/agents and their authority
  semantic_metrics          governed metric definitions incl. their SQL
  semantic_note_concepts    path -> concept, WITH the rule and evidence that
                            produced it (this is the audit trail; a wrong
                            assignment is debuggable, not mysterious)
  semantic_unmapped_tags    live tags no concept claims -- the governance
                            backlog, surfaced rather than swallowed

Full DROP/CREATE rather than incremental upsert is deliberate: the compile is
a pure function of (ontology.json, lexicon.py, current index contents), it runs
in well under a second on the real 5,700-document index, and rebuilding removes
any chance of a stale assignment surviving a vocabulary change. It writes ONLY
semantic_* tables and never touches vault_documents, vault_notes_fts or
vault_links, so a compile can never damage the vault index it reads.

WHY THE ASSIGNMENT IS DETERMINISTIC
-----------------------------------
No embeddings, no LLM classification. Every assignment is a literal match of a
normalized string against a declared surface form, a path prefix, or an ingest
method, plus transitive closure over broader/in_domain. Same standing bias as
lexicon.py and retrofit_links.py (docs/SECOND_BRAIN_STATUS.md): reviewable,
reproducible, and identical on identical input forever. Every row records
`rule` and `evidence` so a human can audit exactly why a note was filed.

Usage:
    python3 -m second_brain.semantic --compile
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from second_brain.semantic.model import SemanticModel, load, normalize

# Read the same env var index_db.py uses, with the same default -- but WITHOUT
# importing index_db, which raises RuntimeError at import time when
# NEXTCLOUD_ADMIN_USER is unset. The semantic layer must stay usable with no
# credentials in the environment.
INDEX_DB = os.environ.get(
    "SECOND_BRAIN_INDEX_DB",
    "/var/lib/corporatetraveldc/second_brain_index.db",
)

# Raw feed intake. Excluded from "curated" everywhere, because 4,540 of the
# vault's 5,706 documents live here and including them makes every corpus-level
# number describe an RSS reader instead of a second brain.
RSS_PREFIX_FRAGMENT = "/00-Inbox/rss/"

_TABLES = (
    "semantic_meta", "semantic_facets", "semantic_concepts", "semantic_labels",
    "semantic_relations", "semantic_agents", "semantic_metrics",
    "semantic_note_concepts", "semantic_unmapped_tags", "semantic_note_derivations",
)


def _create_schema(conn: sqlite3.Connection) -> None:
    for t in _TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.executescript("""
        CREATE TABLE semantic_meta (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE semantic_facets (
            id TEXT PRIMARY KEY, pref_label TEXT, definition TEXT,
            scope_note TEXT, polyhierarchy INTEGER
        );
        CREATE TABLE semantic_concepts (
            id TEXT PRIMARY KEY, facet TEXT NOT NULL, pref_label TEXT NOT NULL,
            definition TEXT, scope_note TEXT, broader TEXT, in_domain TEXT,
            source TEXT, entity_subtype TEXT
        );
        CREATE INDEX idx_semantic_concepts_facet ON semantic_concepts(facet);
        CREATE TABLE semantic_labels (
            concept_id TEXT NOT NULL, label TEXT NOT NULL,
            normalized TEXT NOT NULL, is_preferred INTEGER NOT NULL
        );
        CREATE INDEX idx_semantic_labels_norm ON semantic_labels(normalized);
        CREATE INDEX idx_semantic_labels_concept ON semantic_labels(concept_id);
        CREATE TABLE semantic_relations (
            source_id TEXT NOT NULL, predicate TEXT NOT NULL, target_id TEXT NOT NULL
        );
        CREATE INDEX idx_semantic_relations_source ON semantic_relations(source_id);
        CREATE TABLE semantic_agents (
            id TEXT PRIMARY KEY, pref_label TEXT, kind TEXT, writes_genre TEXT,
            writes_provenance TEXT, authority TEXT, domains TEXT
        );
        CREATE TABLE semantic_metrics (
            id TEXT PRIMARY KEY, pref_label TEXT, definition TEXT,
            scope_note TEXT, unit TEXT, grain TEXT, sql TEXT
        );
        -- 2026-08-23 FIXED: PK used to be (path, concept_id, rule) only --
        -- narrower than assign()'s real 6-tuple dedup key, so when several
        -- DIFFERENT tags/paths/etc justified the same concept under the
        -- same rule (e.g. a note tagged both "aam" and "vertiport", both
        -- mapping to advanced_air_mobility via tag_exact), INSERT OR IGNORE
        -- silently kept only the first evidence and dropped the rest --
        -- 983 rows lost this way in the last compile before the fix,
        -- confirmed live (root-caused in docs/SECOND_BRAIN_STATUS.md).
        -- `evidence` is now part of the PK so every justification survives,
        -- honoring assign()'s own documented guarantee that `SELECT rule,
        -- evidence FROM semantic_note_concepts WHERE path=?` fully explains
        -- a filing.
        CREATE TABLE semantic_note_concepts (
            path TEXT NOT NULL, concept_id TEXT NOT NULL, facet TEXT NOT NULL,
            rule TEXT NOT NULL, evidence TEXT NOT NULL, is_curated INTEGER NOT NULL,
            PRIMARY KEY (path, concept_id, rule, evidence)
        );
        CREATE INDEX idx_snc_concept ON semantic_note_concepts(concept_id);
        CREATE INDEX idx_snc_path ON semantic_note_concepts(path);
        CREATE INDEX idx_snc_facet ON semantic_note_concepts(facet);
        CREATE TABLE semantic_unmapped_tags (
            tag TEXT PRIMARY KEY, occurrences INTEGER NOT NULL
        );
        -- 2026-08-23 FIXED: same PK-narrower-than-dedup-key bug as
        -- semantic_note_concepts above, plus target used to be truncated
        -- to 200 chars while being part of the PK (two different long
        -- targets sharing the same first 200 chars silently collided).
        -- target is no longer truncated at all, and evidence is now in
        -- the PK too -- two differently-worded Provenance lines that
        -- happen to name the same [[target]] are two real, distinct
        -- justifications, not duplicates.
        --
        -- 2026-08-24 REDESIGNED per operator directive: chronology
        -- (timestamp-only precedence, no authored evidence) was first
        -- built as a totally separate table/command -- wrong. The
        -- operator's own words: "I don't want it as a second mechanism...
        -- I want it baked into the causal chain. It's the whole point."
        -- One graph, one traversal (trace_causal_chain() below), for
        -- every note in the vault, old or new. `kind` is what preserves
        -- honesty inside that one graph instead of via separate storage:
        -- 'evidenced' = a real, authored ## Provenance line (relation
        -- leans_on/derives_from/reutilizes); 'chronological' = inferred
        -- purely from shared-concept + real timestamp order (relation
        -- always 'preceded_by'), written by assign_chronology() below
        -- for every note that lacks (or predates) an authored claim.
        -- Never conflated: a consumer reads `kind` off the SAME row
        -- returned by the SAME query, rather than needing to know a
        -- second table/command exists at all.
        CREATE TABLE semantic_note_derivations (
            path TEXT NOT NULL, relation TEXT NOT NULL, target TEXT NOT NULL,
            evidence TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'evidenced',
            PRIMARY KEY (path, relation, target, evidence)
        );
        CREATE INDEX idx_snd_path ON semantic_note_derivations(path);
        CREATE INDEX idx_snd_relation ON semantic_note_derivations(relation);
        CREATE INDEX idx_snd_target ON semantic_note_derivations(target);
        CREATE INDEX idx_snd_kind ON semantic_note_derivations(kind);
    """)


def _write_model(conn: sqlite3.Connection, m: SemanticModel) -> None:
    for f in m.facets():
        conn.execute(
            "INSERT INTO semantic_facets VALUES (?,?,?,?,?)",
            (f.id, f.pref_label, f.definition, f.scope_note, int(f.polyhierarchy)),
        )
    for c in m.concepts():
        conn.execute(
            "INSERT INTO semantic_concepts VALUES (?,?,?,?,?,?,?,?,?)",
            (c.id, c.facet, c.pref_label, c.definition, c.scope_note,
             c.broader, c.in_domain, c.source, c.entity_subtype),
        )
        seen: set[tuple[str, str]] = set()
        for form in c.surface_forms():
            n = normalize(form)
            if not n or (form, n) in seen:
                continue
            seen.add((form, n))
            conn.execute(
                "INSERT INTO semantic_labels VALUES (?,?,?,?)",
                (c.id, form, n, int(form == c.pref_label)),
            )
        if c.broader:
            conn.execute("INSERT INTO semantic_relations VALUES (?,?,?)",
                         (c.id, "broader", c.broader))
        if c.in_domain:
            conn.execute("INSERT INTO semantic_relations VALUES (?,?,?)",
                         (c.id, "in_domain", c.in_domain))
        for r in c.related:
            conn.execute("INSERT INTO semantic_relations VALUES (?,?,?)",
                         (c.id, "related", r))
    for a in m.agents():
        conn.execute("INSERT INTO semantic_agents VALUES (?,?,?,?,?,?,?)",
                     (a.id, a.pref_label, a.kind, a.writes_genre,
                      a.writes_provenance, a.authority, a.domains))
    for mt in m.metrics():
        conn.execute("INSERT INTO semantic_metrics VALUES (?,?,?,?,?,?,?)",
                     (mt.id, mt.pref_label, mt.definition, mt.scope_note,
                      mt.unit, mt.grain, mt.sql))


def _is_curated(path: str) -> bool:
    return RSS_PREFIX_FRAGMENT not in path


# The vault index is in rollback-journal mode (`PRAGMA journal_mode` = delete)
# with `busy_timeout` = 0, not WAL like the main corporatetraveldc.db. That
# combination means a writer takes an exclusive whole-database lock and any
# concurrent writer fails IMMEDIATELY with "database is locked" instead of
# retrying. index_note() is called by live ingest skills at unpredictable
# times, so a compile that holds the write lock while it thinks is a real way
# to make an unrelated skill's vault write fail.
#
# Two mitigations, both here rather than in index_db.py (which is the other
# module's schema authority and is not this change's to alter):
#   * a generous busy_timeout on OUR connection, so we wait for a skill
#     instead of erroring;
#   * all reading and all computation finish BEFORE any DDL or INSERT runs,
#     so the exclusive lock is held only for the insert burst rather than for
#     the whole compile.
BUSY_TIMEOUT_MS = int(os.environ.get("SECOND_BRAIN_SEMANTIC_BUSY_TIMEOUT_MS", "30000"))


def _connect(db_path: str | None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or INDEX_DB, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def assign(conn: sqlite3.Connection, m: SemanticModel) -> dict:
    """Assign concepts to every note in the live index.

    Four direct rules then one closure pass, exactly as declared in
    ontology.json's assignment_rules. Each produces rows carrying the rule id
    and the literal evidence string that matched, so `SELECT rule, evidence
    FROM semantic_note_concepts WHERE path=?` fully explains any filing.

    Reads its surface-form index from semantic_labels, so it must run after
    _write_model(). compile_layer() gets the lock-hold window down a different
    way -- see _read_corpus() -- by pulling the vault-side rows before any
    write begins.
    """
    # Normalised surface form -> concept ids, built once.
    by_form: dict[str, list[str]] = {}
    for cid, norm in conn.execute("SELECT concept_id, normalized FROM semantic_labels"):
        by_form.setdefault(norm, []).append(cid)

    # Path prefixes and ingest methods declared as evidence on concepts.
    path_rules: list[tuple[str, str]] = []      # (vault path fragment, concept id)
    ingest_rules: list[tuple[str, str]] = []    # (ingest_method, concept id)
    for c in m.concepts():
        for p in c.observed_as.get("vault_paths", []) or []:
            path_rules.append((p.rstrip("*"), c.id))
        for im in c.observed_as.get("ingest_methods", []) or []:
            ingest_rules.append((im, c.id))

    rows: set[tuple] = set()
    facet_of = {c.id: c.facet for c in m.concepts()}

    def add(path: str, cid: str, rule: str, evidence: str) -> None:
        rows.add((path, cid, facet_of[cid], rule, evidence, int(_is_curated(path))))

    # --- rule: tag_exact -------------------------------------------------
    tag_counts: dict[str, int] = {}
    unmapped: dict[str, int] = {}
    for path, tags in conn.execute("SELECT path, tags FROM vault_notes_fts"):
        for raw in (tags or "").split(","):
            raw = raw.strip()
            if not raw:
                continue
            tag_counts[raw] = tag_counts.get(raw, 0) + 1
            n = normalize(raw)
            hits = by_form.get(n)
            if hits:
                for cid in hits:
                    add(path, cid, "tag_exact", raw)
            else:
                unmapped[raw] = unmapped.get(raw, 0) + 1

    # --- rules: path_prefix + ingest_method -------------------------------
    for path, ingest in conn.execute(
            "SELECT path, COALESCE(ingest_method,'') FROM vault_documents"):
        for frag, cid in path_rules:
            if frag and frag in path:
                add(path, cid, "path_prefix", frag)
        if ingest:
            for im, cid in ingest_rules:
                if im == ingest:
                    add(path, cid, "ingest_method", ingest)

    # --- rule: title_entity -----------------------------------------------
    # Lexicon regexes over the TITLE only. Body matching is retrofit_links.py's
    # job and running it here too would double-count the same evidence in two
    # different systems.
    try:
        from second_brain.knowledge_graph.lexicon import LEXICON
    except Exception:
        LEXICON = []
    if LEXICON:
        label_to_cid: dict[str, str] = {}
        for c in m.concepts():
            lex = c.observed_as.get("lexicon_label")
            if lex:
                label_to_cid[lex] = c.id
        for label, cid in m.lexicon_crosswalk.items():
            label_to_cid.setdefault(label, cid)
        for path, title in conn.execute("SELECT path, title FROM vault_notes_fts"):
            if not title:
                continue
            for label, _subtype, pattern in LEXICON:
                cid = label_to_cid.get(label)
                if cid and pattern.search(title):
                    add(path, cid, "title_entity", label)

    # --- rule: broader_closure -------------------------------------------
    direct = list(rows)
    for path, cid, _facet, _rule, _ev, _cur in direct:
        for anc in m.closure(cid)[1:]:
            add(path, anc, "broader_closure", cid)

    conn.executemany(
        "INSERT OR IGNORE INTO semantic_note_concepts VALUES (?,?,?,?,?,?)",
        sorted(rows))
    conn.executemany(
        "INSERT OR REPLACE INTO semantic_unmapped_tags VALUES (?,?)",
        sorted(unmapped.items()))

    return {
        "assignments": len(rows),
        "distinct_tags": len(tag_counts),
        "unmapped_tags": len(unmapped),
        "mapped_tags": len(tag_counts) - len(unmapped),
    }


_PROVENANCE_HEADING_RE = re.compile(r"^##\s+Provenance\s*$")
_PROVENANCE_LINE_RE = re.compile(
    r"^[\s\-\*]*"
    r"(?P<label>Leaned on|Derived|Reutilized)\s*:\s*(?P<rest>.+)$"
)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)")

_DERIVATION_RELATION = {
    "Leaned on": "leans_on",
    "Derived": "derives_from",
    "Reutilized": "reutilizes",
}


# 2026-08-23: FIXED -- the terminator used to be a literal `line.startswith("## ")`
# check, so it only ever matched an EXACT h2. A `#` (h1 -- shallower than the
# Provenance heading itself, unambiguously means the section ended) or a
# `###`+ (deeper -- should terminate too only if it's actually h1/h2; a truly
# deeper heading is a SUBSECTION of Provenance under normal markdown nesting
# and correctly stays in-section) fell through un-terminated in the old code
# for the wrong reason (string-prefix matching, not heading-level awareness).
# Reproduced live: a `### Sub Heading` inside the section did not end it
# under the old code, and a bare `# Heading` also didn't (starts with "# ",
# not "## "). Fixed to be level-aware: terminates on any heading of level 1
# or 2 (`#`, `##` -- equal to or shallower than Provenance's own h2), never
# on level 3+ (deeper headings nest *within* Provenance, they don't end it)
# and never on a bare "#" used as prose (e.g. a hashtag) since a real ATX
# heading requires whitespace (or end-of-line) right after the hash run.
_HEADING_TERMINATOR_RE = re.compile(r"^#{1,2}(?!#)(?:\s|$)")


def _provenance_lines(content: str) -> list[str]:
    """Lines inside a note's `## Provenance` section, up to the next heading
    of level 1 or 2 (see _HEADING_TERMINATOR_RE) or end of content.
    Deterministic line-scan, no markdown parser -- matches this module's
    existing bias against anything but literal string/regex matching."""
    lines = (content or "").splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        if _PROVENANCE_HEADING_RE.match(line.strip("\n")):
            in_section = True
            continue
        if in_section:
            if _HEADING_TERMINATOR_RE.match(line):
                break
            out.append(line)
    return out


def assign_derivations(conn: sqlite3.Connection) -> int:
    """Rule `derivation_provenance` (ontology.json assignment_rules): parse
    each note's `## Provenance` section (see docs/SECOND_BRAIN_STATUS.md for
    the format) and record note-to-note/note-to-mechanism causal edges in
    semantic_note_derivations, kind='evidenced'. Separate from assign()
    because the target is whatever the line names -- often a [[wikilink]],
    sometimes free text -- not a controlled-vocabulary concept id, so this
    does not belong in semantic_note_concepts alongside the tag/path/
    ingest-method rules. See assign_chronology() below for the other kind
    of row this table holds."""
    rows: set[tuple[str, str, str, str, str]] = set()
    for path, content in conn.execute("SELECT path, content FROM vault_notes_fts"):
        for raw_line in _provenance_lines(content or ""):
            m = _PROVENANCE_LINE_RE.match(raw_line.strip())
            if not m:
                continue
            relation = _DERIVATION_RELATION[m.group("label")]
            rest = m.group("rest").strip()
            evidence = raw_line.strip()
            targets = _WIKILINK_RE.findall(rest)
            if targets:
                for t in targets:
                    rows.add((path, relation, t.strip(), evidence, "evidenced"))
            elif rest:
                rows.add((path, relation, rest, evidence, "evidenced"))

    conn.executemany(
        "INSERT OR IGNORE INTO semantic_note_derivations VALUES (?,?,?,?,?)",
        sorted(rows))
    return len(rows)


def _resolve_derivation_target(conn: sqlite3.Connection, target: str) -> str | None:
    """Best-effort resolve a derivation edge's free-form `target` to a real
    vault_documents.path, for graph traversal. Two matches only, both
    exact -- same deterministic, no-fuzzy-matching bias as the rest of
    this module: the target is already a full path, or it's a bare name
    matching some note's basename (the common case today -- most
    `[[wikilink]]` targets are dated note stems like "20260823T064349Z").
    An unresolved target (free text, a code filename, an unwritten note)
    is a legitimate, expected terminal, not an error -- as of 2026-08-23
    the majority of derivation edges (41 of 50) are free-text targets by
    design (the operator's own directive was "leaned on / derived /
    reutilized" as prose, not a mandate that everything link), and
    trace_causal_chain() surfaces those as leaf evidence rather than
    silently dropping them."""
    row = conn.execute(
        "SELECT path FROM vault_documents WHERE path = ? OR path LIKE ('%/' || ? || '.md') LIMIT 1",
        (target, target)
    ).fetchone()
    return row[0] if row else None


def trace_causal_chain(start: str, direction: str = "backward",
                       max_depth: int = 5, db_path: str | None = None) -> dict:
    """Causal-reasoning traversal over semantic_note_derivations -- ONE
    table, ONE traversal, for every note in the vault, whether its edges
    are kind='evidenced' (a real, authored ## Provenance line) or
    kind='chronological' (assign_chronology()'s timestamp-only
    preceded_by edges, for the ~7,000+ notes that predate this facet or
    simply never got a Provenance section). Operator directive,
    2026-08-24, after an earlier revision of this feature shipped
    chronology as a second, disconnected table/command: "I don't want it
    as a second mechanism... I want it baked into the causal chain."
    `kind` on every returned edge is what keeps that honest WITHOUT a
    second query surface -- a caller reads it off the same row, not off
    which command they happened to run.

    direction="backward" (default) -- "what led to this": starting at
    `start` (a vault path, or a bare note-stem that resolves to one),
    follow every outgoing edge (evidenced AND chronological alike).
    Each edge whose target resolves to a real note is recursed into (up
    to max_depth); an unresolved target is recorded as a terminal leaf.
    Cycle-safe via a visited set -- a note that transitively derived
    from itself, however unlikely, cannot loop forever.

    direction="forward" -- "what depends on this": the reverse edge --
    every row anywhere in the vault whose target resolves to `start`
    (or, if `start` itself never resolves to a real note -- e.g. tracing
    forward from a code filename or a named mechanism rather than a
    note -- every row whose target textually equals `start`). Also
    recursive and cycle-safe, so "what depends on what depends on this"
    chains correctly.

    Returns {"root": start, "direction": ..., "max_depth": ...,
    "edges": [{"depth", "from"/"path", "relation", "target",
    "resolved_path", "evidence", "kind"}, ...]} -- deliberately a flat
    edge list with depth annotations rather than a nested tree, so both
    a CLI printer and a future consumer (e.g. an auto-selfheal query,
    per the operator's stated end-goal) can walk it without depending on
    this module's own tree-shape choices."""
    conn = _connect(db_path)
    try:
        edges: list[dict] = []
        visited: set[str] = set()

        def _backward(cur: str, depth: int) -> None:
            if depth > max_depth or cur in visited:
                return
            visited.add(cur)
            rows = conn.execute(
                "SELECT relation, target, evidence, kind FROM semantic_note_derivations "
                "WHERE path = ? ORDER BY kind, relation, target", (cur,)
            ).fetchall()
            for relation, target, evidence, kind in rows:
                resolved = _resolve_derivation_target(conn, target)
                edges.append({
                    "depth": depth, "from": cur, "relation": relation,
                    "target": target, "resolved_path": resolved,
                    "evidence": evidence, "kind": kind,
                })
                if resolved:
                    _backward(resolved, depth + 1)

        def _forward(node_id: str, depth: int) -> None:
            if depth > max_depth or node_id in visited:
                return
            visited.add(node_id)
            all_rows = conn.execute(
                "SELECT path, relation, target, evidence, kind FROM semantic_note_derivations "
                "ORDER BY path, relation"
            ).fetchall()
            for src_path, relation, target, evidence, kind in all_rows:
                resolved = _resolve_derivation_target(conn, target)
                if (resolved or target) != node_id:
                    continue
                edges.append({
                    "depth": depth, "path": src_path, "relation": relation,
                    "target": target, "evidence": evidence, "kind": kind,
                })
                _forward(src_path, depth + 1)

        if direction == "backward":
            root = _resolve_derivation_target(conn, start) or start
            _backward(root, 0)
        else:
            root = _resolve_derivation_target(conn, start) or start
            _forward(root, 0)

        return {"root": start, "resolved_root": root if root != start else None,
                "direction": direction, "max_depth": max_depth, "edges": edges}
    finally:
        conn.close()


def assign_chronology(conn: sqlite3.Connection) -> int:
    """Deterministic, timestamp-only temporal-precedence linking -- writes
    INTO semantic_note_derivations (relation='preceded_by',
    kind='chronological'), the SAME table and SAME graph
    trace_causal_chain() already walks. Ships with the platform, computed
    fresh from whatever real vault_documents/semantic_note_concepts a
    deployment actually has.

    Operator directive, 2026-08-24: this was originally a separate table
    with its own CLI command -- wrong, and corrected the same day. "I
    don't want it as a second mechanism... I want it baked into the
    causal chain. It's the whole point." One graph now; `kind` is what
    keeps a chronological edge from ever being mistaken for a verified
    leans_on/derives_from/reutilizes claim, not a second query surface
    nobody thinks to check.

    For every (note, concept) pair in semantic_note_concepts, finds that
    note's immediate chronological predecessor -- the most recent OTHER
    note sharing the same concept -- and a 1-indexed sequence number
    within that concept's whole history to date ("this is the Nth note
    ever indexed under this concept"), packed into `evidence` as
    "concept=<id>;seq=<n>;ts=<real timestamp>" so concept_history() below
    can recover them without a dedicated column. This is what makes "how
    many times has this happened" and "what was the prior note on this
    topic" answerable for EVERY note, including the ~7,000+ that predate
    this facet's own existence and will never have an authored
    ## Provenance section -- those notes get chronological-only edges
    forever in the SAME table, which is honest: nobody can retroactively
    verify what they actually leaned on, but they still show up when you
    --trace them, instead of --trace returning nothing for 99% of the
    vault.

    Ordering source, deliberately NOT vault_documents.indexed_at: that
    column is when the file was last (re-)scanned into the index, which
    clusters into a handful of batch-scan runs (1,482 distinct values
    across 7,132 notes, live-checked 2026-08-24) -- most of this vault's
    history would tie on it. vault_documents.mtime is the real file
    modification time and is properly fine-grained (6,802 distinct
    values), but arrives as an RFC 2822 string ("Tue, 11 Aug 2026
    22:41:59 GMT") that SQLite cannot sort correctly as text (the
    day-of-week/month-name prefix breaks lexicographic date order
    entirely) -- so this parses it in Python via email.utils.
    parsedate_to_datetime() and sorts on the real datetime. A note with
    a missing or unparseable mtime falls back to indexed_at rather than
    being dropped."""
    conn.execute("DELETE FROM semantic_note_derivations WHERE kind='chronological'")
    raw = conn.execute("""
        SELECT sc.path, sc.concept_id, vd.mtime, vd.indexed_at
        FROM semantic_note_concepts sc
        JOIN vault_documents vd ON vd.path = sc.path
        GROUP BY sc.path, sc.concept_id
    """).fetchall()

    def _sort_ts(mtime: str | None, indexed_at: str | None) -> tuple[datetime, str]:
        if mtime:
            try:
                dt = parsedate_to_datetime(mtime)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt, mtime
            except (TypeError, ValueError):
                pass
        try:
            return datetime.fromisoformat(indexed_at), indexed_at
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc), indexed_at or ""

    resolved = sorted(
        ((path, concept_id, *_sort_ts(mtime, indexed_at))
         for path, concept_id, mtime, indexed_at in raw),
        key=lambda r: (r[1], r[2], r[0]),  # concept_id, real timestamp, path
    )

    out: list[tuple] = []
    prev_path: dict[str, str] = {}
    seq: dict[str, int] = {}
    for path, concept_id, _dt, ts_str in resolved:
        n = seq.get(concept_id, 0) + 1
        seq[concept_id] = n
        # target is NOT NULL -- the first occurrence of a concept has no
        # real predecessor, so it gets an explicit sentinel rather than
        # being silently dropped from the table (which would make
        # concept_history() undercount by exactly one per concept and
        # --trace show nothing for the note that actually started a topic).
        # "(first...)" can never collide with a real vault path.
        predecessor = prev_path.get(concept_id, "(first on record)")
        evidence = f"concept={concept_id};seq={n};ts={ts_str}"
        out.append((path, "preceded_by", predecessor, evidence, "chronological"))
        prev_path[concept_id] = path

    conn.executemany(
        "INSERT OR IGNORE INTO semantic_note_derivations VALUES (?,?,?,?,?)", out)
    return len(out)


_CHRONOLOGY_EVIDENCE_RE = re.compile(r"^concept=(?P<concept>.+);seq=(?P<seq>\d+);ts=(?P<ts>.*)$")


def concept_history(concept_id: str, limit: int | None = None,
                    db_path: str | None = None) -> dict:
    """'git blame'-style ordered history for a concept -- every note ever
    filed under it, in real timestamp order, each carrying its own
    sequence number and immediate predecessor (target). Read-only query
    over the SAME semantic_note_derivations table trace_causal_chain()
    walks, filtered to this concept's kind='chronological' rows; pair
    with trace_causal_chain() for "what actually caused what" on any
    note in this list that also has real ## Provenance evidence -- both
    now come from one graph, not two."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT path, target, evidence FROM semantic_note_derivations "
            "WHERE relation='preceded_by' AND kind='chronological' "
            "AND evidence LIKE ?",
            (f"concept={concept_id};%",)
        ).fetchall()
        parsed = []
        for path, predecessor, evidence in rows:
            m = _CHRONOLOGY_EVIDENCE_RE.match(evidence)
            if not m or m.group("concept") != concept_id:
                continue
            parsed.append({"path": path, "sequence": int(m.group("seq")),
                          "preceded_by": predecessor, "ts": m.group("ts")})
        parsed.sort(key=lambda h: h["sequence"])
        total = conn.execute(
            "SELECT COUNT(*) FROM semantic_note_concepts WHERE concept_id = ?",
            (concept_id,)).fetchone()[0]
        if limit:
            parsed = parsed[:limit]
        return {"concept_id": concept_id, "total_occurrences": total, "history": parsed}
    finally:
        conn.close()


def compile_layer(db_path: str | None = None,
                  ontology_path: str | None = None) -> dict:
    m = load(ontology_path)
    conn = _connect(db_path)
    try:
        # Touch every vault-side table we depend on BEFORE taking the write
        # lock, so a cold page cache is paid for under a shared read lock
        # rather than while a live ingest skill is blocked behind us.
        conn.execute("SELECT COUNT(*) FROM vault_notes_fts").fetchone()
        conn.execute("SELECT COUNT(*) FROM vault_documents").fetchone()
        _create_schema(conn)
        _write_model(conn, m)
        result = assign(conn, m)
        result["derivations"] = assign_derivations(conn)
        result["chronology_edges"] = assign_chronology(conn)

        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "semantic_layer_version": m.version,
            "compiled_at": now,
            "namespace": m.namespace,
            **{k: str(v) for k, v in m.stats().items()},
            **{k: str(v) for k, v in result.items()},
        }
        conn.executemany("INSERT OR REPLACE INTO semantic_meta VALUES (?,?)",
                         sorted(meta.items()))
        conn.commit()
    finally:
        conn.close()
    result["compiled_at"] = now
    result["version"] = m.version
    return result


def evaluate_metrics(db_path: str | None = None,
                     ontology_path: str | None = None) -> list[dict]:
    """Run every governed metric against the live index.

    A metric that fails is reported with its error rather than crashing the
    run: a broken metric definition is a governance finding, not a reason the
    other nine become unavailable.
    """
    m = load(ontology_path)
    conn = _connect(db_path)
    out: list[dict] = []
    try:
        for metric in m.metrics():
            entry = {"id": metric.id, "label": metric.pref_label,
                     "unit": metric.unit, "grain": metric.grain}
            try:
                rowsx = conn.execute(metric.sql).fetchall()
                if metric.grain == "concept":
                    entry["value"] = [list(r) for r in rowsx]
                else:
                    entry["value"] = rowsx[0][0] if rowsx and rowsx[0] else None
            except sqlite3.Error as e:
                entry["error"] = str(e)
            out.append(entry)
    finally:
        conn.close()
    return out


def semantic_search(query: str, limit: int = 10,
                    db_path: str | None = None,
                    ontology_path: str | None = None,
                    curated_only: bool = True) -> dict:
    """Concept-expanded full-text search over the vault.

    The comparison that justifies the whole layer: run the user's literal term
    through FTS5, then run every surface form of the concept it names, and
    report both counts. `naive_hits` is what the vault could find before;
    `hits` is what it can find now.
    """
    m = load(ontology_path)
    concept = m.resolve(query)
    conn = _connect(db_path)
    # Counted in SQL rather than by measuring a fetched page: an earlier
    # revision fetched up to 400 rows and reported len(), which silently
    # capped both figures and understated the very gain this function exists
    # to demonstrate.
    where_curated = ("AND path NOT LIKE '%" + RSS_PREFIX_FRAGMENT + "%'"
                     if curated_only else "")
    try:
        def _count(expr: str) -> int:
            if not expr:
                return 0
            try:
                return conn.execute(
                    "SELECT COUNT(*) FROM vault_notes_fts "
                    f"WHERE vault_notes_fts MATCH ? {where_curated}",
                    (expr,)).fetchone()[0]
            except sqlite3.OperationalError:
                # Malformed FTS5 expression (e.g. a bare hyphen read as a
                # column filter) -- report zero rather than crashing the
                # comparison this function exists to make.
                return 0

        expr_expanded = m.fts_query(query) if concept else f'"{query}"'
        naive_n = _count(f'"{query}"')
        exp_n = _count(expr_expanded)

        rows: list[tuple] = []
        if expr_expanded:
            try:
                rows = conn.execute(
                    "SELECT path, title, "
                    "snippet(vault_notes_fts, 2, '**','**','...',18) "
                    "FROM vault_notes_fts "
                    f"WHERE vault_notes_fts MATCH ? {where_curated} "
                    "ORDER BY rank LIMIT ?", (expr_expanded, limit)).fetchall()
            except sqlite3.OperationalError:
                rows = []

        return {
            "query": query,
            "concept": concept.id if concept else None,
            "concept_label": concept.pref_label if concept else None,
            "facet": concept.facet if concept else None,
            "surface_forms": m.expand(query, for_search=True) if concept else [],
            "naive_hits": naive_n,
            "hits": exp_n,
            "curated_only": curated_only,
            "results": [{"path": p, "title": t, "snippet": s}
                        for p, t, s in rows],
        }
    finally:
        conn.close()


def drift_report(db_path: str | None = None,
                 ontology_path: str | None = None) -> dict:
    """Governance check: is the declared vocabulary still true of the live system?

    Three questions, all of which have silently gone wrong here before:
      1. Does the layer still cover everything shared/rss_catalog.py's alias
         clusters cover? It is required to be a strict superset; rss_catalog
         stays the live PWA authority and is never edited from here, so any
         divergence must be reported, not patched.
      2. Which live tags does no concept claim? Each is either a missing
         concept or a writer emitting a typo.
      3. Which curated lexicon entities have no domain?
    """
    m = load(ontology_path)
    report: dict = {"version": m.version, "rss_catalog": {}, "unmapped_tags": [],
                    "entities_without_domain": m.unmapped_entities,
                    "stale_entity_domain_keys": m.stale_entity_domain_keys}

    try:
        from shared.rss_catalog import _CONCEPT_ALIASES, _RSS_CATALOG
    except Exception as e:
        report["rss_catalog"]["error"] = f"could not import shared.rss_catalog: {e}"
        _CONCEPT_ALIASES, _RSS_CATALOG = {}, {}

    missing_categories, missing_aliases = [], []
    for key in _RSS_CATALOG:
        c = m.resolve(key)
        if not c or c.facet != "domain":
            missing_categories.append(key)
    for canonical, aliases in _CONCEPT_ALIASES.items():
        target = m.resolve(canonical)
        for a in sorted(aliases):
            got = m.resolve(a)
            if not got or (target and got.id != target.id
                           and target.id not in m.closure(got.id)):
                missing_aliases.append(
                    {"alias": a, "rss_canonical": canonical,
                     "resolved_to": got.id if got else None})
    report["rss_catalog"] = {
        "categories": len(_RSS_CATALOG),
        "categories_not_covered": missing_categories,
        "aliases_not_covered": missing_aliases,
        "is_superset": not missing_categories and not missing_aliases,
    }

    # Aliases this layer has that rss_catalog does not -- the reverse direction,
    # i.e. improvements this layer makes that rss_catalog would still miss.
    try:
        from shared.rss_catalog import canonical_concept
        gained = []
        for c in m.concepts(facet="domain"):
            for form in c.surface_forms():
                if canonical_concept(form) is None:
                    gained.append(form)
        report["rss_catalog"]["forms_this_layer_adds"] = len(set(gained))
    except Exception:
        pass

    conn = _connect(db_path)
    try:
        rowsx = conn.execute(
            "SELECT tag, occurrences FROM semantic_unmapped_tags "
            "ORDER BY occurrences DESC").fetchall()
        report["unmapped_tags"] = [{"tag": t, "occurrences": n} for t, n in rowsx]
    except sqlite3.Error:
        report["unmapped_tags"] = [{"error": "not compiled yet -- run --compile"}]
    finally:
        conn.close()
    return report


if __name__ == "__main__":  # pragma: no cover - convenience only
    print(json.dumps(compile_layer(), indent=2))
