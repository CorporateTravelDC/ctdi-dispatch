"""
second_brain.semantic.model -- load, validate and query the second-brain
semantic layer.

WHY THIS EXISTS
---------------
The vault grew four independent vocabularies that name overlapping things,
plus a free-text tag string with no schema:

  1. shared/rss_catalog.py  _RSS_CATALOG keys + _CONCEPT_ALIASES
                            (snake_case domains; alias-aware, but the aliases
                            are only ever consulted to stop the PWA creating
                            a duplicate *feed category* -- nothing else in the
                            platform can see them)
  2. knowledge_graph/lexicon.py  LEXICON / SUBTYPE_OF
                            (Title Case entity labels + a 7-value subtype CV;
                            the authority for [[wikilink]] hub names)
  3. index_db.py            _FOLDER_CATEGORY
                            (vault folder -> a third naming style:
                            reference/media/inbox/sources/...)
  4. client_entity_ingest.py  _CATEGORY_PATTERNS
                            (vendor-dispatch/vendor-limo/vendor-restaurant)

  + vault_documents.tags / vault_notes_fts.tags: a comma string in which
    subject, cadence, provenance and workflow state are all flattened
    together with no delimiter between facets.

Measured against the live index on 2026-08-18: 95 distinct tags exist; the
pre-existing canonical_concept() resolves 16 of them and 4 of the 68 lexicon
labels. So ~83% of the vault's own vocabulary was unreachable from the one
alias resolver the platform had.

This module is the reconciliation point. It does NOT replace those four --
each is still the authority for its own job -- it declares the concepts they
are all circling and records the crosswalk, so a query for one surface form
finds content filed under any of the others.

DESIGN CONSTRAINTS, AND WHY
---------------------------
* **No credentials required.** Deliberately imports neither webdav_client nor
  index_db: both call _require_nextcloud_user() at module scope and raise
  RuntimeError on import when NEXTCLOUD_ADMIN_USER is unset. Reading the
  semantic layer must work in any container and any agent shell with no
  secrets at all, so this module talks to the SQLite index directly and keeps
  the ontology itself a plain file read.
* **No common.llm import, ever.** llm.py's _verify_before_inference() resolves
  the calling file off the stack and raises IntegrityCheckFailed if it is not
  in the signed manifest. A module that never participates in an inference
  call can never trip that, which keeps the semantic layer usable from an
  unsigned working tree.
* **Deterministic, never LLM-classified.** Same standing bias already recorded
  for lexicon.py and retrofit_links.py in docs/SECOND_BRAIN_STATUS.md:
  reviewable and byte-identical on identical input, forever.
* **Additive.** Nothing here rewrites a note, a tag, or any of the four
  existing vocabularies. Divergence is REPORTED (see --drift), never silently
  patched, because rss_catalog.py is live PWA behaviour under the signed
  manifest.

USAGE
-----
    from second_brain.semantic import load

    m = load()
    m.resolve("EV tolls")          -> Concept(id='advanced_air_mobility', ...)
    m.expand("aam")                -> every known surface form, sorted
    m.fts_query("aam")             -> '"advanced_air_mobility" OR "aam" OR ...'
    m.concepts(facet="domain")     -> declared domain concepts
    m.ancestors("advanced_air_mobility") -> ['aviation']
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ONTOLOGY_PATH = os.environ.get(
    "SECOND_BRAIN_ONTOLOGY",
    os.path.join(_PKG_DIR, "ontology.json"),
)

# Same normalisation rule as shared/rss_catalog._normalize_label, on purpose:
# the two must agree exactly or --drift would report phantom divergence that
# is really just a tokenisation difference.
_NORM_RE = re.compile(r"[^a-z0-9]")


def normalize(s: str) -> str:
    """'Advanced Air Mobility', 'advanced_air_mobility', 'AAM' and 'A.A.M.'
    all collapse to a comparable key. Aggressive by design so spacing,
    punctuation and casing can never hide a match."""
    return _NORM_RE.sub("", (s or "").lower())


@dataclass(frozen=True)
class Concept:
    id: str
    facet: str
    pref_label: str
    definition: str = ""
    scope_note: str = ""
    alt_labels: tuple[str, ...] = ()
    # Forms that must keep RESOLVING to this concept but must not be used to
    # expand a full-text query. Identifier lookup and free-text retrieval have
    # opposite error costs: resolving 'utm' to Advanced Air Mobility is
    # correct, while full-text-matching 'utm' drags in every note whose body
    # contains a utm_source= tracking URL. One vocabulary, two precision
    # regimes -- so the distinction is declared, not left to each caller.
    noisy_forms: tuple[str, ...] = ()
    broader: str | None = None
    related: tuple[str, ...] = ()
    observed_as: dict = field(default_factory=dict)
    source: str = "ontology"          # 'ontology' | 'lexicon'
    entity_subtype: str | None = None  # only for source='lexicon'
    # Cross-facet relation: the domain an entity belongs to. An entity is not
    # a *narrower* concept than a domain (different facet, different scheme),
    # so this is NOT `broader` -- but a note mentioning the entity IS a note
    # about that domain, so assignment follows it. SKOS-wise this exports as
    # a dedicated sb:inDomain predicate, not skos:broader.
    in_domain: str | None = None

    @property
    def uri_slug(self) -> str:
        return f"{self.facet}/{self.id}"

    def surface_forms(self, *, for_search: bool = False) -> list[str]:
        """Every string that should resolve to this concept, sorted and
        de-duplicated. Includes the id itself: writers emit the id as a tag
        (`advanced_air_mobility`) as often as they emit a label.

        for_search=True drops `noisy_forms` -- see that field's comment.
        """
        forms = {self.pref_label, self.id, *self.alt_labels}
        for key in ("note_tags", "rss_category", "lexicon_label",
                    "ingest_methods", "skills", "ollama_models"):
            v = self.observed_as.get(key)
            if isinstance(v, str):
                forms.add(v)
            elif isinstance(v, list):
                forms.update(v)
        if for_search:
            noisy = {n.lower() for n in self.noisy_forms}
            forms = {f for f in forms if f.lower() not in noisy}
        return sorted({f for f in forms if f})


@dataclass(frozen=True)
class Facet:
    id: str
    pref_label: str
    definition: str = ""
    scope_note: str = ""
    polyhierarchy: bool = False
    populated_from: str | None = None


@dataclass(frozen=True)
class Metric:
    id: str
    pref_label: str
    definition: str
    sql: str
    unit: str = ""
    scope_note: str = ""
    grain: str = "corpus"


@dataclass(frozen=True)
class Agent:
    id: str
    pref_label: str
    kind: str
    writes_genre: str = ""
    writes_provenance: str = ""
    authority: str = ""
    domains: str = ""


class SemanticModelError(RuntimeError):
    """Raised for a structurally invalid ontology -- a dangling `broader`,
    a duplicate concept id, an unknown facet. Loud on purpose: a silently
    half-loaded vocabulary produces confidently wrong answers, which is
    worse than no answer."""


class SemanticModel:
    def __init__(self, raw: dict, *, with_lexicon: bool = True):
        self.raw = raw
        self.version: str = raw.get("version", "0")
        self.namespace: str = raw.get("namespace", "urn:second-brain:")
        self.prefix: str = raw.get("prefix", "sb")
        self.governance: dict = raw.get("governance", {})
        self.problem_statement: dict = raw.get("problem_statement", {})
        self.assignment_rules: dict = raw.get("assignment_rules", {})
        self.consumers: dict = raw.get("consumers", {})

        self._facets: dict[str, Facet] = {}
        self._concepts: dict[str, Concept] = {}
        self._index: dict[str, list[str]] = {}   # normalized form -> concept ids
        self._metrics: dict[str, Metric] = {}
        self._agents: dict[str, Agent] = {}
        # lexicon label -> concept id, when a curated entity label turned out
        # to name a concept the ontology already declares. This IS the
        # crosswalk the vault never had.
        self.lexicon_crosswalk: dict[str, str] = {}
        self.unmapped_entities: list[str] = []
        self.stale_entity_domain_keys: list[str] = []

        self._load_facets()
        self._load_concepts()
        if with_lexicon:
            self._load_lexicon_entities()
        self._load_metrics()
        self._load_agents()
        self._build_index()
        self._validate()

    # ── loading ──────────────────────────────────────────────────────────

    def _load_facets(self) -> None:
        for f in self.raw.get("facets", []):
            self._facets[f["id"]] = Facet(
                id=f["id"],
                pref_label=f.get("pref_label", f["id"]),
                definition=f.get("definition", ""),
                scope_note=f.get("scope_note", ""),
                polyhierarchy=bool(f.get("polyhierarchy")),
                populated_from=f.get("populated_from"),
            )

    def _load_concepts(self) -> None:
        for c in self.raw.get("concepts", []):
            cid = c["id"]
            if cid in self._concepts:
                raise SemanticModelError(f"duplicate concept id: {cid!r}")
            self._concepts[cid] = Concept(
                id=cid,
                facet=c["facet"],
                pref_label=c.get("pref_label", cid),
                definition=c.get("definition", ""),
                scope_note=c.get("scope_note", ""),
                alt_labels=tuple(c.get("alt_labels", ())),
                noisy_forms=tuple(c.get("noisy_forms", ())),
                broader=c.get("broader"),
                related=tuple(c.get("related", ())),
                observed_as=c.get("observed_as", {}),
            )

    def _load_lexicon_entities(self) -> None:
        """Fold the curated entity lexicon in as the `entity` facet rather than
        restating 68 entries here.

        Two outcomes per lexicon label, decided by the lexicon's OWN subtype
        controlled vocabulary rather than by accidental string collision:

          * subtype 'topic' AND the label already names a declared concept ->
            record a CROSSWALK ('Advanced Air Mobility' -> the domain concept)
            and mint nothing. A lexicon topic and an ontology domain really are
            the same kind of thing, so two concepts for it would be the exact
            duplication this layer exists to end.
          * everything else (person/agent/org/place/system/project, or any
            unmatched topic) -> mint an entity concept whose alt_labels are
            recovered from the lexicon's own regex alternation, which is safe
            to parse because lexicon.py builds every pattern as
            rf"\\b(?:{pat})\\b".

        Deciding on subtype rather than on "did the string happen to match"
        matters: an earlier revision let a domain's *observed evidence* tags
        (platform_engineering having been seen tagged 'swim') absorb the SWIM
        system entity, so SWIM stopped existing as a thing and started being a
        synonym for a domain. Evidence of co-occurrence is not identity.

        Import is lazy and failure is non-fatal: lexicon.py is pure regex with
        no credential requirement today, but the semantic layer must stay
        loadable even if that ever changes.
        """
        try:
            from second_brain.knowledge_graph.lexicon import LEXICON
        except Exception:
            return

        entity_domains = (self.raw.get("entity_domains") or {}).get("map", {})
        declared = self._index_preview()
        for label, subtype, pattern in LEXICON:
            norm = normalize(label)
            if subtype == "topic" and norm in declared:
                self.lexicon_crosswalk[label] = declared[norm]
                continue
            cid = "entity_" + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
            if cid in self._concepts:
                continue
            dom = entity_domains.get(label)
            self._concepts[cid] = Concept(
                id=cid,
                facet="entity",
                pref_label=label,
                definition=f"{subtype.capitalize()} tracked by the vault entity lexicon.",
                alt_labels=tuple(_alternatives_from_pattern(pattern.pattern)),
                observed_as={"lexicon_label": label},
                source="lexicon",
                entity_subtype=subtype,
                in_domain=dom,
            )
        self.unmapped_entities = sorted(
            c.pref_label for c in self._concepts.values()
            if c.source == "lexicon" and not c.in_domain
        )
        # entity_domains keys are lexicon labels VERBATIM. A key that matches
        # no lexicon entry is dead weight -- a typo, or an entity that was
        # renamed or removed from lexicon.py since this map was written. It
        # would otherwise fail silently and invisibly, so it is recorded and
        # reported by --drift rather than raising: a stale crosswalk entry is a
        # governance finding, not a reason the vocabulary refuses to load.
        lexicon_labels = {label for label, _s, _p in LEXICON}
        crosswalked = set(self.lexicon_crosswalk)
        self.stale_entity_domain_keys = sorted(
            k for k in entity_domains
            if k not in lexicon_labels and k not in crosswalked
        )

    def _index_preview(self) -> dict[str, str]:
        """Normalised surface form -> concept id, over ONLY the concepts
        declared in ontology.json. Used to decide whether a lexicon label is
        already covered, before the full index is built."""
        preview: dict[str, str] = {}
        for c in self._concepts.values():
            for form in c.surface_forms():
                preview.setdefault(normalize(form), c.id)
        return preview

    def _load_metrics(self) -> None:
        for m in self.raw.get("metrics", []):
            self._metrics[m["id"]] = Metric(
                id=m["id"], pref_label=m.get("pref_label", m["id"]),
                definition=m.get("definition", ""), sql=m["sql"],
                unit=m.get("unit", ""), scope_note=m.get("scope_note", ""),
                grain=m.get("grain", "corpus"),
            )

    def _load_agents(self) -> None:
        for a in self.raw.get("agents", []):
            self._agents[a["id"]] = Agent(
                id=a["id"], pref_label=a.get("pref_label", a["id"]),
                kind=a.get("kind", ""), writes_genre=a.get("writes_genre", ""),
                writes_provenance=a.get("writes_provenance", ""),
                authority=a.get("authority", ""), domains=a.get("domains", ""),
            )

    def _build_index(self) -> None:
        for c in sorted(self._concepts.values(), key=lambda x: x.id):
            for form in c.surface_forms():
                n = normalize(form)
                if not n:
                    continue
                bucket = self._index.setdefault(n, [])
                if c.id not in bucket:
                    bucket.append(c.id)

    def _validate(self) -> None:
        for c in self._concepts.values():
            if c.facet not in self._facets:
                raise SemanticModelError(
                    f"concept {c.id!r} declares unknown facet {c.facet!r}")
            if c.broader and c.broader not in self._concepts:
                raise SemanticModelError(
                    f"concept {c.id!r} has dangling broader {c.broader!r}")
            for r in c.related:
                if r not in self._concepts:
                    raise SemanticModelError(
                        f"concept {c.id!r} has dangling related {r!r}")
            if c.in_domain and c.in_domain not in self._concepts:
                raise SemanticModelError(
                    f"concept {c.id!r} has dangling in_domain {c.in_domain!r}")
            if c.in_domain and self._concepts[c.in_domain].facet != "domain":
                raise SemanticModelError(
                    f"concept {c.id!r} in_domain {c.in_domain!r} is not a domain")
        # cycle check on broader
        for c in self._concepts.values():
            seen, cur = {c.id}, c.broader
            while cur:
                if cur in seen:
                    raise SemanticModelError(
                        f"broader cycle involving {c.id!r}")
                seen.add(cur)
                cur = self._concepts[cur].broader

    # ── query API ────────────────────────────────────────────────────────

    def facets(self) -> list[Facet]:
        return sorted(self._facets.values(), key=lambda f: f.id)

    def facet(self, fid: str) -> Facet | None:
        return self._facets.get(fid)

    def concepts(self, facet: str | None = None,
                 source: str | None = None) -> list[Concept]:
        out = list(self._concepts.values())
        if facet:
            out = [c for c in out if c.facet == facet]
        if source:
            out = [c for c in out if c.source == source]
        return sorted(out, key=lambda c: (c.facet, c.id))

    def get(self, concept_id: str) -> Concept | None:
        return self._concepts.get(concept_id)

    def resolve_all(self, term: str) -> list[Concept]:
        """Every concept `term` could mean, most-specific first.

        Ambiguity is real and must not be hidden: 'rail' legitimately names
        both Rail Transport and the Rail-and-Marine bundle it sits under.
        Assignment wants both (broader closure would add the parent anyway);
        a human lookup wants the specific one first.
        """
        ids = self._index.get(normalize(term), [])
        cs = [self._concepts[i] for i in ids]
        return sorted(cs, key=lambda c: (-self._depth(c.id),
                                         normalize(c.pref_label) != normalize(term),
                                         c.id))

    def resolve(self, term: str) -> Concept | None:
        """Single best concept for `term`, or None. Prefers the most specific
        concept, then an exact pref_label hit, then id order -- fully
        deterministic, no scoring heuristics."""
        got = self.resolve_all(term)
        return got[0] if got else None

    def _depth(self, concept_id: str) -> int:
        d, cur = 0, self._concepts[concept_id].broader
        while cur:
            d += 1
            cur = self._concepts[cur].broader
        return d

    def ancestors(self, concept_id: str) -> list[str]:
        out, cur = [], self._concepts[concept_id].broader
        while cur:
            out.append(cur)
            cur = self._concepts[cur].broader
        return out

    def descendants(self, concept_id: str) -> list[str]:
        kids = [c.id for c in self._concepts.values() if c.broader == concept_id]
        out = list(kids)
        for k in kids:
            out.extend(self.descendants(k))
        return sorted(set(out))

    def closure(self, concept_id: str) -> list[str]:
        """Everything a note is implicitly about, given that it is explicitly
        about `concept_id`: the concept, its `broader` ancestors, and -- for an
        entity -- its domain and that domain's ancestors.

        This is what makes the entity facet pay for itself. A note mentioning
        Joby Aviation becomes findable under Advanced Air Mobility and under
        Aviation, without anyone having tagged it with either.
        """
        out = [concept_id, *self.ancestors(concept_id)]
        dom = self._concepts[concept_id].in_domain
        if dom and dom in self._concepts:
            for cid in [dom, *self.ancestors(dom)]:
                if cid not in out:
                    out.append(cid)
        return out

    def expand(self, term: str, *, include_descendants: bool = True,
               for_search: bool = False) -> list[str]:
        """Every surface form that should be treated as equivalent to `term`.

        This is the whole point of the layer in one call: one word in, every
        spelling any writer in this system has ever used out.
        """
        c = self.resolve(term)
        if not c:
            return []
        ids = [c.id] + (self.descendants(c.id) if include_descendants else [])
        forms: set[str] = set()
        for cid in ids:
            forms.update(self._concepts[cid].surface_forms(for_search=for_search))
        return sorted(forms)

    def fts_query(self, term: str, *, include_descendants: bool = True) -> str:
        """An FTS5 MATCH expression covering every surface form of `term`.

        Each form is phrase-quoted for the same reason index_db.search_notes
        phrase-wraps by default: FTS5 reads a bare hyphen as a column-filter /
        NOT operator, so an unquoted `gig-economy` raises 'no such column:
        economy' instead of matching text.
        """
        forms = self.expand(term, include_descendants=include_descendants,
                            for_search=True)
        if not forms:
            return ""
        return " OR ".join(f'"{f}"' for f in forms)

    def metrics(self) -> list[Metric]:
        return sorted(self._metrics.values(), key=lambda m: m.id)

    def metric(self, mid: str) -> Metric | None:
        return self._metrics.get(mid)

    def agents(self) -> list[Agent]:
        return sorted(self._agents.values(), key=lambda a: a.id)

    def agent(self, aid: str) -> Agent | None:
        return self._agents.get(aid)

    def stats(self) -> dict:
        by_facet: dict[str, int] = {}
        for c in self._concepts.values():
            by_facet[c.facet] = by_facet.get(c.facet, 0) + 1
        return {
            "version": self.version,
            "facets": len(self._facets),
            "concepts": len(self._concepts),
            "concepts_by_facet": dict(sorted(by_facet.items())),
            "declared_concepts": len(self.concepts(source="ontology")),
            "lexicon_entity_concepts": len(self.concepts(source="lexicon")),
            "lexicon_labels_reconciled": len(self.lexicon_crosswalk),
            "surface_forms": len(self._index),
            "ambiguous_forms": sum(1 for v in self._index.values() if len(v) > 1),
            "metrics": len(self._metrics),
            "agents": len(self._agents),
        }


def _alternatives_from_pattern(pattern: str) -> list[str]:
    r"""Recover the literal alternatives from a lexicon regex.

    lexicon.py builds every entry as rf"\b(?:{pat})\b", so peeling that
    wrapper and splitting the top level on '|' returns exactly the surface
    forms the author wrote. Anything still carrying regex metacharacters
    after unescaping is dropped rather than guessed at -- a wrong alt_label
    silently mis-files notes, which is worse than a missing one.
    """
    m = re.match(r"^\\b\(\?:(.*)\)\\b$", pattern, re.DOTALL)
    body = m.group(1) if m else pattern
    out: list[str] = []
    depth = 0
    current = ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "|" and depth == 0:
            out.append(current)
            current = ""
        else:
            current += ch
    out.append(current)

    cleaned: list[str] = []
    for alt in out:
        alt = alt.strip()
        # optional trailing 's' -- 'NOTAMs?' means NOTAM and NOTAMs
        if alt.endswith("s?"):
            cleaned.append(alt[:-2])
            alt = alt[:-1]
        alt = alt.replace("\\.", ".").replace("\\-", "-").replace("[- ]", " ")
        alt = alt.replace("?", "")
        if not alt or re.search(r"[\\\[\]()*+{}|^$]", alt):
            continue
        cleaned.append(alt)
    return sorted({c for c in cleaned if c})


@lru_cache(maxsize=4)
def _load_raw(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load(path: str | None = None, *, with_lexicon: bool = True) -> SemanticModel:
    """Load the semantic layer. Cheap and cached; safe to call per request."""
    return SemanticModel(_load_raw(path or ONTOLOGY_PATH),
                         with_lexicon=with_lexicon)
