"""
second_brain.semantic.compile -- materialise the semantic layer into the live
vault index, and assign concepts to real notes.

WHY MATERIALISE AT ALL
----------------------
model.py already answers every semantic question in Python. Compiling the same
answers into tables in the corporatetraveldc-pgsql Postgres instance
(pg_schema/0055_second_brain_index.sql -- moved from a standalone SQLite file
2026-09-19, docs/POSTGRES_MIGRATION.md sec2) buys the one thing a Python API
cannot: consumption by things that are not Python. A shell script, a future
Rust rewrite, a Grafana panel, a different vendor's agent, or a plain `psql`
one-liner from an SSH session can all join `semantic_note_concepts` against
`vault_notes_fulltext` with no import, no dependency, and no knowledge that
this package exists. That is what makes the layer interoperable rather than
merely shared.

It also makes the metric definitions in ontology.json executable: several of
them are SQL over these tables, so "notes by domain" stops being a thing each
consumer re-derives (differently) and becomes one governed query.

TABLES WRITTEN (all prefixed semantic_, all TRUNCATEd and rewritten every compile)
-----------------------------------------------------------------------------------
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
  semantic_note_instance_refs  path -> real flight/train instance a note
                            references (2026-09-20, geometric reasoning
                            Phase 0), regex-extracted, with resolved
                            lat/lon where live position data exists

Full TRUNCATE-and-rewrite rather than incremental upsert is deliberate: the
compile is a pure function of (ontology.json, lexicon.py, current index
contents), it runs well under a second against Postgres on the real
5,700+-document index, and rebuilding removes any chance of a stale
assignment surviving a vocabulary change. It writes ONLY semantic_* tables
and never touches vault_documents, vault_notes_fulltext or vault_links, so
a compile can never damage the vault index it reads.

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
import math
import os
import bisect
import re
import time
import sqlite3  # only for isinstance() in _create_schema()'s test-double branch -- see its docstring
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import psycopg

from common import db_backend
from second_brain.semantic.model import SemanticModel, load, normalize

# 2026-09-19: the vault index (and this semantic layer materialized on top
# of it) moved from a standalone SQLite file to the corporatetraveldc-pgsql
# Postgres instance (pg_schema/0055_second_brain_index.sql) --
# docs/POSTGRES_MIGRATION.md sec2's JOIN exception (geometric reasoning
# needs to JOIN semantic_note_derivations against flight/vault data; see
# db_backend.py's REFERENCE_TABLES comment for the same reasoning applied
# to the dispatch write path). _connect() below now wraps
# common.db_backend.pg_conn() directly -- NOT second_brain.index_db.get_conn(),
# on purpose: index_db raises RuntimeError at import time when
# NEXTCLOUD_ADMIN_USER is unset, and this module must stay usable with no
# credentials in the environment, same constraint as always.
INDEX_DB = os.environ.get(
    "SECOND_BRAIN_INDEX_DB",
    "/var/lib/corporatetraveldc/second_brain_index.db",
)  # vestigial (no longer read by _connect()); kept for anything still importing it for display/logging.

# Raw feed intake. Excluded from "curated" everywhere, because 4,540 of the
# vault's 5,706 documents live here and including them makes every corpus-level
# number describe an RSS reader instead of a second brain.
RSS_PREFIX_FRAGMENT = "/00-Inbox/rss/"

_TABLES = (
    "semantic_meta", "semantic_facets", "semantic_concepts", "semantic_labels",
    "semantic_relations", "semantic_agents", "semantic_metrics",
    "semantic_note_concepts", "semantic_unmapped_tags", "semantic_note_derivations",
    "semantic_note_instance_refs",
)

# Geometric reasoning Phase 0 (docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md).
# Default matches the design doc's own worked example ("threshold 25km/30min").
# Same config.get(...)-style env-var override as AMTRAK_CORE_ROUTES/
# AMTRAK_REGIONAL_STATIONS (src/poller/fetchers/amtrak.py) -- no hardcoded
# km constant baked into assign_geometry() itself.
def _geo_proximity_radius_km() -> float:
    try:
        return float(os.environ.get("GEO_PROXIMITY_RADIUS_KM", "25").strip())
    except (TypeError, ValueError):
        return 25.0


GEO_TIME_WINDOW_MIN = 30  # sibling-scaled from the disruption-digest's days=30 (decision #2)

# ICAO callsign shape: 3-letter airline prefix + 1-4 digit flight number,
# optional trailing letter (some codeshares/cargo append one, e.g. "179G"
# seen live in flight_events.flight_num). The full (airline, flight_num)
# pair is validated against flight_events' REAL live rows at call time
# (_valid_flight_pairs() below), not a hardcoded list and not prefix-only
# (2026-09-20: prefix-only validation let 'MAX8'/'NEW269' through as false
# positives -- see _valid_flight_pairs()'s docstring) -- this is what keeps
# the regex from matching arbitrary 3-letter+digits text (acronyms, model
# numbers, etc.) as a false positive flight reference.
_CALLSIGN_RE = re.compile(r"\b([A-Z]{3})\s?(\d{1,4}[A-Z]?)\b")

# Matches this platform's own existing convention for naming a train in text
# (see src/poller/skills/transport_pattern_digest.py: f"Train {x['train_number']}"),
# not the design doc's illustrative "AMTK2157" -- checked live, "AMTK"-prefixed
# train identifiers are not how this codebase actually renders them anywhere.
_TRAIN_RE = re.compile(r"\btrain\s+(\d{1,4})\b", re.IGNORECASE)


def _create_schema(conn) -> None:
    """2026-09-19: TRUNCATE, not DROP/CREATE, on the real (Postgres) path --
    the schema (tables + indexes) is now owned by
    pg_schema/0055_second_brain_index.sql, applied once by
    scripts/pg_migrate.py, same authority as every other Postgres table.
    Re-issuing this module's own copy of the DDL on every compile would
    risk silently drifting from that canonical schema (a missed index, a
    type change made in 0055 but not here) while buying nothing --
    TRUNCATE gives the exact same "no stale row survives a compile"
    guarantee the original DROP/CREATE was for, without owning a second
    copy of the DDL. Every original comment explaining a PK design choice
    (evidence in the PK, target never truncated, etc.) is still true and
    still enforced -- it just lives in 0055 now.

    The sqlite3.Connection branch below is test-only: tests/second_brain/
    test_semantic.py injects a raw, throwaway sqlite3 connection directly
    into this function (and assign()/_write_model()/etc. -- all of which
    take `conn` as an explicit parameter, not via _connect()) to get fast,
    isolated unit tests without touching the shared Postgres instance.
    That is a test-double substitution, not the app supporting two
    backends in production -- compile_layer() and every other real
    caller only ever reaches this through _connect(), which always wraps
    Postgres now (see its own docstring)."""
    if isinstance(conn, sqlite3.Connection):
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
            CREATE TABLE semantic_note_derivations (
                path TEXT NOT NULL, relation TEXT NOT NULL, target TEXT NOT NULL,
                evidence TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'evidenced',
                PRIMARY KEY (path, relation, target, evidence)
            );
            CREATE INDEX idx_snd_path ON semantic_note_derivations(path);
            CREATE INDEX idx_snd_relation ON semantic_note_derivations(relation);
            CREATE INDEX idx_snd_target ON semantic_note_derivations(target);
            CREATE INDEX idx_snd_kind ON semantic_note_derivations(kind);
            CREATE TABLE semantic_note_instance_refs (
                path TEXT NOT NULL, instance_type TEXT NOT NULL, identifier TEXT NOT NULL,
                note_ts TEXT NOT NULL, lat REAL, lon REAL, position_source TEXT,
                PRIMARY KEY (path, instance_type, identifier)
            );
            CREATE INDEX idx_snir_path ON semantic_note_instance_refs(path);
            CREATE INDEX idx_snir_type_id ON semantic_note_instance_refs(instance_type, identifier);
        """)
        return
    for t in _TABLES:
        conn.execute(f"TRUNCATE TABLE {t}")


def _write_model(conn, m: SemanticModel) -> None:
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


