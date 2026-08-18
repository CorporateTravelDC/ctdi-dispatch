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
    _create_schema,
    _write_model,
    drift_report,
    semantic_search,
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
