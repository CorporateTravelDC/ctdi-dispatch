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
import sqlite3
from datetime import datetime, timezone

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
    "semantic_note_concepts", "semantic_unmapped_tags",
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
        CREATE TABLE semantic_note_concepts (
            path TEXT NOT NULL, concept_id TEXT NOT NULL, facet TEXT NOT NULL,
            rule TEXT NOT NULL, evidence TEXT, is_curated INTEGER NOT NULL,
            PRIMARY KEY (path, concept_id, rule)
        );
        CREATE INDEX idx_snc_concept ON semantic_note_concepts(concept_id);
        CREATE INDEX idx_snc_path ON semantic_note_concepts(path);
        CREATE INDEX idx_snc_facet ON semantic_note_concepts(facet);
        CREATE TABLE semantic_unmapped_tags (
            tag TEXT PRIMARY KEY, occurrences INTEGER NOT NULL
        );
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