# HISTORICAL, no longer true -- kept because it explains why compile_layer()
# below still reads vault-side tables before writing, even though the
# original reason (avoiding a lock) no longer applies:
#
# The vault index used to be a standalone SQLite file in rollback-journal
# mode (`PRAGMA journal_mode` = delete) with `busy_timeout` = 0, not WAL
# like the main corporatetraveldc.db. That combination meant a writer took
# an exclusive whole-database lock and any concurrent writer failed
# IMMEDIATELY with "database is locked" instead of retrying. index_note()
# is called by live ingest skills at unpredictable times, so a compile
# holding the write lock while it thinks was a real way to make an
# unrelated skill's vault write fail -- this was mitigated with a generous
# busy_timeout plus finishing all reads/computation before any write.
#
# 2026-09-19: moot. Postgres's MVCC means readers and writers never block
# each other this way (docs/POSTGRES_MIGRATION.md sec1) -- there is no
# busy_timeout equivalent needed and no exclusive lock to hold. The
# read-before-write ORDERING is harmless to keep (and does, since
# compile_layer() still calls assign() etc. before _create_schema()'s
# TRUNCATE... actually the reverse is now true, see compile_layer()) but is
# no longer load-bearing for correctness.


@contextmanager
def _connect(db_path: str | None = None):
    """db_path=None (the real default, every production caller) wraps
    common.db_backend.pg_conn() directly -- NOT
    second_brain.index_db.get_conn(), see the module docstring's note on
    why (NEXTCLOUD_ADMIN_USER must not be required just to import this
    module). An explicit db_path opens a raw sqlite3 connection to that
    file instead -- test-only (tests/second_brain/test_semantic.py's
    synthetic_index fixture), same test-double substitution as
    _create_schema()'s isinstance branch, not the app supporting two
    backends in production."""
    if db_path:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
        return
    with db_backend.pg_conn() as conn:
        yield conn


def assign(conn, m: SemanticModel) -> dict:
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
    # 2026-09-19: iterate rows and index by key, not tuple-unpack the
    # cursor directly -- Postgres rows are dicts (dict_row), and
    # unpacking a dict yields its KEYS, not its values (a real, silent
    # bug found live during this migration -- see db_backend.py's module
    # docstring for the wider incident this pattern caused).
    by_form: dict[str, list[str]] = {}
    for row in conn.execute("SELECT concept_id, normalized FROM semantic_labels"):
        by_form.setdefault(row["normalized"], []).append(row["concept_id"])

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
    for row in conn.execute("SELECT path, tags FROM vault_notes_fulltext"):
        path, tags = row["path"], row["tags"]
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
    for row in conn.execute(
            "SELECT path, COALESCE(ingest_method,'') AS ingest_method FROM vault_documents"):
        path, ingest = row["path"], row["ingest_method"]
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
        for row in conn.execute("SELECT path, title FROM vault_notes_fulltext"):
            path, title = row["path"], row["title"]
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
        "INSERT OR REPLACE INTO semantic_unmapped_tags(tag, occurrences) VALUES (?,?)",
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


def assign_derivations(conn) -> int:
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
    for row in conn.execute("SELECT path, content FROM vault_notes_fulltext"):
        path, content = row["path"], row["content"]
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


def _resolve_derivation_target(conn, target: str) -> str | None:
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
    return row["path"] if row else None


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
    with _connect(db_path) as conn:
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
            for row in rows:
                relation, target, evidence, kind = row["relation"], row["target"], row["evidence"], row["kind"]
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
            for row in all_rows:
                src_path, relation, target, evidence, kind = (
                    row["path"], row["relation"], row["target"], row["evidence"], row["kind"]
                )
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


def _resolve_note_ts(mtime: str | None, indexed_at: str | None) -> tuple[datetime, str]:
    """Real-timestamp resolution for a vault note, shared by assign_chronology()
    and assign_geometry() (hoisted from assign_chronology()'s own nested closure,
    2026-09-20 -- geometric reasoning needs the identical resolution logic, and
    a second copy would be exactly the kind of parallel mechanism the operator
    already rejected once, 2026-08-24, for chronology's own table). See
    assign_chronology()'s docstring for why mtime (not indexed_at) is the
    preferred source and why it must be parsed via email.utils.parsedate_to_datetime()
    rather than sorted as text."""
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


