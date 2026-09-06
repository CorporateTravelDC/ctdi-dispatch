"""Unit tests for the second-brain semantic layer.

These assert the properties the layer is *for*, not just that the code runs:

  * the vocabulary is structurally sound (no dangling or cyclic relations)
  * it is a strict superset of shared/rss_catalog.py's alias clusters, which
    is the standing governance promise made in ontology.json
  * the surface forms that were actually fragmenting the live vault all
    collapse to one concept each
  * assignment is deterministic and auditable
  * the compiled SQLite tables answer the same questions as the Python API,
    since that equivalence is the entire interoperability claim

The compile/assign tests build a small synthetic index with the same schema as
the live one. They deliberately do NOT touch
/var/lib/corporatetraveldc/second_brain_index.db -- a unit test must never
depend on, or mutate, production data. The layer is separately exercised
against the real index through the CLI.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from second_brain.semantic import load, normalize  # noqa: E402
from second_brain.semantic.compile import (  # noqa: E402
    assign,
    assign_chronology,
    assign_derivations,
    concept_history,
    _create_schema,
    _write_model,
    drift_report,
    semantic_search,
    trace_causal_chain,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def model():
    return load()


@pytest.fixture
def synthetic_index():
    """A throwaway DB with the live index's schema and a handful of notes whose
    tags reproduce the real fragmentation (underscore vs hyphen vs shorthand)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.executescript("""
        CREATE TABLE vault_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL, category TEXT, size_bytes INTEGER,
            mtime TEXT, etag TEXT, indexed_at TEXT NOT NULL, tags TEXT,
            ingest_method TEXT, compile_status TEXT DEFAULT 'raw');
        CREATE VIRTUAL TABLE vault_notes_fts USING fts5(
            path UNINDEXED, title, content, tags, tokenize='porter');
    """)
    rows = [
        # (path, tags, ingest_method, title, content)
        ("corporatetraveldc/04-Syntheses/daily/aam-2026-08-01.md",
         "daily,aam,vertiport,evtol,synthesis,auto", "aam-daily-watch",
         "AAM Daily Watch", "vertiport news"),
        ("corporatetraveldc/00-Inbox/cross-link-findings/advanced_air_mobility-joby.md",
         "novel-finding,cross-link,auto,advanced_air_mobility",
         "entity-tracking-novel-finding",
         "Cross-link finding: Joby Aviation", "joby body"),
        ("corporatetraveldc/04-Syntheses/daily/gig-2026-08-01.md",
         "daily,gig-economy,synthesis,auto", "gig-economy-daily-watch",
         "Gig Economy Daily Watch", "rideshare"),
        ("corporatetraveldc/00-Inbox/cross-link-findings/gig_economy-uber.md",
         "novel-finding,cross-link,auto,gig_economy",
         "entity-tracking-novel-finding", "Cross-link finding: Uber", "uber"),
        ("corporatetraveldc/01-Sources/manual/20260818T000000Z.md",
         "manual,high-priority,infra", "manual",
         "Ollama thermal finding", "the box ran hot"),
        ("corporatetraveldc/00-Inbox/rss/abc123.md",
         "rss,untriaged", "rss", "Some feed item", "raw"),
    ]
    for path, tags, ingest, title, content in rows:
        conn.execute(
            "INSERT INTO vault_documents(path, filename, category, size_bytes,"
            " mtime, etag, indexed_at, tags, ingest_method) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (path, path.rsplit("/", 1)[-1], "test", 10, "t", "e", "t", tags, ingest))
        conn.execute(
            "INSERT INTO vault_notes_fts(path, title, content, tags) "
            "VALUES (?,?,?,?)", (path, title, content, tags))
    conn.commit()
    yield conn, tmp.name
    conn.close()
    Path(tmp.name).unlink(missing_ok=True)


# ── Vocabulary integrity ─────────────────────────────────────────────────────

def test_model_loads_and_validates(model):
    """load() runs the full structural validation; a dangling broader, an
    unknown facet or a cycle raises rather than loading half a vocabulary."""
    assert model.version
    assert len(model.concepts()) > 50
    for c in model.concepts():
        assert c.facet in {f.id for f in model.facets()}


def test_no_broader_cycles(model):
    for c in model.concepts():
        assert c.id not in model.ancestors(c.id)


def test_every_entity_has_a_domain(model):
    """An entity with no domain cannot contribute to domain retrieval, which
    silently costs recall rather than failing loudly."""
    assert model.unmapped_entities == []


def test_in_domain_targets_are_domains(model):
    for c in model.concepts():
        if c.in_domain:
            assert model.get(c.in_domain).facet == "domain"


# ── The fragmentation this layer exists to fix ───────────────────────────────

@pytest.mark.parametrize("forms,expected", [
    # every spelling of AAM observed in the live vault / catalog / lexicon
    (["advanced_air_mobility", "aam", "AAM", "evtol", "eVTOL", "vertiport",
      "Advanced Air Mobility", "urban air mobility", "EV tolls"],
     "advanced_air_mobility"),
    # the pure underscore-vs-hyphen splits
    (["gig_economy", "gig-economy", "Gig Economy", "rideshare"], "gig_economy"),
    (["executive_protection", "executive-protection", "security", "EP"],
     "executive_protection"),
    (["concierge_luxury_travel", "concierge", "luxury-travel"],
     "concierge_luxury_travel"),
])
def test_surface_forms_collapse_to_one_concept(model, forms, expected):
    for f in forms:
        got = model.resolve(f)
        assert got is not None, f"{f!r} resolved to nothing"
        assert expected in model.closure(got.id), \
            f"{f!r} -> {got.id}, which does not imply {expected}"


def test_trains_tag_resolves(model):
    """Regression: shared/rss_catalog.py's trains_yachts alias cluster contains
    rail/railways/yacht/yachts/marine but NOT 'trains', so the live notes
    tagged `trains` failed to resolve to their own domain."""
    got = model.resolve("trains")
    assert got is not None
    assert "trains_yachts" in model.closure(got.id)


def test_normalize_matches_rss_catalog():
    """--drift compares this layer against rss_catalog by normalised form. If
    the two normalisers ever diverge, drift would report phantom failures."""
    from shared.rss_catalog import _normalize_label
    for s in ["Advanced Air Mobility", "A.A.M.", "gig-economy", "EV toll(s)"]:
        assert normalize(s) == _normalize_label(s)


# ── Facet separation ─────────────────────────────────────────────────────────

def test_cadence_tags_are_not_domains(model):
    """`daily` is a genre, not a topic. Flattening the two into one tag string
    is the defect; the layer must not reproduce it."""
    for tag in ["daily", "weekly", "synthesis"]:
        assert model.resolve(tag).facet == "genre"
    for tag in ["auto", "authored"]:
        assert model.resolve(tag).facet == "provenance"
    assert model.resolve("untriaged").facet == "lifecycle"


def test_entity_closure_reaches_domain(model):
    """A note mentioning Joby must become findable under AAM and Aviation
    without anyone having tagged it with either."""
    joby = model.resolve("Joby Aviation")
    assert joby.facet == "entity"
    assert "advanced_air_mobility" in model.closure(joby.id)
    assert "aviation" in model.closure(joby.id)


# ── Search expansion ─────────────────────────────────────────────────────────

def test_fts_query_is_phrase_quoted(model):
    """FTS5 reads a bare hyphen as a column filter, so an unquoted
    `gig-economy` raises 'no such column: economy'."""
    q = model.fts_query("gig economy")
    assert '"gig-economy"' in q
    assert " OR " in q


def test_noisy_forms_excluded_from_search_but_still_resolve(model):
    """`utm` must keep resolving to Advanced Air Mobility, but must not enter
    full-text expansion -- it matched every utm_source= tracking URL."""
    assert "advanced_air_mobility" in model.closure(model.resolve("utm").id)
    assert "utm" in model.expand("aam")
    assert "utm" not in model.expand("aam", for_search=True)
    assert '"utm"' not in model.fts_query("aam")


# ── Compilation and assignment ───────────────────────────────────────────────

def test_compile_assigns_and_is_auditable(model, synthetic_index):
    conn, _ = synthetic_index
    _create_schema(conn)
    _write_model(conn, model)
    result = assign(conn, model)
    conn.commit()

    assert result["assignments"] > 0

    # both AAM spellings land on the one concept
    paths = {r[0] for r in conn.execute(
        "SELECT path FROM semantic_note_concepts WHERE concept_id=?",
        ("advanced_air_mobility",))}
    assert len(paths) == 2, "the two AAM notes did not unify"

    # every assignment carries the rule and evidence that produced it
    rows = conn.execute(
        "SELECT rule, evidence FROM semantic_note_concepts LIMIT 50").fetchall()
    assert all(r[0] and r[1] is not None for r in rows)
    assert {r[0] for r in rows} & {"tag_exact", "path_prefix", "ingest_method",
                                   "broader_closure", "title_entity"}


def test_assignment_is_deterministic(model, synthetic_index):
    conn, _ = synthetic_index
    _create_schema(conn); _write_model(conn, model); assign(conn, model)
    first = sorted(conn.execute(
        "SELECT path, concept_id, rule FROM semantic_note_concepts").fetchall())
    _create_schema(conn); _write_model(conn, model); assign(conn, model)
    second = sorted(conn.execute(
        "SELECT path, concept_id, rule FROM semantic_note_concepts").fetchall())
    assert first == second


def test_raw_feed_items_marked_non_curated(model, synthetic_index):
    conn, _ = synthetic_index
    _create_schema(conn); _write_model(conn, model); assign(conn, model)
    rows = conn.execute(
        "SELECT DISTINCT is_curated FROM semantic_note_concepts "
        "WHERE path LIKE '%/00-Inbox/rss/%'").fetchall()
    assert rows == [(0,)]


def test_sqlite_answers_match_python_api(model, synthetic_index):
    """The interoperability claim: a non-Python consumer joining the compiled
    tables must get the same answer the Python API gives."""
    conn, _ = synthetic_index
    _create_schema(conn); _write_model(conn, model); assign(conn, model)
    for term in ["aam", "gig-economy", "vertiport"]:
        via_sql = conn.execute(
            "SELECT concept_id FROM semantic_labels WHERE normalized=?",
            (normalize(term),)).fetchall()
        via_api = {c.id for c in model.resolve_all(term)}
        assert {r[0] for r in via_sql} == via_api


def test_unmapped_tags_are_surfaced(model, synthetic_index):
    """Unmapped vocabulary must land in the governance backlog, never be
    silently dropped."""
    conn, _ = synthetic_index
    conn.execute("INSERT INTO vault_notes_fts(path, title, content, tags) "
                 "VALUES (?,?,?,?)",
                 ("corporatetraveldc/01-Sources/x.md", "x", "x",
                  "definitely-not-a-real-concept"))
    _create_schema(conn); _write_model(conn, model); assign(conn, model)
    rows = dict(conn.execute(
        "SELECT tag, occurrences FROM semantic_unmapped_tags").fetchall())
    assert "definitely-not-a-real-concept" in rows


def test_semantic_search_beats_literal(model, synthetic_index):
    """The payoff, measured: a query using one spelling must retrieve notes
    filed under the others."""
    conn, path = synthetic_index
    _create_schema(conn); _write_model(conn, model); assign(conn, model)
    conn.commit()
    res = semantic_search("aam", db_path=path)
    assert res["concept"] == "advanced_air_mobility"
    assert res["hits"] >= res["naive_hits"]


# ── Governance ───────────────────────────────────────────────────────────────

def test_is_superset_of_rss_catalog(synthetic_index):
    """ontology.json promises this in governance.authoring_rules. rss_catalog
    stays the live PWA authority and is never edited from here, so the promise
    has to be enforced from this side."""
    conn, path = synthetic_index
    rep = drift_report(db_path=path)
    rc = rep["rss_catalog"]
    assert rc.get("is_superset") is True, (
        f"not a superset: categories={rc.get('categories_not_covered')} "
        f"aliases={rc.get('aliases_not_covered')}")


def test_context_pack_never_drops_facets_its_rules_depend_on(model):
    """Regression, found live against corporatetraveldc-pi5-chat.

    The first degradation ladder shed the genre and provenance sections before
    less essential content, so at a tight budget the model was asked for a
    genre id with no genre vocabulary present and answered with a *domain* id
    instead. Every rule in the pack's footer is expressed in the domain, genre
    and provenance vocabularies, so those must survive at any budget.
    """
    from second_brain.semantic import export as E
    for budget in (12000, 9000, 6000, 4000, 1000):
        pack = E.to_context_pack(model, budget=budget)
        assert "## Domains" in pack, f"domains dropped at budget={budget}"
        assert "## Genre" in pack, f"genre dropped at budget={budget}"
        assert "## Provenance —" in pack, f"provenance dropped at budget={budget}"
        assert "## Provenance rules" in pack


def test_context_pack_is_never_truncated_mid_content(model):
    """Degradation drops whole tiers. A half-cut definition is worse than an
    absent one -- the reader cannot tell anything is missing."""
    from second_brain.semantic import export as E
    for budget in (12000, 6000, 1000):
        pack = E.to_context_pack(model, budget=budget)
        assert pack.rstrip().endswith("."), f"pack ends mid-sentence at {budget}"


def test_turtle_is_wellformed(model):
    """Cheap structural check; the real parser check is `rapper -i turtle`."""
    from second_brain.semantic import export as E
    ttl = E.to_turtle(model)
    assert ttl.startswith("@prefix skos:")
    # every statement block terminates
    assert ttl.count(" a skos:Concept ;") == len(model.concepts())
    for line in ttl.splitlines():
        s = line.strip()
        if s and not s.startswith(("@", "#")):
            assert s.endswith((";", ".")), f"unterminated turtle line: {s!r}"


def test_metrics_have_definitions_and_sql(model):
    for m in model.metrics():
        assert m.definition.strip(), f"{m.id} has no definition"
        assert m.sql.strip().upper().startswith("SELECT")


def test_agents_reference_declared_concepts(model):
    """The context-graph facet is only useful if its edges point at real
    concepts."""
    ids = {c.id for c in model.concepts()}
    for a in model.agents():
        if a.writes_genre:
            assert a.writes_genre in ids, f"{a.id} writes unknown genre"
        if a.writes_provenance:
            assert a.writes_provenance in ids, f"{a.id} writes unknown provenance"


def test_no_stale_entity_domain_keys(model):
    """Every key in ontology.json's entity_domains map must still name a real
    lexicon label. A key left behind after an entity is renamed or removed
    from lexicon.py does nothing, silently and invisibly."""
    assert model.stale_entity_domain_keys == []


# ── Derivation facet (leans_on / derives_from / reutilizes) ────────────────
# 2026-08-23: operator directive -- every second-brain write should record
# what it leaned on, derived, and reutilized, not just its outcome. Distinct
# code path from assign() (note-to-note/note-to-mechanism, not
# note-to-concept), so it gets its own test coverage rather than being
# folded into the tag/path/ingest-method tests above.

_PROVENANCE_NOTE = """## Provenance
Leaned on: [[thermal-ingest-guard]], the LOCKDOWN redesign
Derived: watchdog restart_containers() was unconditional stop-all/start-all
Reutilized: [[STREAK_FILE pattern]] same as the pre-existing COOLDOWN_FILE

## Findings
This text must not be picked up as Provenance content.
Leaned on: this line is past the section boundary and must be ignored
"""


def _insert_note(conn, path, content, tags="manual", ingest="manual"):
    conn.execute(
        "INSERT INTO vault_documents(path, filename, category, size_bytes,"
        " mtime, etag, indexed_at, tags, ingest_method) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (path, path.rsplit("/", 1)[-1], "test", 10, "t", "e", "t", tags, ingest))
    conn.execute(
        "INSERT INTO vault_notes_fts(path, title, content, tags) "
        "VALUES (?,?,?,?)", (path, "Provenance test note", content, tags))
    conn.commit()


def test_derivation_provenance_parses_wikilinks_and_freetext(synthetic_index):
    conn, _ = synthetic_index
    _create_schema(conn)
    _insert_note(conn, "corporatetraveldc/01-Sources/manual/prov-test.md",
                _PROVENANCE_NOTE)
    n = assign_derivations(conn)
    conn.commit()

    rows = {(r[0], r[1], r[2]): r[3] for r in conn.execute(
        "SELECT path, relation, target, evidence "
        "FROM semantic_note_derivations").fetchall()}

    assert n == len(rows) == 3
    key = "corporatetraveldc/01-Sources/manual/prov-test.md"
    assert (key, "leans_on", "thermal-ingest-guard") in rows
    assert rows[(key, "leans_on", "thermal-ingest-guard")].startswith("Leaned on:")
    assert (key, "reutilizes", "STREAK_FILE pattern") in rows
    # Derived: has no [[wikilink]] -- falls back to the free-text remainder.
    assert (key, "derives_from",
            "watchdog restart_containers() was unconditional "
            "stop-all/start-all") in rows

    # The Provenance-lookalike line under "## Findings" must not have been
    # picked up -- the section scan stops at the next "## " heading.
    assert not any("past the section boundary" in ev for ev in rows.values())


def test_derivation_ignores_notes_without_provenance_section(synthetic_index):
    conn, _ = synthetic_index
    _create_schema(conn)
    n = assign_derivations(conn)
    conn.commit()
    # synthetic_index's stock rows have no "## Provenance" heading at all.
    assert n == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM semantic_note_derivations").fetchone()[0] == 0


def test_derivation_is_deterministic(synthetic_index):
    conn, _ = synthetic_index
    _create_schema(conn)
    _insert_note(conn, "corporatetraveldc/01-Sources/manual/prov-test.md",
                _PROVENANCE_NOTE)
    assign_derivations(conn)
    first = sorted(conn.execute(
        "SELECT path, relation, target, evidence "
        "FROM semantic_note_derivations").fetchall())

    # Re-run against the SAME vault content (vault_documents/vault_notes_fts
    # are untouched by _create_schema(), which only rebuilds semantic_*
    # tables) -- determinism means identical input yields identical output,
    # not that re-inserting the same note twice should even be legal.
    _create_schema(conn)
    assign_derivations(conn)
    second = sorted(conn.execute(
        "SELECT path, relation, target, evidence "
        "FROM semantic_note_derivations").fetchall())

    assert first == second


def test_derivation_concepts_are_declared_in_ontology(model):
    """The three relation ids assign_derivations() writes must be real
    concepts under the derivation facet -- otherwise the compiled edges name
    a relation nothing in the vocabulary defines."""
    ids = {c.id for c in model.concepts(facet="derivation")}
    assert ids == {"leans_on", "derives_from", "reutilizes"}


# ── Regressions, 2026-08-23: two real bugs found by an independent audit ──────
#
# 1. _provenance_lines()'s section terminator used to be a literal
#    `line.startswith("## ")` check -- a `#` (h1, shallower than Provenance's
#    own h2) or a `###`+ (deeper) neither matched that exact prefix, so
#    neither correctly terminated the section for the right reason. Fixed to
#    be level-aware (_HEADING_TERMINATOR_RE): terminates on h1/h2 (equal to
#    or shallower than Provenance itself), never on h3+ (a deeper heading
#    nests WITHIN Provenance under normal markdown section semantics, it
#    doesn't end it).
# 2. semantic_note_derivations' target column was truncated to 200 chars
#    while being part of the primary key, and neither table's PK included
#    evidence -- so two rows differing only in target-past-200-chars, or in
#    evidence wording for the same target, silently collided on INSERT OR
#    IGNORE and one was discarded. Root-caused against the live index: 983
#    of ~50,700 assignments were being lost this way in
#    semantic_note_concepts alone (see docs/SECOND_BRAIN_STATUS.md). Fixed
#    by removing the truncation and widening both tables' PKs to include
#    evidence.

def test_provenance_h3_subheading_does_not_terminate_section(synthetic_index):
    """A ### sub-heading is a subsection OF Provenance, not a sibling ending
    it -- content after it must still be parsed."""
    conn, _ = synthetic_index
    _create_schema(conn)
    note = ("## Provenance\n"
            "Leaned on: [[foo]]\n"
            "### Sub Heading\n"
            "Derived: still captured after an h3\n")
    _insert_note(conn, "corporatetraveldc/01-Sources/manual/h3-test.md", note)
    assign_derivations(conn)
    conn.commit()
    rows = conn.execute(
        "SELECT relation, target FROM semantic_note_derivations "
        "WHERE path=?", ("corporatetraveldc/01-Sources/manual/h3-test.md",)
    ).fetchall()
    assert ("derives_from", "still captured after an h3") in [tuple(r) for r in rows]


def test_provenance_h1_heading_terminates_section(synthetic_index):
    """A bare # heading is SHALLOWER than Provenance's own h2 -- it must
    terminate the section (the old literal '## ' check missed this too,
    since '# Heading' doesn't start with '## ' either)."""
    conn, _ = synthetic_index
    _create_schema(conn)
    note = ("## Provenance\n"
            "Leaned on: [[foo]]\n"
            "# Top Level\n"
            "Derived: must not appear, past an h1\n")
    _insert_note(conn, "corporatetraveldc/01-Sources/manual/h1-test.md", note)
    assign_derivations(conn)
    conn.commit()
    rows = conn.execute(
        "SELECT relation, target FROM semantic_note_derivations "
        "WHERE path=?", ("corporatetraveldc/01-Sources/manual/h1-test.md",)
    ).fetchall()
    assert ("derives_from", "must not appear, past an h1") not in [tuple(r) for r in rows]
    assert ("leans_on", "foo") in [tuple(r) for r in rows]


def test_derivation_target_not_truncated(synthetic_index):
    """Two free-text targets differing only after character 200 must both
    survive -- the old rest[:200] truncation made them collide on the PK."""
    conn, _ = synthetic_index
    _create_schema(conn)
    long_a = "x" * 199 + " ALPHA tail that makes this target genuinely unique"
    long_b = "x" * 199 + " BETA tail that makes this target genuinely unique"
    note = f"## Provenance\nDerived: {long_a}\nReutilized: {long_b}\n"
    _insert_note(conn, "corporatetraveldc/01-Sources/manual/trunc-test.md", note)
    n = assign_derivations(conn)
    conn.commit()
    stored = conn.execute(
        "SELECT COUNT(*) FROM semantic_note_derivations WHERE path=?",
        ("corporatetraveldc/01-Sources/manual/trunc-test.md",)
    ).fetchone()[0]
    assert n == 2
    assert stored == 2  # both rows present, not collapsed to 1


def test_derivation_same_target_different_evidence_both_retained(synthetic_index):
    """Two differently-worded lines naming the same [[target]] are two real,
    distinct justifications -- both must be retained, not just the first."""
    conn, _ = synthetic_index
    _create_schema(conn)
    note = ("## Provenance\n"
            "Leaned on: [[Dup]], via the first reasoning\n"
            "Leaned on: [[Dup]], via a second, differently-worded reasoning\n")
    _insert_note(conn, "corporatetraveldc/01-Sources/manual/dup-target.md", note)
    n = assign_derivations(conn)
    conn.commit()
    rows = conn.execute(
        "SELECT evidence FROM semantic_note_derivations WHERE path=? AND target=?",
        ("corporatetraveldc/01-Sources/manual/dup-target.md", "Dup"),
    ).fetchall()
    assert n == 2
    assert len(rows) == 2
    evidences = {r[0] for r in rows}
    assert "via the first reasoning" in next(iter(e for e in evidences if "first" in e))
    assert any("second" in e for e in evidences)


def test_semantic_note_concepts_retains_multiple_evidence_per_concept(synthetic_index, model):
    """Two different tags that both map to the SAME concept under the SAME
    rule are two real justifications, not duplicates -- the old 3-column PK
    (path, concept_id, rule) silently kept only the first via INSERT OR
    IGNORE (983 real rows lost against the live index before this fix)."""
    conn, _ = synthetic_index
    m = model
    _create_schema(conn)
    _write_model(conn, m)
    # aviation's alt_labels include both "aviation" and "flight" -- two
    # distinct tags, same concept, same tag_exact rule.
    _insert_note(conn, "corporatetraveldc/01-Sources/manual/dup-evidence.md",
                "content", tags="aviation,flight")
    result = assign(conn, m)
    conn.commit()
    rows = conn.execute(
        "SELECT evidence FROM semantic_note_concepts "
        "WHERE path=? AND concept_id='aviation' AND rule='tag_exact'",
        ("corporatetraveldc/01-Sources/manual/dup-evidence.md",),
    ).fetchall()
    evidences = {r[0] for r in rows}
    assert evidences == {"aviation", "flight"}
    # The reported count must equal what's actually stored -- no silent gap.


# ── Causal-reasoning traversal (trace_causal_chain, 2026-08-23) ────────────

def test_trace_backward_follows_multihop_chain(synthetic_index):
    """A leans_on B, B derives_from C (all real notes) -- tracing backward
    from A must reach both the direct (depth 0) and transitive (depth 1)
    edges, resolving each [[wikilink]] target to its real note path."""
    conn, db_path = synthetic_index
    _create_schema(conn)
    a = "corporatetraveldc/01-Sources/manual/trace-a.md"
    b = "corporatetraveldc/01-Sources/manual/trace-b.md"
    c = "corporatetraveldc/01-Sources/manual/trace-c.md"
    _insert_note(conn, a, "## Provenance\nLeaned on: [[trace-b]], the earlier finding\n")
    _insert_note(conn, b, "## Provenance\nDerived: [[trace-c]], root cause identified\n")
    _insert_note(conn, c, "## Provenance\nDerived: nothing further upstream\n")
    assign_derivations(conn)
    conn.commit()

    result = trace_causal_chain(a, direction="backward", db_path=db_path)
    by_depth = {e["depth"]: e for e in result["edges"]}
    assert 0 in by_depth and by_depth[0]["target"] == "trace-b"
    assert by_depth[0]["resolved_path"] == b
    assert any(e["depth"] == 1 and e["target"] == "trace-c" and e["resolved_path"] == c
              for e in result["edges"])
    assert any(e["depth"] == 2 for e in result["edges"])  # trace-c's own "Derived" leaf


def test_trace_forward_finds_dependents(synthetic_index):
    """The reverse of the above: tracing forward from C must find B (which
    derives_from C), and transitively A (which leans_on B)."""
    conn, db_path = synthetic_index
    _create_schema(conn)
    a = "corporatetraveldc/01-Sources/manual/dep-a.md"
    b = "corporatetraveldc/01-Sources/manual/dep-b.md"
    c = "corporatetraveldc/01-Sources/manual/dep-c.md"
    _insert_note(conn, a, "## Provenance\nLeaned on: [[dep-b]], continuation\n")
    _insert_note(conn, b, "## Provenance\nDerived: [[dep-c]], the root finding\n")
    _insert_note(conn, c, "## Provenance\nDerived: original discovery\n")
    assign_derivations(conn)
    conn.commit()

    result = trace_causal_chain(c, direction="forward", db_path=db_path)
    paths_by_depth = {e["depth"]: e["path"] for e in result["edges"]}
    assert paths_by_depth.get(0) == b
    assert paths_by_depth.get(1) == a


def test_trace_unresolved_target_is_leaf_not_error(synthetic_index):
    """Most real derivation targets today are free text, not another note
    (41 of 50 live). An unresolved target must surface as a real edge with
    resolved_path=None, not be dropped or raise."""
    conn, db_path = synthetic_index
    _create_schema(conn)
    path = "corporatetraveldc/01-Sources/manual/leaf-test.md"
    _insert_note(conn, path,
                "## Provenance\nLeaned on: a purely descriptive prior incident, no note behind it\n")
    assign_derivations(conn)
    conn.commit()

    result = trace_causal_chain(path, direction="backward", db_path=db_path)
    assert len(result["edges"]) == 1
    assert result["edges"][0]["resolved_path"] is None
    assert "purely descriptive" in result["edges"][0]["target"]


def test_trace_is_cycle_safe(synthetic_index):
    """A leans_on B, B leans_on A -- a real cycle must terminate rather than
    recurse forever, via the visited-set guard."""
    conn, db_path = synthetic_index
    _create_schema(conn)
    a = "corporatetraveldc/01-Sources/manual/cycle-a.md"
    b = "corporatetraveldc/01-Sources/manual/cycle-b.md"
    _insert_note(conn, a, "## Provenance\nLeaned on: [[cycle-b]], mutual reference\n")
    _insert_note(conn, b, "## Provenance\nLeaned on: [[cycle-a]], mutual reference back\n")
    assign_derivations(conn)
    conn.commit()

    result = trace_causal_chain(a, direction="backward", max_depth=10, db_path=db_path)
    # Must terminate (test itself would hang otherwise) and visit each note once.
    resolved_paths = {e["resolved_path"] for e in result["edges"] if e["resolved_path"]}
    assert resolved_paths == {a, b}


def test_trace_respects_max_depth(synthetic_index):
    """A 4-hop chain with max_depth=1 must include depth 0 and depth 1
    (standard inclusive BFS convention -- max_depth=N means up to N edges
    of separation from the root are explored) but never recurse into
    depth-c's own onward edge (which would be depth 2)."""
    conn, db_path = synthetic_index
    _create_schema(conn)
    a = "corporatetraveldc/01-Sources/manual/depth-a.md"
    b = "corporatetraveldc/01-Sources/manual/depth-b.md"
    c = "corporatetraveldc/01-Sources/manual/depth-c.md"
    d = "corporatetraveldc/01-Sources/manual/depth-d.md"
    _insert_note(conn, a, "## Provenance\nLeaned on: [[depth-b]], step one\n")
    _insert_note(conn, b, "## Provenance\nLeaned on: [[depth-c]], step two\n")
    _insert_note(conn, c, "## Provenance\nLeaned on: [[depth-d]], step three\n")
    _insert_note(conn, d, "## Provenance\nDerived: terminal\n")
    assign_derivations(conn)
    conn.commit()

    result = trace_causal_chain(a, direction="backward", max_depth=1, db_path=db_path)
    depths = {e["depth"] for e in result["edges"]}
    assert depths == {0, 1}
    assert not any(e["target"] == "depth-d" for e in result["edges"])


# ── Chronology (assign_chronology / concept_history, 2026-08-24) ───────────
# Timestamp-only temporal precedence -- explicitly NOT a causal claim, but
# (corrected same day, see the module-level docstrings on assign_chronology()
# and trace_causal_chain()) written into the SAME semantic_note_derivations
# table as the evidenced leans_on/derives_from/reutilizes edges, distinguished
# only by `kind`. An earlier revision of this test file asserted the two were
# structurally separate tables -- that was the design that got corrected.

def _insert_note_with_mtime(conn, path, mtime, indexed_at="2026-01-01T00:00:00+00:00",
                            tags="manual", content=""):
    conn.execute(
        "INSERT INTO vault_documents(path, filename, category, size_bytes,"
        " mtime, etag, indexed_at, tags, ingest_method) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (path, path.rsplit("/", 1)[-1], "test", 10, mtime, "e", indexed_at, tags, "manual"))
    conn.execute(
        "INSERT INTO vault_notes_fts(path, title, content, tags) "
        "VALUES (?,?,?,?)", (path, "Chronology test note", content, tags))


def test_chronology_orders_by_real_mtime_not_indexed_at(model, synthetic_index):
    """Three notes sharing a concept, all with the SAME indexed_at (the
    real-world case -- batch index-scan runs collapse indexed_at to a
    handful of values) but genuinely different RFC 2822 mtimes. Order
    must follow real mtime, not indexed_at or insertion order."""
    conn, db_path = synthetic_index
    _create_schema(conn)
    _write_model(conn, model)
    same_indexed_at = "2026-08-18T18:27:01.578769+00:00"
    # Inserted deliberately out of chronological order.
    _insert_note_with_mtime(conn, "corporatetraveldc/01-Sources/manual/chron-c.md",
                            "Wed, 12 Aug 2026 23:03:47 GMT", same_indexed_at, tags="aviation")
    _insert_note_with_mtime(conn, "corporatetraveldc/01-Sources/manual/chron-a.md",
                            "Tue, 11 Aug 2026 22:23:13 GMT", same_indexed_at, tags="aviation")
    _insert_note_with_mtime(conn, "corporatetraveldc/01-Sources/manual/chron-b.md",
                            "Tue, 11 Aug 2026 22:42:05 GMT", same_indexed_at, tags="aviation")
    assign(conn, model)
    conn.commit()
    assign_chronology(conn)
    conn.commit()

    rows = conn.execute(
        "SELECT path, evidence, target FROM semantic_note_derivations "
        "WHERE kind='chronological' AND evidence LIKE 'concept=aviation;%' "
        "AND path LIKE '%chron-%' ORDER BY evidence"
    ).fetchall()
    paths_in_order = [r[0] for r in rows]
    assert paths_in_order == [
        "corporatetraveldc/01-Sources/manual/chron-a.md",
        "corporatetraveldc/01-Sources/manual/chron-b.md",
        "corporatetraveldc/01-Sources/manual/chron-c.md",
    ]
    # predecessor chain (target) must be correct, not just the order.
    by_path = {r[0]: r[2] for r in rows}
    assert by_path["corporatetraveldc/01-Sources/manual/chron-b.md"] == \
        "corporatetraveldc/01-Sources/manual/chron-a.md"
    assert by_path["corporatetraveldc/01-Sources/manual/chron-c.md"] == \
        "corporatetraveldc/01-Sources/manual/chron-b.md"


def test_chronology_falls_back_to_indexed_at_when_mtime_unparseable(model, synthetic_index):
    """A note with a garbage/missing mtime must not crash the compile --
    falls back to indexed_at rather than being silently dropped."""
    conn, db_path = synthetic_index
    _create_schema(conn)
    _write_model(conn, model)
    _insert_note_with_mtime(conn, "corporatetraveldc/01-Sources/manual/chron-bad-mtime.md",
                            mtime="not-a-real-date", indexed_at="2026-08-20T00:00:00+00:00",
                            tags="aviation")
    assign(conn, model)
    conn.commit()
    n = assign_chronology(conn)
    conn.commit()
    assert n > 0  # did not raise, and produced real rows
    row = conn.execute(
        "SELECT evidence FROM semantic_note_derivations "
        "WHERE kind='chronological' AND path=?",
        ("corporatetraveldc/01-Sources/manual/chron-bad-mtime.md",)).fetchone()
    assert row is not None
    assert "ts=2026-08-20T00:00:00+00:00" in row[0]  # fell back to indexed_at verbatim


def test_concept_history_reports_total_and_order(model, synthetic_index):
    """concept_history() is a read-only view over the same table -- total
    count and ordering must match a direct query, and the function itself
    must never claim these are causal."""
    conn, db_path = synthetic_index
    _create_schema(conn)
    _write_model(conn, model)
    _insert_note_with_mtime(conn, "corporatetraveldc/01-Sources/manual/hist-1.md",
                            "Mon, 10 Aug 2026 10:00:00 GMT", tags="aviation")
    _insert_note_with_mtime(conn, "corporatetraveldc/01-Sources/manual/hist-2.md",
                            "Tue, 11 Aug 2026 10:00:00 GMT", tags="aviation")
    assign(conn, model)
    conn.commit()
    assign_chronology(conn)
    conn.commit()

    result = concept_history("aviation", db_path=db_path)
    # "aviation" also picks up occurrences from synthetic_index's own stock
    # rows via broader_closure (vertiport -> advanced_air_mobility ->
    # aviation) -- assert total matches a direct count rather than a
    # hardcoded number that depends on fixture internals.
    direct_total = conn.execute(
        "SELECT COUNT(*) FROM semantic_note_concepts WHERE concept_id='aviation'"
    ).fetchone()[0]
    assert result["total_occurrences"] == direct_total
    hist1 = next(h for h in result["history"]
                if h["path"] == "corporatetraveldc/01-Sources/manual/hist-1.md")
    hist2 = next(h for h in result["history"]
                if h["path"] == "corporatetraveldc/01-Sources/manual/hist-2.md")
    assert hist2["preceded_by"] == "corporatetraveldc/01-Sources/manual/hist-1.md"
    assert hist2["sequence"] == hist1["sequence"] + 1


def test_chronology_and_derivations_share_one_table_and_one_traversal(model, synthetic_index):
    """The point of the 2026-08-24 redesign: a note with BOTH a real,
    evidenced derivation edge AND chronology edges lives in ONE table,
    and trace_causal_chain() -- ONE traversal, no separate command --
    returns both, each correctly tagged by `kind`. This is what "baked
    into the causal chain" actually means, verified structurally rather
    than asserted in prose."""
    conn, db_path = synthetic_index
    _create_schema(conn)
    _write_model(conn, model)
    _insert_note_with_mtime(
        conn, "corporatetraveldc/01-Sources/manual/both-kinds.md",
        "Mon, 10 Aug 2026 10:00:00 GMT", tags="aviation",
        content="## Provenance\nLeaned on: [[some-prior-note]], a real reason\n")
    assign(conn, model)
    conn.commit()
    assign_derivations(conn)
    assign_chronology(conn)
    conn.commit()

    rows = conn.execute(
        "SELECT relation, kind FROM semantic_note_derivations WHERE path=?",
        ("corporatetraveldc/01-Sources/manual/both-kinds.md",)).fetchall()
    kinds_present = {r[1] for r in rows}
    assert "evidenced" in kinds_present  # the real leans_on edge
    assert "chronological" in kinds_present  # at least one concept's chronology
    evidenced_rows = [r for r in rows if r[1] == "evidenced"]
    chronological_rows = [r for r in rows if r[1] == "chronological"]
    assert all(r[0] in {"leans_on", "derives_from", "reutilizes"} for r in evidenced_rows)
    assert all(r[0] == "preceded_by" for r in chronological_rows)

    # The actual proof: --trace (trace_causal_chain) returns BOTH kinds
    # from one call, not just the evidenced one -- an old note with only
    # chronology must not come back empty.
    result = trace_causal_chain(
        "corporatetraveldc/01-Sources/manual/both-kinds.md",
        direction="backward", db_path=db_path)
    result_kinds = {e["kind"] for e in result["edges"]}
    assert "evidenced" in result_kinds
    assert "chronological" in result_kinds


def test_trace_returns_chronology_for_a_note_with_no_provenance_section(model, synthetic_index):
    """A note that predates the derivation facet (or simply never got a
    ## Provenance section) must still return something from --trace --
    its chronological predecessor -- rather than the empty result an
    evidenced-only traversal would give it. This is the exact gap the
    operator flagged: 99% of the vault has no authored Provenance
    section and --trace must not go silent for all of it."""
    conn, db_path = synthetic_index
    _create_schema(conn)
    _write_model(conn, model)
    _insert_note_with_mtime(conn, "corporatetraveldc/01-Sources/manual/old-a.md",
                            "Mon, 10 Aug 2026 10:00:00 GMT", tags="aviation")
    _insert_note_with_mtime(conn, "corporatetraveldc/01-Sources/manual/old-b.md",
                            "Tue, 11 Aug 2026 10:00:00 GMT", tags="aviation")
    assign(conn, model)
    conn.commit()
    assign_derivations(conn)  # produces nothing for these -- no Provenance section
    assign_chronology(conn)
    conn.commit()

    result = trace_causal_chain("corporatetraveldc/01-Sources/manual/old-b.md",
                                direction="backward", db_path=db_path)
    assert result["edges"]  # NOT empty
    assert all(e["kind"] == "chronological" for e in result["edges"])
    assert any(e["resolved_path"] == "corporatetraveldc/01-Sources/manual/old-a.md"
              for e in result["edges"])
