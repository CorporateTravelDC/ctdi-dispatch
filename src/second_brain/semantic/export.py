"""
second_brain.semantic.export -- render the semantic layer into the shapes each
consumer can actually eat.

One governed vocabulary, four renderings. Adding a consumer must never mean
re-authoring the vocabulary; that re-authoring is precisely how this vault
ended up with four vocabularies in the first place.

  to_json()          the resolved, denormalised model -- for other AI systems,
                     future tooling, or anything that would rather parse one
                     file than link a Python package.
  to_turtle()        SKOS/RDF. Concept schemes per facet, skos:prefLabel /
                     altLabel / broader / definition / scopeNote, plus a local
                     sb:inDomain predicate for the entity-to-domain relation
                     (which is deliberately NOT skos:broader -- an entity and a
                     domain are in different schemes, so asserting broader
                     across them would be a modelling lie that a reasoner would
                     happily propagate). Loadable by any triplestore or
                     ontology editor with no knowledge of this platform.
  to_context_pack()  token-budgeted markdown for LLMs.
  write_all()        all three to the shared data volume every container mounts.

WHY THE CONTEXT PACK IS BUDGETED
--------------------------------
The local models are 21 dedicated corporatetraveldc-pi5-* builds, all 3.8B,
with single-model residency on one thermally-governed Pi 5. Context is the
scarcest resource on the box. The pack therefore has a hard character budget
and degrades by DROPPING WHOLE TIERS (entity concepts first, then definitions,
then non-domain facets) rather than truncating mid-sentence -- a half-cut
concept definition is worse than an absent one, because the model will still
confidently act on the fragment.

WHERE THE OUTPUTS GO
--------------------
/var/lib/corporatetraveldc/semantic/ -- the shared data volume every dispatch
container already mounts, chosen for the same reason build_graph.py writes its
live copy there: anything written into the repo tree is baked into container
images at BUILD time (Containerfile's `COPY src/ src/`) and goes stale between
rebuilds. Writing to the volume means a recompile is visible to every running
container immediately, with no rebuild and no restart.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from second_brain.semantic.model import SemanticModel, load

LIVE_DIR = os.environ.get("SECOND_BRAIN_SEMANTIC_DIR",
                          "/var/lib/corporatetraveldc/semantic")

# Roughly 4 chars/token. 12000 chars is ~3k tokens: comfortably inside the
# brief-class models' prompt headroom alongside their real task payload.
DEFAULT_BUDGET = 12000


def to_json(m: SemanticModel) -> dict:
    return {
        "version": m.version,
        "namespace": m.namespace,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "governance": m.governance,
        "problem_statement": m.problem_statement,
        "assignment_rules": m.assignment_rules,
        "consumers": m.consumers,
        "stats": m.stats(),
        "facets": [
            {"id": f.id, "pref_label": f.pref_label, "definition": f.definition,
             "scope_note": f.scope_note, "polyhierarchy": f.polyhierarchy}
            for f in m.facets()
        ],
        "concepts": [
            {"id": c.id, "facet": c.facet, "pref_label": c.pref_label,
             "definition": c.definition, "scope_note": c.scope_note,
             "broader": c.broader, "in_domain": c.in_domain,
             "related": list(c.related), "source": c.source,
             "entity_subtype": c.entity_subtype,
             "surface_forms": c.surface_forms(),
             "closure": m.closure(c.id)}
            for c in m.concepts()
        ],
        "lexicon_crosswalk": m.lexicon_crosswalk,
        "agents": [vars(a) for a in m.agents()],
        "metrics": [vars(x) for x in m.metrics()],
    }


def _ttl_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def to_turtle(m: SemanticModel) -> str:
    ns = m.namespace
    out: list[str] = [
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix dct:  <http://purl.org/dc/terms/> .",
        "@prefix owl:  <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        f"@prefix {m.prefix}:   <{ns}> .",
        "",
        f"# Second-brain semantic layer v{m.version}",
        f"# Generated {datetime.now(timezone.utc).isoformat()} by "
        f"second_brain.semantic.export -- do not hand-edit; edit ontology.json.",
        "",
        f"{m.prefix}:inDomain a owl:ObjectProperty ;",
        '    rdfs:label "in domain" ;',
        '    rdfs:comment "Relates an entity to the business domain it belongs '
        'to. Deliberately not skos:broader: entity and domain are separate '
        'concept schemes, so a broader assertion across them would license '
        'false inferences." .',
        "",
    ]

    for f in m.facets():
        out.append(f"{m.prefix}:scheme-{f.id} a skos:ConceptScheme ;")
        out.append(f'    skos:prefLabel "{_ttl_escape(f.pref_label)}"@en ;')
        if f.definition:
            out.append(f'    dct:description "{_ttl_escape(f.definition)}"@en ;')
        if f.scope_note:
            out.append(f'    skos:scopeNote "{_ttl_escape(f.scope_note)}"@en ;')
        out[-1] = out[-1].rstrip(" ;") + " ."
        out.append("")

    for c in m.concepts():
        out.append(f"{m.prefix}:{c.id} a skos:Concept ;")
        out.append(f"    skos:inScheme {m.prefix}:scheme-{c.facet} ;")
        out.append(f'    skos:prefLabel "{_ttl_escape(c.pref_label)}"@en ;')
        for form in c.surface_forms():
            if form != c.pref_label:
                out.append(f'    skos:altLabel "{_ttl_escape(form)}"@en ;')
        if c.definition:
            out.append(f'    skos:definition "{_ttl_escape(c.definition)}"@en ;')
        if c.scope_note:
            out.append(f'    skos:scopeNote "{_ttl_escape(c.scope_note)}"@en ;')
        if c.broader:
            out.append(f"    skos:broader {m.prefix}:{c.broader} ;")
        if c.in_domain:
            out.append(f"    {m.prefix}:inDomain {m.prefix}:{c.in_domain} ;")
        for r in c.related:
            out.append(f"    skos:related {m.prefix}:{r} ;")
        if c.entity_subtype:
            out.append(f'    {m.prefix}:entitySubtype "{c.entity_subtype}" ;')
        out[-1] = out[-1].rstrip(" ;") + " ."
        out.append("")

    return "\n".join(out) + "\n"


def to_context_pack(m: SemanticModel, budget: int = DEFAULT_BUDGET,
                    corpus_facts: dict | None = None) -> str:
    """Markdown briefing that teaches any model this vault's vocabulary.

    Written for a reader with no prior knowledge of this platform, because that
    is exactly what a fresh Claude session, a new vendor's agent, and a 3.8B
    local model all are.
    """
    def domain_block(with_defs: bool) -> list[str]:
        lines = ["## Domains — what a note can be ABOUT", ""]
        for c in m.concepts(facet="domain"):
            forms = [f for f in c.surface_forms() if f != c.pref_label][:10]
            lines.append(f"### {c.pref_label}  `{c.id}`")
            if with_defs and c.definition:
                lines.append(c.definition)
            if c.broader:
                lines.append(f"- Part of: **{m.get(c.broader).pref_label}**")
            if forms:
                lines.append(f"- Also written as: {', '.join(forms)}")
            lines.append("")
        return lines

    def facet_block(fid: str, with_defs: bool) -> list[str]:
        f = m.facet(fid)
        lines = [f"## {f.pref_label} — {f.definition}", ""]
        for c in m.concepts(facet=fid):
            forms = [x for x in c.surface_forms() if x != c.pref_label][:6]
            line = f"- **{c.pref_label}** `{c.id}`"
            if forms:
                line += f" — also: {', '.join(forms)}"
            lines.append(line)
            if with_defs and c.definition:
                lines.append(f"  - {c.definition}")
        lines.append("")
        return lines

    def entity_block() -> list[str]:
        lines = ["## Tracked entities (name → domain)", ""]
        by_dom: dict[str, list[str]] = {}
        for c in m.concepts(facet="entity"):
            by_dom.setdefault(c.in_domain or "unassigned", []).append(c.pref_label)
        for dom, names in sorted(by_dom.items()):
            label = m.get(dom).pref_label if m.get(dom) else dom
            lines.append(f"- **{label}**: {', '.join(sorted(names))}")
        lines.append("")
        return lines

    header = [
        f"# Second-Brain Semantic Layer v{m.version}",
        "",
        "The controlled vocabulary for the ctdi-dispatch second-brain vault. "
        "Use it to interpret a note's tags and to choose the right words when "
        "writing one.",
        "",
        "**Why it exists.** The vault grew four independent vocabularies that "
        "name overlapping things, so the same concept appears under several "
        "spellings (`advanced_air_mobility`, `aam`, `evtol`, `vertiport`, "
        "`Advanced Air Mobility`). Every spelling below is equivalent; treat "
        "them as one concept.",
        "",
        "**How to use it.**",
        "- Reading: map any tag you see to its concept `id` here before reasoning about it.",
        "- Writing: prefer the concept `id` as the tag. Never invent a new spelling for a concept that already exists.",
        "- Facets are independent. A note carries at most one genre, one lifecycle state and one provenance, plus any number of domains and entities.",
        "",
    ]
    if corpus_facts:
        header += [
            "**Corpus facts you must not get wrong.**",
            f"- {corpus_facts.get('indexed_documents','?')} indexed documents, but only "
            f"{corpus_facts.get('curated_notes','?')} are curated notes — the rest is raw RSS intake. "
            "Quote the curated figure when describing the vault's size.",
            f"- {corpus_facts.get('triage_backlog','?')} notes are untriaged.",
            "",
        ]

    footer = [
        "## Provenance rules for anything you assert",
        "",
        "- A note tagged `auto` is model output over feed input, not verified fact. "
        "Attribute it to the producing skill, never to the platform.",
        "- A note tagged `manual` or `authored` was deliberately kept by a human — weight it higher.",
        "- A note tagged `citable` has been cleared by the operator for external use. Nothing else has.",
        "- Raw feed items are evidence, not conclusions.",
        "",
    ]

    # Degradation ladder, most complete first.
    #
    # Ordering is not "biggest block first" -- it is by how badly the reader
    # misbehaves without each part. A live test against
    # corporatetraveldc-pi5-chat at a 6000-char budget exposed the first
    # version of this ladder: it dropped the genre and provenance sections
    # before entity and definition content, so the model was asked for a genre
    # id with no genre vocabulary in front of it and answered with a DOMAIN id
    # instead (inventing `concierge_luxuryty_travel`). Meanwhile the footer
    # went on stating provenance rules for tags the model could no longer see.
    #
    # So: the facet LABEL lists for domain, genre and provenance are the last
    # things to go, because every rule in the footer is expressed in them.
    # Definitions and the entity roster are comparatively decorative -- a
    # reader can still file a note correctly without them.
    tiers = [
        # everything
        lambda: (header + domain_block(True) + facet_block("genre", True)
                 + facet_block("lifecycle", True) + facet_block("provenance", True)
                 + entity_block() + footer),
        # drop non-domain definitions
        lambda: (header + domain_block(True) + facet_block("genre", False)
                 + facet_block("lifecycle", False) + facet_block("provenance", False)
                 + entity_block() + footer),
        # drop the entity roster
        lambda: (header + domain_block(True) + facet_block("genre", False)
                 + facet_block("lifecycle", False) + facet_block("provenance", False)
                 + footer),
        # drop domain definitions -- labels and aliases still present
        lambda: (header + domain_block(False) + facet_block("genre", False)
                 + facet_block("lifecycle", False) + facet_block("provenance", False)
                 + footer),
        # drop lifecycle; keep the three facets the footer's rules refer to
        lambda: (header + domain_block(False) + facet_block("genre", False)
                 + facet_block("provenance", False) + footer),
    ]
    for build in tiers:
        text = "\n".join(build())
        if len(text) <= budget:
            return text
    # Even the smallest tier is over budget. Return it whole rather than
    # truncating: a half-cut concept list is worse than a complete small one,
    # because the reader cannot tell that anything is missing.
    return text


def write_all(out_dir: str | None = None, ontology_path: str | None = None,
              budget: int = DEFAULT_BUDGET,
              corpus_facts: dict | None = None) -> dict:
    m = load(ontology_path)
    out_dir = out_dir or LIVE_DIR
    os.makedirs(out_dir, exist_ok=True)

    paths = {}
    p = os.path.join(out_dir, "semantic_layer.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(to_json(m), f, indent=1)
    paths["json"] = p

    p = os.path.join(out_dir, "semantic_layer.ttl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(to_turtle(m))
    paths["turtle"] = p

    pack = to_context_pack(m, budget=budget, corpus_facts=corpus_facts)
    p = os.path.join(out_dir, "SEMANTIC_LAYER.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(pack)
    paths["context_pack"] = p
    paths["context_pack_chars"] = len(pack)
    paths["context_pack_budget"] = budget
    return paths