def assign_chronology(conn) -> int:
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
    # 2026-09-19: vd.mtime/vd.indexed_at added to GROUP BY -- Postgres
    # enforces the SQL standard strictly (every selected column must be
    # grouped or aggregated); SQLite silently picked an arbitrary row's
    # value for them instead. Safe/no-op semantically: vault_documents.path
    # is UNIQUE, so mtime/indexed_at are already functionally dependent
    # on sc.path, which was already in the GROUP BY -- this just makes
    # that dependency explicit, not a behavior change.
    raw = conn.execute("""
        SELECT sc.path, sc.concept_id, vd.mtime, vd.indexed_at
        FROM semantic_note_concepts sc
        JOIN vault_documents vd ON vd.path = sc.path
        GROUP BY sc.path, sc.concept_id, vd.mtime, vd.indexed_at
    """).fetchall()

    resolved = sorted(
        ((r["path"], r["concept_id"], *_resolve_note_ts(r["mtime"], r["indexed_at"]))
         for r in raw),
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


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km. Deliberately a small local copy rather than
    importing one of this codebase's several existing _haversine_nm() helpers
    (cifp_lookup.py, geo_filter.py, local_airspace.py, runner/main.py,
    web/main.py) -- those are nautical-mile-native and private to their own
    modules; this design's evidence format wants distance_km (§6), and this
    module has no other reason to depend on ingest/runner/web internals."""
    r_km = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r_km * math.asin(math.sqrt(a))


def _valid_flight_pairs(conn) -> set[tuple[str, str]]:
    """The real guard against _CALLSIGN_RE false positives (2026-09-20 fix,
    replaces an earlier airline-prefix-only _valid_airline_codes()). Pulled
    live from flight_events rather than a hardcoded list -- self-maintaining,
    covers whatever carriers/flight numbers this platform actually sees,
    not a static dict someone has to remember to extend (flight_resolver.py's
    IATA_TO_ICAO_CARRIER is a smaller, curated subset for a different
    purpose -- API carrier filtering -- and was considered but rejected here
    for that reason: it would silently miss real carriers this platform
    already has live position data for).

    Airline-prefix-only validation let 'MAX8'/'MAX9'/'NEW269' through as
    extracted flight identifiers -- 'MAX' and 'NEW' are both real values in
    flight_events.airline, so any nearby 1-4 digit number satisfied the old
    check, regardless of whether that (airline, flight_num) pair ever
    actually flew. Requiring the EXACT pair to exist as a real flight_events
    row closes this off directly: flight_num '8'/'9' never occurred for
    airline 'MAX' (only '230'/'240' did, live-checked), and '269' never
    occurred for 'NEW' (only '111'/'115'/'232'/'442'/'443'/'961' did) --
    both false positives fail this check. A real trade-off, noted not
    hidden: a genuinely real flight_num that predates flight_events'
    ~30-35 day retention window, or one flown but never SWIM-observed,
    won't validate either -- same honesty-over-completeness principle
    Phase 0 already accepts for position resolution, now also gating
    extraction itself."""
    rows = conn.execute(
        "SELECT DISTINCT airline, flight_num FROM flight_events "
        "WHERE airline IS NOT NULL AND flight_num IS NOT NULL").fetchall()
    return {(r["airline"], r["flight_num"]) for r in rows}


def _known_station_codes(conn) -> dict[str, tuple[float, float]]:
    """station_code -> (lat, lon) from the Phase 0 reference table
    (pg_schema/0058_station_coordinates.sql). This IS the
    Amtrak-side spatial gate the design doc's decision #2 calls for
    (reusing amtrak.py's regional_stations() membership) -- station_coordinates
    was seeded from that same default station set, so matching against this
    table's codes is equivalent without a cross-module import from
    second_brain.semantic into poller.fetchers.amtrak."""
    rows = conn.execute("SELECT station_code, lat, lon FROM station_coordinates").fetchall()
    return {r["station_code"]: (r["lat"], r["lon"]) for r in rows}


def _extract_flight_refs(text: str, valid_pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """2026-09-20 fix: was airline-prefix-only ('MAX8'/'NEW269' false
    positives -- see _valid_flight_pairs()'s docstring). Two guards now,
    both evidence-backed against the real false positives found:

    1. Digit-adjacency context guard -- reject a candidate immediately
       preceded by a digit (ignoring at most one space). Confirmed live:
       every MAX8/MAX9 false positive came from "Boeing 737 MAX 8"-shaped
       aircraft-type text, where a real aircraft-type-designator or model
       number sits directly before the candidate. A genuine flight
       reference (a callsign standing on its own in prose) doesn't have a
       digit run immediately ahead of it. Cheap, no DB lookup, catches the
       whole aircraft-type-designator collision class in general, not just
       'MAX8'/'MAX9' specifically.
    2. Exact (airline, flight_num) pair validated against a real
       flight_events row (valid_pairs) -- the primary guard, closes both
       known false-positive cases (MAX8/MAX9 AND the unrelated NEW269
       "New 269-Foot Superyacht" case, which guard 1 alone would NOT catch
       since nothing aircraft-type-shaped precedes it)."""
    if not text or not valid_pairs:
        return set()
    upper = text.upper()
    out = set()
    for m in _CALLSIGN_RE.finditer(upper):
        pre = upper[:m.start()]
        if pre and (pre[-1].isdigit() or (pre[-1] == " " and len(pre) > 1 and pre[-2].isdigit())):
            continue
        airline, num = m.group(1), m.group(2)
        if (airline, num) in valid_pairs:
            out.add((airline, num))
    return out


def _extract_train_refs(text: str) -> set[str]:
    if not text:
        return set()
    return {m.group(1) for m in _TRAIN_RE.finditer(text)}


def _extract_station_codes_in_text(text: str, known: dict[str, tuple[float, float]]) -> set[str]:
    if not text or not known:
        return set()
    upper = text.upper()
    found = set()
    for code in known:
        if re.search(rf"\b{re.escape(code)}\b", upper):
            found.add(code)
    return found


# How far a note's own real timestamp may drift from a live flight_events row's
# updated_at and still be trusted as "this row IS the specific flight instance
# the note is about," not some other day's instance of the same recurring
# airline+flight_num. Not specified by the design doc -- a judgment call made
# here: wide enough to cover normal pre-departure/post-arrival note-writing lag
# for a single flight (hours, not minutes), tight enough that two instances of
# a daily scheduled flight (typically >12h apart) can't be confused for each
# other. Deliberately NOT the same number as GEO_TIME_WINDOW_MIN (30min) --
# that governs whether two DIFFERENT notes' real-world events are "proximate,"
# a different question from "is this the right instance of THIS note's flight."
_FLIGHT_INSTANCE_GATE_SECONDS = 6 * 3600


def _lookup_flight_position(conn, airline: str, flight_num: str,
                             note_epoch: float) -> tuple[float, float] | None:
    row = conn.execute(
        """SELECT position_lat, position_lon, updated_at FROM flight_events
           WHERE airline = ? AND flight_num = ?
             AND position_lat IS NOT NULL AND position_lon IS NOT NULL
           ORDER BY ABS(updated_at - ?) ASC LIMIT 1""",
        (airline, flight_num, note_epoch),
    ).fetchone()
    if not row:
        return None
    if abs(row["updated_at"] - note_epoch) > _FLIGHT_INSTANCE_GATE_SECONDS:
        return None
    return row["position_lat"], row["position_lon"]


def assign_geometry(conn) -> int:
    """Geometric reasoning Phase 0 (docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md)
    -- writes INTO semantic_note_derivations (relation='proximate_to',
    kind='geometric'), same table/graph as chronology and derivations, same
    "one graph, not a second mechanism" precedent (2026-08-24).

    Two stages, both wholesale-recomputed every run (semantic_note_instance_refs
    is TRUNCATEd by _create_schema() like every other table in _TABLES;
    semantic_note_derivations' 'geometric' rows are cleared explicitly below,
    matching assign_chronology()'s own belt-and-suspenders DELETE even though
    the whole table is already empty on the real Postgres path):

    1. EXTRACT: regex over every note's title+content for a real flight
       reference (_CALLSIGN_RE, (airline, flight_num) pair validated live
       against flight_events -- see _valid_flight_pairs()) or train reference
       (_TRAIN_RE, matching this platform's own "Train N" convention, not the
       design doc's illustrative "AMTK2157" -- checked live, this codebase
       never renders trains that way). Each found instance is resolved to a
       real position where one exists: flights via flight_events, gated to
       rows within _FLIGHT_INSTANCE_GATE_SECONDS of the note's own real
       timestamp (never "whatever's currently live" -- airline+flight_num
       recurs daily for scheduled service, so an ungated join would silently
       attach today's position to an old note about a different day's flight);
       trains via whichever station_coordinates code is also mentioned in the
       same note (Amtrak has no live per-train position feed at all --
       ustrains_departures is 0 rows, checked live 2026-09-20 -- so a train
       reference's spatial anchor IS the station it's mentioned with, and its
       temporal anchor is the note's own real timestamp, not a live train feed
       that doesn't exist). No position resolves for either kind and the
       identifier is still recorded with lat/lon NULL: real per decision #1
       (all notes are candidates), honest per this design's established
       insufficient_data convention (never fabricate a position that doesn't
       exist), NOT a bug.

    2. LINK: every pair of instance-refs from DIFFERENT notes (excluding pairs
       that are literally the same real-world instance -- two notes about the
       identical flight are a concept-co-occurrence question, not a geometric
       one) is tested against BOTH dimensions independently, per decision #3
       ("dropping a real spatial-only or time-only match would be a travesty"):
       time_delta_min <= GEO_TIME_WINDOW_MIN (30, sibling-scaled from the
       disruption-digest's days=30) and, where both sides resolved a position,
       distance_km <= _geo_proximity_radius_km() (default 25, matching this
       design's own §6 worked example). A pair passing either test gets an
       edge; `evidence` records matched=both|space_only|time_only plus the
       real distance/time-delta so a consumer can tell a strong compound match
       from a partial one, never losing the partial case. Edges are written in
       BOTH directions (proximity is symmetric; the relation isn't a temporal
       ordering the way preceded_by is) so either note's own path resolves it
       without an OR-across-two-columns query.

    Structurally honest gap, not a bug to route around: flight_events retains
    only ~30-35 days live (960,934 rows / 564,586 with position, live-checked
    2026-09-20; older rows are archived to gzipped JSONL in the vault, not
    SQL-queryable) -- so the flight side of this backfill can only ever
    resolve real positions for notes referencing a flight from within that
    live window, not the full ~7,000+-note historical corpus decision #1
    describes as the seed. The Amtrak side has no equivalent retention
    ceiling (station_coordinates is static reference data, not a live feed),
    so its coverage is bounded only by whether a note actually names both a
    train number and a station -- not by data aging out."""
    valid_pairs = _valid_flight_pairs(conn)
    stations = _known_station_codes(conn)
    radius_km = _geo_proximity_radius_km()

    conn.execute("DELETE FROM semantic_note_derivations WHERE kind='geometric'")

    notes = conn.execute("""
        SELECT vd.path, vd.mtime, vd.indexed_at,
               COALESCE(nf.title, '') AS title, COALESCE(nf.content, '') AS content
        FROM vault_documents vd
        LEFT JOIN vault_notes_fulltext nf ON nf.path = vd.path
    """).fetchall()

    # (path, instance_type, identifier, dt, epoch, lat, lon, source)
    facts: list[tuple] = []
    for n in notes:
        dt, ts_str = _resolve_note_ts(n["mtime"], n["indexed_at"])
        epoch = dt.timestamp()
        # Bounded read: a pathological single note shouldn't make extraction
        # unbounded; 20k chars comfortably covers any real vault note's body.
        text = f"{n['title']}\n{n['content'][:20000]}"

        for airline, num in _extract_flight_refs(text, valid_pairs):
            pos = _lookup_flight_position(conn, airline, num, epoch)
            lat, lon = pos if pos else (None, None)
            source = "flight_events" if pos else None
            facts.append((n["path"], "flight", f"{airline}{num}", dt, epoch, lat, lon, source, ts_str))

        train_nums = _extract_train_refs(text)
        if train_nums:
            station_codes = _extract_station_codes_in_text(text, stations)
            # Deterministic choice when a note names more than one station:
            # the one mentioned FIRST in the text, not an arbitrary set order.
            anchor = min(station_codes, key=lambda c: text.upper().find(c)) if station_codes else None
            lat, lon, source = (None, None, None)
            if anchor:
                lat, lon = stations[anchor]
                source = "station_coordinates"
            for train_num in train_nums:
                facts.append((n["path"], "train", train_num, dt, epoch, lat, lon, source, ts_str))

    refs_out = [(path, itype, ident, ts_str, lat, lon, source)
                for path, itype, ident, _dt, _epoch, lat, lon, source, ts_str in facts]
    conn.executemany(
        "INSERT OR IGNORE INTO semantic_note_instance_refs VALUES (?,?,?,?,?,?,?)", refs_out)

    out: list[tuple] = []
    n = len(facts)
    for i in range(n):
        path_i, type_i, id_i, dt_i, _epoch_i, lat_i, lon_i, _src_i, _ts_i = facts[i]
        for j in range(i + 1, n):
            path_j, type_j, id_j, dt_j, _epoch_j, lat_j, lon_j, _src_j, _ts_j = facts[j]
            if path_i == path_j:
                continue
            if type_i == type_j and id_i == id_j:
                continue  # same real-world instance -- concept co-occurrence, not geometry

            time_delta_min = abs((dt_i - dt_j).total_seconds()) / 60.0
            time_ok = time_delta_min <= GEO_TIME_WINDOW_MIN

            distance_km = None
            space_ok = False
            if lat_i is not None and lat_j is not None:
                distance_km = _haversine_km(lat_i, lon_i, lat_j, lon_j)
                space_ok = distance_km <= radius_km

            if not time_ok and not space_ok:
                continue
            matched = "both" if (time_ok and space_ok) else ("space_only" if space_ok else "time_only")
            dist_str = f"{distance_km:.2f}" if distance_km is not None else ""

            ev_fwd = (f"self={type_i}:{id_i};target={type_j}:{id_j};"
                      f"distance_km={dist_str};time_delta_min={time_delta_min:.1f};"
                      f"method=haversine;matched={matched}")
            ev_rev = (f"self={type_j}:{id_j};target={type_i}:{id_i};"
                      f"distance_km={dist_str};time_delta_min={time_delta_min:.1f};"
                      f"method=haversine;matched={matched}")
            out.append((path_i, "proximate_to", path_j, ev_fwd, "geometric"))
            out.append((path_j, "proximate_to", path_i, ev_rev, "geometric"))

    conn.executemany(
        "INSERT OR IGNORE INTO semantic_note_derivations VALUES (?,?,?,?,?)", out)
    return len(out)


# Geometric reasoning Phase 2 (design doc §3, "provable tier"). Both numbers
# are the design's own locked-in 2026-09-17 decision -- deliberately the SAME
# values already governing every analyze_* function in common/db.py
# (analyze_flight_number_patterns, analyze_train_patterns,
# analyze_vessel_patterns all take min_samples=5; analyze_disruption_weather_split
# takes days=30), not new constants invented for this layer.
CAUSAL_WINDOW_DAYS = 30
CAUSAL_MIN_SAMPLES = 5

# Bounded per-entity-pair note-pair sample. Direction is a majority test and
# evidence quotes at most 3, so retaining more buys nothing and was the main
# term in the 2026-09-22 OOM (465M against a 384M ceiling).
_PAIR_SAMPLE_CAP = 64

_CAUSAL_EVIDENCE_RE = re.compile(
    r"^self=(?P<self>[^;]+);target=(?P<target>[^;]+);n=(?P<n>\d+);"
    r"expected=(?P<expected>[^;]+);lift=(?P<lift>[^;]+);"
    r"lead_lag_min=(?P<lead_lag_min>[^;]*);direction=(?P<direction>[^;]+);"
    r"event_lead_lag_min=(?P<event_lead_lag_min>[^;]*);"
    r"event_direction=(?P<event_direction>[^;]+);event_n=(?P<event_n>\d+);"
    r"window_days=(?P<window_days>\d+);samples=(?P<samples>.*)$"
)


def assign_causal_associations(conn, window_days: int = CAUSAL_WINDOW_DAYS,
                               min_samples: int = CAUSAL_MIN_SAMPLES) -> int:
    """Geometric reasoning Phase 2 -- the PROVABLE tier, aggregating Phase 1's
    per-instance `proximate_to` edges into per-entity-pair statistical
    associations (relation='statistically_associated', kind='causal').

    Same one graph as derivations/chronology/geometry, per the 2026-08-24
    precedent the operator set explicitly ("I don't want it as a second
    mechanism... I want it baked into the causal chain"). `kind` is what keeps
    the tiers honest, not a separate table or query surface.

    WHAT IT COMPUTES, per design doc §3:
      * N            -- how many DISTINCT note-pairs co-locate entity A with
                        entity B. Phase 1 writes each pair twice (both
                        directions, proximity being symmetric), so the pair is
                        canonicalised before counting or N would double.
      * Baseline     -- how often A and B each occur independently, as a share
                        of in-window notes. `expected` is the co-occurrence
                        count independence alone would predict
                        (n_A * n_B / n_notes); `lift` is observed/expected.
                        Lift near 1.0 means "these two show up together exactly
                        as often as chance would put them together" -- which is
                        the whole point of measuring a baseline at all, and the
                        reason a raw co-occurrence count is NOT evidence.
      * Direction    -- taken from the SAME note pairs' existing `preceded_by`
                        chronological edges (kind='chronological'), never
                        recomputed from timestamps here. If A's note reliably
                        precedes B's, direction=a_leads_b and lead_lag_min is
                        the median gap; if the order is inconsistent,
                        direction=ambiguous and the interval is omitted rather
                        than averaged into a meaningless number.
      * Minimum-N    -- nothing below `min_samples` is written at all.

    HONEST DEVIATION from the design doc's illustrative example, recorded
    rather than quietly resolved: §3 says "per entity-type pair (e.g. 'NEC
    Amtrak delay' x 'DCA arrival delay')". This platform has no delay-EVENT
    taxonomy -- semantic_note_instance_refs stores concrete identifiers
    (flight:DAL8, train:2157), and instance_type is only 'flight'|'train', so a
    literal type-pair reading yields three useless buckets. Associating
    identifier pairs is what the data actually supports, and it is strictly
    more drillable: every edge names the two real entities and carries sample
    note paths. If an event-class taxonomy is built later, this aggregates up
    to it without re-deriving anything.

    SHAPE OF THE ROW: `path` is the most recent in-window note evidencing the
    association (real, drillable), `target` is the partner ENTITY string, not a
    note. An entity target does not resolve to a vault note and is returned by
    trace_causal_chain() as a terminal leaf -- the behaviour Phase 1 already
    designed for, since most authored Provenance targets are free text too.
    One row per ordered entity pair, not one per evidencing note-pair, so this
    stays compact against Phase 1's ~900k edges.

    NOT causation. The relation is 'statistically_associated' and every
    consumer-facing string says associated. Two flights sharing an approach
    corridor will co-occur constantly with high lift and mean nothing causal.
    Lift and N are reported so a reader can judge; they are never asserted as
    cause, per §6's framing rules.

    READ lead_lag_min CAREFULLY -- it is the interval between the NOTES, not
    between the real-world movements. Much of this vault is daily digests, so a
    genuine daily service pattern shows up as lead_lag_min near 1440 (observed
    live: 1435.3 between two NEC trains). That is the notes being a day apart,
    not one train trailing another by a day. Treated as an event interval it
    would be nonsense; treated as "these appear in consecutive daily notes,
    consistently in this order" it is real and useful. A true
    event-to-event interval needs per-event timestamps this layer does not
    have, and is deliberately not faked here."""
    conn.execute("DELETE FROM semantic_note_derivations WHERE kind='causal'")

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    # In-window notes, and how many distinct notes each entity appears in.
    # instance_refs is the right source for the baseline (it is one row per
    # note x entity), NOT the edge table, whose counts are already pair-shaped.
    in_window: dict[str, set[str]] = {}
    note_ts: dict[str, datetime] = {}
    for r in conn.execute(
            "SELECT path, instance_type, identifier, note_ts "
            "FROM semantic_note_instance_refs").fetchall():
        try:
            ts = datetime.fromisoformat(r["note_ts"])
        except (TypeError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        ent = f"{r['instance_type']}:{r['identifier']}"
        in_window.setdefault(ent, set()).add(r["path"])
        note_ts[r["path"]] = ts

    n_notes = len({p for paths in in_window.values() for p in paths})
    if n_notes < 2:
        return 0

    # Canonicalised co-occurrence counts, plus a BOUNDED sample of the
    # note-pairs backing each one (direction + drill-down evidence).
    #
    # 2026-09-22 memory rewrite, after the first version was OOM-killed in
    # production at 465M against this unit's 384M ceiling. The original held
    # every note-pair for every entity-pair as a set of (path, target) string
    # tuples -- up to ~900k long-string tuples -- and materialised all 902,518
    # edge rows at once via fetchall(). It ran fine on the host, where no
    # cgroup limit applies, which is exactly why it was not caught before
    # deploying. Three changes, none of which alter a single output value:
    #
    #   1. N is COUNTED, not accumulated in a set. Phase 1 writes each
    #      note-pair exactly twice per entity-pair (both directions, and its
    #      PK includes the evidence string, so no third row can collide), so
    #      the exact distinct count is rows/2 -- no set required to dedupe.
    #   2. Only _PAIR_SAMPLE_CAP note-pairs are retained per entity-pair.
    #      Direction is a majority test over pairs; a bounded sample answers it
    #      identically once the sample exceeds the 5-sample floor by this much,
    #      and the evidence field only ever quotes 3 of them anyway.
    #   3. Rows are streamed with fetchmany() instead of fetchall(), so peak
    #      memory no longer scales with the edge count at all.
    pair_notes: dict[tuple[str, str], set[int]] = {}
    npair_id: dict[tuple[str, str], int] = {}
    npair_of: list[tuple[str, str]] = []
    cur = conn.execute(
        "SELECT path, target, evidence FROM semantic_note_derivations "
        "WHERE kind='geometric'")
    while True:
        chunk = cur.fetchmany(20000)
        if not chunk:
            break
        for r in chunk:
            m = _GEOMETRY_EVIDENCE_RE.match(r["evidence"] or "")
            if not m:
                continue
            a, b = m.group("self"), m.group("target")
            if a == b:
                continue
            pa, pb = r["path"], r["target"]
            if pa not in note_ts or pb not in note_ts:
                continue  # outside the rolling window
            npair = (pa, pb) if pa < pb else (pb, pa)
            nid = npair_id.get(npair)
            if nid is None:
                nid = npair_id[npair] = len(npair_of)
                npair_of.append(npair)
            key = (a, b) if a < b else (b, a)
            pair_notes.setdefault(key, set()).add(nid)

    # Chronological order between note pairs, read from the edges Phase 1.5
    # already wrote. Deliberately not recomputed from timestamps: preceded_by
    # is the platform's own answer to "which came first", and a second,
    # slightly-different answer computed here is exactly the "second mechanism"
    # failure the 2026-08-24 correction was about.
    precedes: set[tuple[str, str]] = set()
    for r in conn.execute(
            "SELECT path, target FROM semantic_note_derivations "
            "WHERE kind='chronological' AND relation='preceded_by'").fetchall():
        precedes.add((r["path"], r["target"]))

    # Baseline is computed INSIDE the proximity graph, not over raw note
    # counts, and the distinction is not academic. N counts note-PAIRS; a
    # baseline built from note counts (n_a * n_b / n_notes) mixes units and
    # inflates every lift -- measured on the live graph it produced lifts of
    # 28-64x for ordinary daily Amtrak service, which would have made "measured
    # against chance" worthless exactly where the design doc demands it be
    # meaningful.
    #
    # The right null model is: of all note-pairs that passed Phase 1's
    # proximity test at all, how often would A and B land together by chance,
    # given how often each appears in that graph? Both terms are then in
    # pair-units and lift is interpretable -- ~1.0 means "no more often than
    # the proximity graph's own structure already predicts".
    # Both baseline terms are counts of DISTINCT note-pairs, and they must be
    # derived from the sets, never by summing per-entity-pair counts: one
    # note-pair commonly backs several entity-pairs (a note naming six flights
    # backs fifteen), so summing counts it repeatedly and inflates the two
    # terms unequally. Measured 2026-09-22 when that shortcut was tried,
    # median lift moved 1.09 -> 0.84 and the maximum blew out from 100 to
    # 9,256 -- silently wrong, because every individual number still looked
    # plausible.
    total_pairs = len(npair_of)
    ent_pairs: dict[str, set[int]] = {}
    for (a, b), nids in pair_notes.items():
        ent_pairs.setdefault(a, set()).update(nids)
        ent_pairs.setdefault(b, set()).update(nids)

    path_ents: dict[str, set[str]] = {}
    for ent, paths in in_window.items():
        for pth in paths:
            path_ents.setdefault(pth, set()).add(ent)

    event_times = _entity_event_times(conn, window_days)

    out: list[tuple] = []
    for (a, b), nids in pair_notes.items():
        n = len(nids)
        if n < min_samples:
            continue
        npairs = [npair_of[i] for i in nids]

        p_a, p_b = len(ent_pairs.get(a, ())), len(ent_pairs.get(b, ()))
        expected = (p_a * p_b) / total_pairs if total_pairs else 0.0
        lift = (n / expected) if expected > 0 else float("inf")

        # Direction + lead/lag, from preceded_by only.
        a_first = b_first = 0
        gaps: list[float] = []
        for pa, pb in npairs:
            a_note = pa if a in path_ents.get(pa, ()) else pb
            b_note = pb if a_note == pa else pa
            if (a_note, b_note) in precedes:
                a_first += 1
            elif (b_note, a_note) in precedes:
                b_first += 1
            else:
                continue
            gaps.append(abs((note_ts[b_note] - note_ts[a_note]).total_seconds()) / 60.0)

        decided = a_first + b_first
        if decided and max(a_first, b_first) / decided >= 0.8 and gaps:
            direction = "a_leads_b" if a_first > b_first else "b_leads_a"
            gaps.sort()
            lead_lag = f"{gaps[len(gaps) // 2]:.1f}"
        else:
            # Inconsistent ordering, or no chronological edge joins these notes
            # at all. Reporting an averaged interval here would invent a
            # precision the data does not have.
            direction, lead_lag = "ambiguous", ""

        # Second, independent channel: the entities' REAL movements. The note
        # channel above answers "in what order were these written about"; this
        # answers "in what order did they actually move". They are reported
        # side by side and never merged -- when they disagree that IS the
        # finding (e.g. a diversion written up hours after it happened).
        ev_dir, ev_lag, ev_n = _event_lead_lag(
            event_times.get(a, []), event_times.get(b, []))

        sample = ";".join(sorted(p for p, _ in sorted(npairs))[:3])
        newest = max(npairs, key=lambda np: max(note_ts[np[0]], note_ts[np[1]]))
        anchor = max(newest, key=lambda p: note_ts[p])

        _flip = {"a_leads_b": "b_leads_a", "b_leads_a": "a_leads_b"}
        for self_ent, other_ent, dirn, edirn in (
                (a, b, direction, ev_dir),
                (b, a, _flip.get(direction, "ambiguous"), _flip.get(ev_dir, "ambiguous"))):
            ev = (f"self={self_ent};target={other_ent};n={n};"
                  f"expected={expected:.2f};lift={lift:.2f};"
                  f"lead_lag_min={lead_lag};direction={dirn};"
                  f"event_lead_lag_min={ev_lag};event_direction={edirn};event_n={ev_n};"
                  f"window_days={window_days};samples={sample}")
            out.append((anchor, "statistically_associated", other_ent, ev, "causal"))

    conn.executemany(
        "INSERT OR IGNORE INTO semantic_note_derivations VALUES (?,?,?,?,?)", out)
    return len(out)


def _entities_of(in_window: dict[str, set[str]], path: str) -> set[str]:
    """Which entities a given note references, inverted from the baseline map
    already in memory -- avoids a second pass over instance_refs per pair."""
    return {ent for ent, paths in in_window.items() if path in paths}


# Real-world movement channel for Phase 2 (operator directive 2026-09-22:
# lead/lag "should both be between real world movements as well as the notes").
# ±6h is the pairing horizon for "these two movements are the same episode" --
# wider and a morning departure starts matching an evening one purely because
# both exist; narrower and a genuine multi-hour knock-on gets dropped.
_EVENT_PAIR_HORIZON_MIN = 360


def _parse_event_ts(val) -> float | None:
    """Epoch seconds from the several shapes these tables actually store:
    epoch floats (flight_events), ISO-with-Z (flight_ooooi_times), and
    ISO-with-offset (train_events). Returns None rather than guessing."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) or None
    s = str(val).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _entity_event_times(conn, window_days: int) -> dict[str, list[tuple[float, str]]]:
    """entity -> sorted [(epoch, event_kind)] of REAL movements, not notes.

    This is the channel that makes lead/lag mean what an operator expects.
    Sources, in order of how directly they describe a movement:

      * flight_ooooi_times -- OUT/OFF/ON/IN, the actual phase movements. These
        only became usable on 2026-09-22; before that the table held 4 rows
        because of the watchlist gate + ON CONFLICT bugs fixed the same day.
      * flight_events -- departure_time/arrival_time where the feed supplied
        them (frequently NULL, so this supplements OOOI, never replaces it).
      * fdps_diversion_continuations -- a diversion is exactly the kind of
        disruptive event the operator named, and it is keyed by callsign.
      * train_events -- estimated_time per station (the real movement),
        falling back to scheduled_time when no estimate exists.

    Entity strings match semantic_note_instance_refs: 'flight:DAL8',
    'train:97'."""
    cutoff = time.time() - window_days * 86400
    out: dict[str, list[tuple[float, str]]] = {}

    def add(ent: str, ts: float | None, kind: str) -> None:
        if ts and ts >= cutoff:
            out.setdefault(ent, []).append((ts, kind))

    for r in conn.execute(
            "SELECT airline, flight_num, airline_out_time, airline_off_time, "
            "airline_on_time, airline_in_time FROM flight_ooooi_times "
            "WHERE airline IS NOT NULL AND flight_num IS NOT NULL").fetchall():
        ent = f"flight:{r['airline']}{r['flight_num']}"
        for col, kind in (("airline_out_time", "out"), ("airline_off_time", "off"),
                          ("airline_on_time", "on"), ("airline_in_time", "in")):
            add(ent, _parse_event_ts(r[col]), kind)

    for r in conn.execute(
            "SELECT airline, flight_num, departure_time, arrival_time "
            "FROM flight_events WHERE airline IS NOT NULL AND flight_num IS NOT NULL "
            "AND (departure_time IS NOT NULL OR arrival_time IS NOT NULL)").fetchall():
        ent = f"flight:{r['airline']}{r['flight_num']}"
        add(ent, _parse_event_ts(r["departure_time"]), "dep")
        add(ent, _parse_event_ts(r["arrival_time"]), "arr")

    for r in conn.execute(
            "SELECT callsign, diversion_detected_at FROM fdps_diversion_continuations "
            "WHERE callsign IS NOT NULL").fetchall():
        add(f"flight:{r['callsign']}", _parse_event_ts(r["diversion_detected_at"]), "diversion")

    for r in conn.execute(
            "SELECT train_number, scheduled_time, estimated_time FROM train_events "
            "WHERE train_number IS NOT NULL").fetchall():
        ts = _parse_event_ts(r["estimated_time"]) or _parse_event_ts(r["scheduled_time"])
        add(f"train:{r['train_number']}", ts, "train_stop")

    for ent in out:
        out[ent].sort()
    return out


def _event_lead_lag(ev_a: list[tuple[float, str]],
                    ev_b: list[tuple[float, str]]) -> tuple[str, str, int]:
    """(direction, median_signed_minutes_as_str, n_matched) between two
    entities' REAL movements.

    For each movement of A, the temporally nearest movement of B within
    ±_EVENT_PAIR_HORIZON_MIN is paired with it and the signed gap recorded.
    Median, not mean -- one diversion or one held train should not drag the
    interval. Direction is only claimed when at least 80% of matched pairs
    agree on sign, the same consistency bar the note channel uses; otherwise
    'ambiguous' with no interval, because an averaged interval over
    inconsistent ordering is a fabricated number."""
    if not ev_a or not ev_b:
        return "ambiguous", "", 0
    b_times = [t for t, _ in ev_b]
    horizon = _EVENT_PAIR_HORIZON_MIN * 60
    deltas: list[float] = []
    for ta, _ in ev_a:
        i = bisect.bisect_left(b_times, ta)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(b_times):
                d = b_times[j] - ta
                if abs(d) <= horizon and (best is None or abs(d) < abs(best)):
                    best = d
        if best is not None:
            deltas.append(best / 60.0)
    if not deltas:
        return "ambiguous", "", 0
    a_first = sum(1 for d in deltas if d > 0)   # B happens after A -> A leads
    b_first = sum(1 for d in deltas if d < 0)
    decided = a_first + b_first
    if not decided or max(a_first, b_first) / decided < 0.8:
        return "ambiguous", "", len(deltas)
    deltas.sort()
    median = deltas[len(deltas) // 2]
    return ("a_leads_b" if a_first > b_first else "b_leads_a",
            f"{abs(median):.1f}", len(deltas))


CLUSTER_MIN_SIZE = 3

_CLUSTER_EVIDENCE_RE = re.compile(
    r"^member=(?P<member>[^;]+);cluster=(?P<cluster>[^;]+);size=(?P<size>\d+);"
    r"internal_edges=(?P<internal_edges>\d+);median_lift=(?P<median_lift>[^;]+);"
    r"provable=(?P<provable>\d+);plausible=(?P<plausible>\d+);members=(?P<members>.*)$"
)


def assign_clusters(conn, min_size: int = CLUSTER_MIN_SIZE) -> int:
    """Geometric reasoning Phase 3 (design doc §4) -- `member_of_cluster`.

    Deterministic connected-components over the Phase 2 association graph.
    Components rather than modularity: §4 offers either, and components have no
    seed, no iteration count and no tunable resolution parameter. That matters
    more than tighter communities would, because ontology.json's own governance
    rule is "deterministic and reviewable, never LLM-in-the-loop" -- a
    clustering whose output shifted between runs on identical data could not be
    audited, and auditability is this layer's whole claim.

    EDGE SET -- associations at or above chance only (lift >= 1.0).
    §4 describes clusters as groups that "repeatedly show up interlinked". A
    pair with lift < 1.0 co-occurs LESS than chance: real evidence, but
    evidence of separation, and folding it in would assert belonging from data
    saying the opposite. Measured live 2026-09-22: 1,044 of 1,466 association
    edges sit at or below chance, so including them collapses nearly everything
    into one meaningless mega-component. This threshold is an INTERPRETATION of
    §4's wording, not a number the design doc states -- surfaced here as an
    operator decision rather than buried in the code.

    TIER PRESERVATION, per §4's explicit requirement that "each member edge
    carries its own plausible/provable tier ... the tiering doesn't get lost at
    the group level": every cluster records how many internal edges are
    provable (lift >= 2.0 -- at least twice chance) versus plausible
    (1.0 <= lift < 2.0), plus the cluster's median lift. A cluster of "3
    plausible, 1 provable" reads as exactly that, never flattened to one
    confidence number.

    Cluster ids are content-derived (`cluster:<alphabetically-first member>`),
    not sequential, so the same entity set yields the same id across runs and
    two compiles can be diffed meaningfully."""
    conn.execute("DELETE FROM semantic_note_derivations WHERE kind='cluster'")

    adj: dict[str, set[str]] = {}
    lifts: dict[tuple[str, str], float] = {}
    anchors: dict[str, str] = {}
    for r in conn.execute(
            "SELECT path, evidence FROM semantic_note_derivations "
            "WHERE kind='causal'").fetchall():
        m = _CAUSAL_EVIDENCE_RE.match(r["evidence"] or "")
        if not m:
            continue
        try:
            lift = float(m.group("lift"))
        except ValueError:
            continue
        if lift < 1.0:
            continue
        a, b = m.group("self"), m.group("target")
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
        lifts[(a, b) if a < b else (b, a)] = lift
        anchors.setdefault(a, r["path"])

    seen: set[str] = set()
    out: list[tuple] = []
    for start in sorted(adj):
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:                      # iterative: a deep component must not
            cur = stack.pop()             # blow the recursion limit
            comp.append(cur)
            for nxt in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        if len(comp) < min_size:
            continue

        comp.sort()
        members = set(comp)
        internal = sorted(lv for (x, y), lv in lifts.items()
                          if x in members and y in members)
        provable = sum(1 for lv in internal if lv >= 2.0)
        plausible = len(internal) - provable
        median_lift = internal[len(internal) // 2] if internal else 0.0
        cluster_id = f"cluster:{comp[0]}"
        member_str = ",".join(comp[:12]) + (",..." if len(comp) > 12 else "")

        for ent in comp:
            # `member=` is FIRST and is what makes this row unique. The table's
            # PK is (path, relation, target, evidence); without the member in
            # the evidence, every entity in a cluster shares the same target
            # (the cluster id) and the same evidence, so members collapse to
            # one row per distinct anchor path and INSERT OR IGNORE silently
            # drops the rest. Observed 2026-09-22: 60 rows built, 6 stored,
            # and the function still returned 60 because it counted attempted
            # inserts. It also left the row unable to say WHICH entity it was
            # a membership record for, which is the one thing it exists to say.
            ev = (f"member={ent};cluster={cluster_id};size={len(comp)};"
                  f"internal_edges={len(internal)};median_lift={median_lift:.2f};"
                  f"provable={provable};plausible={plausible};members={member_str}")
            out.append((anchors.get(ent, cluster_id), "member_of_cluster",
                        cluster_id, ev, "cluster"))

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
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT path, target, evidence FROM semantic_note_derivations "
            "WHERE relation='preceded_by' AND kind='chronological' "
            "AND evidence LIKE ?",
            (f"concept={concept_id};%",)
        ).fetchall()
        parsed = []
        for row in rows:
            path, predecessor, evidence = row["path"], row["target"], row["evidence"]
            m = _CHRONOLOGY_EVIDENCE_RE.match(evidence)
            if not m or m.group("concept") != concept_id:
                continue
            parsed.append({"path": path, "sequence": int(m.group("seq")),
                          "preceded_by": predecessor, "ts": m.group("ts")})
        parsed.sort(key=lambda h: h["sequence"])
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM semantic_note_concepts WHERE concept_id = ?",
            (concept_id,)).fetchone()["n"]
        if limit:
            parsed = parsed[:limit]
        return {"concept_id": concept_id, "total_occurrences": total, "history": parsed}


_GEOMETRY_EVIDENCE_RE = re.compile(
    r"^self=(?P<self>[^;]+);target=(?P<target>[^;]+);"
    r"distance_km=(?P<distance_km>[^;]*);time_delta_min=(?P<time_delta_min>[^;]+);"
    r"method=haversine;matched=(?P<matched>.+)$"
)

# matched=both is a compound (space AND time) signal, strictly stronger than
# either dimension alone -- ranks first regardless of its own distance value,
# per design doc §3 decision #3 ("both together is the strongest signal").
_GEOMETRY_MATCH_RANK = {"both": 0, "space_only": 1, "time_only": 2}


def query_all_angles(entity: str, db_path: str | None = None) -> dict:
    """Geometric reasoning Phase 1 (docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md
    §5) -- composite, multi-angular retrieval: every relational `kind` for one
    vault note path, in ONE call, over the SAME semantic_note_derivations
    table trace_causal_chain()/concept_history() already read -- no second
    query surface, same 2026-08-24 "one graph" precedent as everything else
    in this module. This is the literal fix for the operator's stated goal
    (multi-angular, SIMULTANEOUS reasoning): today every reader here,
    including this module's own trace_causal_chain(), still only ever looks
    at one relation/kind slice at a time -- even though the graph has carried
    multiple relational axes (evidenced/chronological/geometric) since Phase
    0. Nothing before this function actually reasons across them together.

    entity -- a vault note path, same convention as trace_causal_chain()'s
    `start` param (not a bare flight/train identifier -- those live INSIDE
    a geometric edge's `evidence` string, keyed to whichever note mentioned
    them; see assign_geometry()'s docstring). Read-only, writes nothing.

    Returns {"entity", "computed_at", "derivation": [...], "chronological":
    [...], "geometric": {"threshold_km", "threshold_min",
    "candidates_evaluated", "matches": [...]}, "causal": [...], "cluster":
    [...]}. `causal`/`cluster` are always empty today -- Phase 2/3
    (assign_causal_associations()/assign_clusters()) don't exist yet, so
    there is nothing to query; this function does not special-case that,
    it just naturally finds no kind='causal'/'cluster' rows and returns [].
    `derivation` covers kind='evidenced' (a real authored ## Provenance
    line) -- named to match this table's OWN relation-family term, not
    assign_derivations()'s function name, since 'derivation' the noun is
    what a caller actually wants back, not which function wrote it.

    Each geometric match is parsed from its evidence string (self=/target=/
    distance_km=/time_delta_min=/matched=) into real fields rather than
    handing a caller a raw evidence blob to re-parse -- one parse site, not
    one per consumer. `candidates_evaluated` is the real count of every
    OTHER instance-ref assign_geometry() actually pairwise-tested this
    entity's own instance-refs against (semantic_note_instance_refs rows
    excluding this path's own) -- not a placeholder: assign_geometry()'s
    O(n^2) pass really does test every pair, so this is the true candidate
    pool size, most of which fail both dimensions and never become a
    written edge. Distinguishing "evaluated" from "matched" (design doc §6's
    "3 candidates evaluated; 1 within threshold") requires this -- the
    matches list alone can't recover how many were checked and rejected.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT relation, target, evidence, kind FROM semantic_note_derivations "
            "WHERE path = ? ORDER BY kind, relation, target", (entity,)
        ).fetchall()

        derivation: list[dict] = []
        chronological: list[dict] = []
        causal: list[dict] = []
        cluster: list[dict] = []
        geometric_raw: list[str] = []
        for r in rows:
            relation, target, evidence, kind = r["relation"], r["target"], r["evidence"], r["kind"]
            if kind == "evidenced":
                derivation.append({"relation": relation, "target": target, "evidence": evidence})
            elif kind == "chronological":
                chronological.append({"relation": relation, "target": target, "evidence": evidence})
            elif kind == "geometric":
                geometric_raw.append(evidence)
            elif kind == "causal":
                causal.append({"relation": relation, "target": target, "evidence": evidence})
            elif kind == "cluster":
                cluster.append({"relation": relation, "target": target, "evidence": evidence})

        geo_matches: list[dict] = []
        for ev in geometric_raw:
            m = _GEOMETRY_EVIDENCE_RE.match(ev)
            if not m:
                continue  # defensive only -- assign_geometry() always writes this exact shape
            d = m.groupdict()
            geo_matches.append({
                "self": d["self"],
                "target": d["target"],
                "distance_km": float(d["distance_km"]) if d["distance_km"] else None,
                "time_delta_min": float(d["time_delta_min"]),
                "matched": d["matched"],
            })
        # Sort by strength, never narrative importance (design doc §6):
        # both-dimension matches first, then real distance ascending
        # (closer = stronger), time_delta as the final tiebreak.
        geo_matches.sort(key=lambda g: (
            _GEOMETRY_MATCH_RANK.get(g["matched"], 3),
            g["distance_km"] if g["distance_km"] is not None else float("inf"),
            g["time_delta_min"],
        ))

        # ?-placeholder + this table's real name, same as every other query
        # in this module -- _connect()'s Postgres path already runs every
        # statement through db_backend's translate_sql() (?  -> %s, plus
        # SQLite-only syntax elsewhere in this file), so this one query
        # string works unmodified against both backends, exactly like
        # concept_history()'s COUNT(*) query just above.
        #
        # candidates_evaluated = (this entity's own instance-ref count) x
        # (every other note's instance-ref count) -- assign_geometry()'s
        # O(n^2) pass tests EVERY one of this note's own extracted
        # flight/train references against every other note's, not the note
        # as a single unit. A note with only 1 own reference has
        # own_count=own*other==other, same as the naive "just count other
        # rows" version; a digest note with many own references (own_count
        # >1) pairwise-tests each of them, so the real total is strictly
        # larger -- caught live: an early version of this query used
        # other-count alone and produced candidates_evaluated < matches
        # (3,017 candidates vs 21,912 matches) for a busy transport-patterns
        # digest note, which is impossible (matches can't exceed the pool
        # they were drawn from). own_count*other_count is the real product
        # assign_geometry()'s nested loop actually computes for this path.
        own_count = conn.execute(
            "SELECT COUNT(*) AS n FROM semantic_note_instance_refs WHERE path = ?",
            (entity,)
        ).fetchone()["n"]
        other_count = conn.execute(
            "SELECT COUNT(*) AS n FROM semantic_note_instance_refs WHERE path != ?",
            (entity,)
        ).fetchone()["n"]
        candidates_evaluated = own_count * other_count

        return {
            "entity": entity,
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "derivation": derivation,
            "chronological": chronological,
            "geometric": {
                "threshold_km": _geo_proximity_radius_km(),
                "threshold_min": GEO_TIME_WINDOW_MIN,
                "candidates_evaluated": candidates_evaluated,
                "matches": geo_matches,
            },
            "causal": causal,
            "cluster": cluster,
        }


def format_all_angles_block(result: dict) -> str:
    """Renders query_all_angles()'s geometric-tier output into the ONE
    canonical text block design doc §6 specifies -- byte-identical no
    matter which persona/consumer calls this, computed once here rather
    than re-derived/re-worded per caller. Framing rules applied exactly as
    written there:
      - explicit coverage ("N candidates evaluated, M within threshold")
        even when M=0 -- silence must never be ambiguous between "nothing
        checked" and "checked, found nothing" (this IS an active,
        already-live Phase 0 computation, so a real zero gets stated, not
        omitted)
      - computed-at timestamp, explicit snapshot framing
      - sorted by strength (already done by query_all_angles()), never
        re-sorted here by narrative importance
      - raw identifiers only (self=/target= as extracted, e.g.
        "flight:AAL1284"), no causal-implying adjectives
      - plausible-tier (geometric) disclaimer on every render: co-occurrence
        only, no causal relation computed

    Only renders GEOMETRY today. causal/cluster sections are DELIBERATELY
    omitted, not rendered as an empty/zero section like geometric -- Phase
    2/3 haven't shipped, so there is genuinely nothing to report yet, a
    different state from "checked this entity, found none" (which IS what
    an empty geometric.matches list means, since assign_geometry() already
    ran against the whole corpus). When assign_causal_associations()/
    assign_clusters() ship, extend this function with a HISTORICAL PATTERN
    section following the same explicit-coverage rule, not a separate
    formatter -- one canonical block stays one function.
    derivation/chronological are returned by query_all_angles() (the full
    composite view the design doc's data layer promises) but not rendered
    into this persona-facing block -- they're note-provenance facts
    (already separately reachable via trace_causal_chain()/--trace), not
    the "what's operationally proximate right now" content this block
    exists to surface.
    """
    geo = result["geometric"]
    ts_utc = result["computed_at"]
    lines = [
        f"GEOMETRY (computed {ts_utc}, snapshot; "
        f"threshold {geo['threshold_km']:g}km/{geo['threshold_min']:g}min)"
    ]
    matches = geo["matches"]
    for i, g in enumerate(matches, start=1):
        if g["distance_km"] is not None:
            sep = f"separation {g['distance_km']:.1f}km"
        else:
            sep = "separation unresolved (no position on one or both sides)"
        lines.append(
            f"Pair {i} — {g['self']} ↔ {g['target']}: {sep}; "
            f"time-window overlap {g['time_delta_min']:.0f}min."
        )
    lines.append(
        f"{geo['candidates_evaluated']} candidates evaluated; {len(matches)} within threshold. "
        "Co-occurrence only, no causal relation computed."
    )
    return "\n".join(lines)


def most_recent_geometry_entity(db_path: str | None = None) -> str | None:
    """Picks a real, current entity for a persona's data-builder to pass into
    query_all_angles() -- ops-brief (Phase 1's one wired persona) is a daily
    conditions briefing with no single pre-existing "entity of interest" the
    way a per-flight tracking skill has, so this exists to make a defensible,
    real choice rather than the caller hardcoding one: the most recently
    (real-timestamp) touched note that has at least one kind='geometric' edge.

    Real-timestamp, not vault_documents.mtime as a raw string sort --
    mtime is an RFC 2822 string ("Tue, 11 Aug 2026 22:41:59 GMT") that
    cannot sort correctly as text (the day-of-week/month-name prefix breaks
    lexicographic date order), same trap _resolve_note_ts() already exists
    to avoid for assign_chronology() -- reused here rather than
    re-introducing that exact bug in a second location.
    """
    with _connect(db_path) as conn:
        paths = conn.execute(
            "SELECT DISTINCT path FROM semantic_note_derivations WHERE kind='geometric'"
        ).fetchall()
        if not paths:
            return None
        placeholders = ",".join("?" for _ in paths)
        rows = conn.execute(
            f"SELECT path, mtime, indexed_at FROM vault_documents "
            f"WHERE path IN ({placeholders})",
            tuple(r["path"] for r in paths),
        ).fetchall()
        if not rows:
            return None
        best_path, best_dt = None, None
        for r in rows:
            dt, _ts_str = _resolve_note_ts(r["mtime"], r["indexed_at"])
            if best_dt is None or dt > best_dt:
                best_path, best_dt = r["path"], dt
        return best_path


def compile_layer(db_path: str | None = None,
                  ontology_path: str | None = None) -> dict:
    m = load(ontology_path)
    with _connect(db_path) as conn:
        # 2026-09-19: the pre-write cache-warming reads that used to live
        # here (SELECT COUNT(*) FROM vault_notes_fts/vault_documents) were
        # a lock-avoidance mitigation for SQLite's exclusive-lock mode --
        # see _connect()'s HISTORICAL comment above. Postgres's MVCC has
        # no such lock to avoid, so they were removed rather than kept as
        # meaningless overhead.
        _create_schema(conn)
        _write_model(conn, m)
        result = assign(conn, m)
        result["derivations"] = assign_derivations(conn)
        result["chronology_edges"] = assign_chronology(conn)
        result["geometry_edges"] = assign_geometry(conn)
        # Phase 2 runs AFTER assign_geometry in the same pass, by necessity:
        # it aggregates the proximate_to edges that call just wrote. Running it
        # against a stale graph would measure the previous compile's geometry.
        result["causal_edges"] = assign_causal_associations(conn)
        # Phase 3 consumes Phase 2's output, so it runs last in the chain.
        result["cluster_edges"] = assign_clusters(conn)

        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "semantic_layer_version": m.version,
            "compiled_at": now,
            "namespace": m.namespace,
            **{k: str(v) for k, v in m.stats().items()},
            **{k: str(v) for k, v in result.items()},
        }
        conn.executemany("INSERT OR REPLACE INTO semantic_meta(key, value) VALUES (?,?)",
                         sorted(meta.items()))
        conn.commit()
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
    out: list[dict] = []
    with _connect(db_path) as conn:
        for metric in m.metrics():
            entry = {"id": metric.id, "label": metric.pref_label,
                     "unit": metric.unit, "grain": metric.grain}
            try:
                rowsx = conn.execute(metric.sql).fetchall()
                # 2026-09-19: dict_row rows -- list(r) on a dict gives its
                # KEYS, not its values; .values() is the fix. Same for the
                # single-value case below (column name is whatever the
                # metric's own SQL happened to alias it, so take the first
                # value positionally via .values(), not by name).
                if metric.grain == "concept":
                    entry["value"] = [list(r.values()) for r in rowsx]
                else:
                    entry["value"] = next(iter(rowsx[0].values())) if rowsx and rowsx[0] else None
            except (psycopg.Error, sqlite3.Error) as e:
                entry["error"] = str(e)
                # A failed statement aborts the connection's current
                # transaction (autocommit=False, see db_backend.py's pool
                # config) -- without this rollback, every metric AFTER
                # the first broken one would also fail (with
                # InFailedSqlTransaction, not its own real error),
                # defeating this loop's whole "one broken metric doesn't
                # take down the other nine" purpose. Same class of bug as
                # the KeyError cascade found live earlier in this
                # migration -- see db_backend.py's module docstring.
                conn.rollback()
            out.append(entry)
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
    # Counted in SQL rather than by measuring a fetched page: an earlier
    # revision fetched up to 400 rows and reported len(), which silently
    # capped both figures and understated the very gain this function exists
    # to demonstrate.
    where_curated = ("AND path NOT LIKE '%" + RSS_PREFIX_FRAGMENT + "%'"
                     if curated_only else "")
    with _connect(db_path) as conn:
        # 2026-09-19: FTS5 MATCH -> tsvector/plainto_tsquery. Rather than
        # reusing model.SemanticModel.fts_query() (which formats an FTS5-
        # dialect boolean expression -- quoted phrases joined by " OR ",
        # meaningless to Postgres), this builds the OR-of-forms directly
        # from the same underlying data (model.expand(..., for_search=True))
        # via Postgres's own tsquery OR operator (`||` between two tsquery
        # VALUES, not `|` between lexemes inside one to_tsquery() string --
        # `||` is what correctly ORs several possibly-multi-word phrases,
        # each handled by its own plainto_tsquery() call).
        def _tsquery_expr(forms: list[str]) -> tuple[str, tuple]:
            if not forms:
                return "", ()
            parts = ["plainto_tsquery('english', ?)"] * len(forms)
            return " || ".join(parts), tuple(forms)

        def _count(forms: list[str]) -> int:
            expr, params = _tsquery_expr(forms)
            if not expr:
                return 0
            try:
                return conn.execute(
                    "SELECT COUNT(*) AS n FROM vault_notes_fulltext "
                    f"WHERE search_vector @@ ({expr}) {where_curated}",
                    params).fetchone()["n"]
            except (psycopg.Error, sqlite3.Error):
                # Malformed query text -- report zero rather than crashing
                # the comparison this function exists to make (same
                # defensive intent as the original FTS5-error handling).
                conn.rollback()
                return 0

        forms_expanded = m.expand(query, for_search=True) if concept else [query]
        naive_n = _count([query])
        exp_n = _count(forms_expanded)

        rows: list = []
        expr, params = _tsquery_expr(forms_expanded)
        if expr:
            try:
                rows = conn.execute(
                    "SELECT path, title, "
                    f"ts_headline('english', content, ({expr}), "
                    "'StartSel=**, StopSel=**, MaxFragments=1, MaxWords=18, MinWords=5') AS snippet "
                    "FROM vault_notes_fulltext "
                    f"WHERE search_vector @@ ({expr}) {where_curated} "
                    "ORDER BY ts_rank(search_vector, (" + expr + ")) DESC LIMIT ?",
                    params + params + params + (limit,)).fetchall()
            except (psycopg.Error, sqlite3.Error):
                conn.rollback()
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
            "results": [{"path": r["path"], "title": r["title"], "snippet": r["snippet"]}
                        for r in rows],
        }


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

    try:
        with _connect(db_path) as conn:
            rowsx = conn.execute(
                "SELECT tag, occurrences FROM semantic_unmapped_tags "
                "ORDER BY occurrences DESC").fetchall()
            report["unmapped_tags"] = [
                {"tag": r["tag"], "occurrences": r["occurrences"]} for r in rowsx
            ]
    except (psycopg.Error, sqlite3.Error):
        report["unmapped_tags"] = [{"error": "not compiled yet -- run --compile"}]
    return report


if __name__ == "__main__":  # pragma: no cover - convenience only
    print(json.dumps(compile_layer(), indent=2))
