# Causal reasoning + auto-selfheal roadmap

Operator directive, 2026-08-23: the second-brain ontology/semantic layer
should grow to serve two things at once — a growing multi-modal knowledge
base (research notes, OSINT, entity tracking, RSS, etc.) **and** the
operational debugging/auto-selfheal work this repo has been hardening over
the preceding several days (thermal-ingest-guard's LOCKDOWN mechanism, the
watchdog, Ollama contention handling). This file is the standing roadmap so
that intent survives across sessions rather than living only in one
conversation. Update this file, don't recreate it, as phases complete.

## Phase 0 — DONE, 2026-08-23. Causal edges exist.

`derivation` facet (ontology v1.1.0: `leans_on`/`derives_from`/`reutilizes`),
`semantic_note_derivations(path, relation, target, evidence)`, populated by
`assign_derivations()` parsing a `## Provenance` section on every new
second-brain write (see [[feedback_document_immediately]] in Claude's own
memory, and `docs/SECOND_BRAIN_STATUS.md` for the exact format). This is
data capture only — no reasoning over it yet at this phase.

## Phase 1 — DONE, 2026-08-23. Causal reasoning queries.

`src/second_brain/semantic/compile.py::trace_causal_chain()` +
`python3 -m second_brain.semantic --trace NOTE` / `--depends-on
NOTE_OR_TARGET` (`--max-depth N`, default 5, inclusive — depths 0..N
explored). Real multi-hop graph traversal over the derivation edges:

- `--trace`: "what led to this" — backward from a note through its own
  `leans_on`/`derives_from`/`reutilizes` edges, recursing into any target
  that resolves to a real vault note, surfacing an unresolved target
  (still the majority of edges today — most Provenance lines are honest
  free text, not another note) as a terminal leaf rather than dropping it.
- `--depends-on`: the reverse — what (transitively) leans on, derives
  from, or reutilizes a given note or named mechanism.
- Cycle-safe (visited-set), tested against a real multi-hop chain from
  tonight's own session history (`20260823T133344Z.md` → `20260823T064349Z.md`).

This makes the causal record *queryable*, not just stored — the actual
reasoning gap Phase 0 alone didn't close.

## Phase 1.5 — DONE, 2026-08-24 (corrected same session). Chronology, unified into the one causal chain.

Operator directive: close the "everything prior to tonight" gap — but
honestly, not by fabricating causal claims for content nobody can verify
the real reasoning behind. `assign_chronology()` links every note to its
immediate predecessor under each shared topical concept (real timestamp
order, `email.utils.parsedate_to_datetime()` on `vault_documents.mtime`,
NOT the coarser `indexed_at`), plus a sequence number — `--history
CONCEPT_ID`.

**Correction, same session, not a later pass:** the first version stored
this in a separate table (`semantic_note_chronology`) with its own
command, invisible to `--trace`/`--depends-on`. The operator rejected
that outright — *"I don't want it as a second mechanism... I want it
baked into the causal chain. It's the whole point"* — and it was rebuilt
the same pass to write into `semantic_note_derivations` (the exact table
`trace_causal_chain()` already walks) as `relation='preceded_by'`,
`kind='chronological'`, alongside real authored edges at
`kind='evidenced'`. One graph, one traversal, for the whole vault; `kind`
on every returned edge is what keeps the honesty, not a second query
surface. See CLAUDE.md's chronology section for the live-verified proof
(`--trace` on a real Provenance-less note now returns real chronological
ancestry instead of nothing).

This is what "strictly chronological, prior to the 18th" — the
operator's own framing — actually means in the schema: a real, useful,
honestly-labeled part of the SAME timeline for the ~7,000+ notes (and
any future note lacking a real Provenance section) that can never have a
genuine causal edge, because
nobody can retroactively verify what they actually leaned on.

**On "linking new vocabulary / an Uber project / a Cowork series note /
personal downloads / reports across all of them" (the operator's other
ask, same message):** checked what's actually real in the vault before
building anything that would imply coverage that doesn't exist yet:
- **Real, substantial content already in the vault**: a genuine "Uber
  Series" of research articles (`Notes/Uber Series/`,
  `corporatetraveldc/01-Sources/personal-notes/Research - Uber Series/`,
  and a third near-duplicate path under `.../Series/Uber Series/` — worth
  a dedup pass sometime, not done here). This already gets picked up by
  the existing topical layer via the `gig_economy`/`uber` concepts (see
  the cross-link findings under `00-Inbox/cross-link-findings/gig_economy-uber.md`),
  and Phase 1.5's chronology mechanism links it into the same timeline
  as every other gig-economy note automatically — no bespoke code needed,
  it's the same mechanism, driven by the same existing concept tags.
- **Not real yet**: `corporatetraveldc-personal-export-watch.service`'s
  own docstring says "LinkedIn is the only source wired in today... Uber/Lyft
  plug into exist, it isn't LinkedIn-specific itself" — and the only
  vault content matching LinkedIn export analysis is a single
  `export-analysis-linkedin_TEST_VERIFICATION-2026-08-07.md` note. There
  is no real LinkedIn or Uber/Lyft personal-download analysis in the
  vault yet to link anything to. Building a linking mechanism for data
  that doesn't exist would itself be a form of the over-claiming this
  whole phase exists to avoid — nothing built here promises coverage of
  those sources; they'll join the same graph automatically, for free,
  the moment `personal-export-watch` actually produces real tagged notes,
  since chronology/topical tagging both work off whatever's really in
  the vault, not a hardcoded source list.

## Phase 2 — NOT STARTED. Operational entity registration.

The `entity` facet's lexicon (`second_brain.knowledge_graph.lexicon.LEXICON`,
subtype `system`) is the natural place to register the operational
components this repo keeps root-causing incidents against —
`thermal-ingest-guard`, `corporatetraveldc-watchdog`, `ollama.service`,
`LOCKDOWN`, the guard's fallback-count trigger, etc. — as real, curated
entities, the same way `[[Advanced Air Mobility]]` or `[[Joby Aviation]]`
already are for the research side of the vault. Once registered:

- `retrofit_links.py` starts backlinking every incident write-up that
  mentions them, for free.
- The `entity` facet tags every incident note by which system component(s)
  it involves, making "every incident that has ever touched the watchdog"
  a real query (`semantic_search`/`--search`), not a CLAUDE.md grep.
- It's the real prerequisite for Phase 3 below — an auto-selfheal query
  needs a stable, curated vocabulary of *which component failed* to look
  anything up against.

Deliberately not started 2026-08-23 — the operator chose to build Phase 1
(causal-reasoning queries) first and explicitly asked to plan, not build,
Phase 3 the same night. This phase is the natural next step whenever
picked back up.

## Phase 3 — END-GOAL STATE, multi-day, NOT STARTED. Auto-selfheal consulting the graph.

The ambition: `thermal-ingest-guard.py` / `scripts/watchdog.sh` (or a new
shared decision layer both call into) consult the causal-reasoning graph
*before* acting, instead of — or in addition to — the fixed-threshold
rules they run on today. Concretely, something like: "this failure
signature (container X down, guard tier Y, load Z) has a causal history —
what fixed it last time, was it a real fix or a symptom-suppression, is
there an open NEEDS OPERATOR DECISION against this exact component" —
queried at decision time, not just written down for a human to read later.

**This phase inherits every safety lesson this repo has already paid for
in the preceding days, it does not get a pass on any of them:**

- Fail open, never fail silent-and-wrong. A missing/stale/unparseable
  graph must degrade to today's existing threshold behavior, exactly like
  `_guard_tier()`'s fail-open-to-0 design (see CLAUDE.md's watchdog
  section) — a knowledge-graph outage must never become a NEW reason
  something fails to recover, or fails to alert.
- Alert-first bias holds. Every existing "never auto-act on X, alert with
  the exact command a human should run" gate in `watchdog.sh`
  (`--allow-system-restart`, the `SYSTEM_SERVICES` restriction) stays —
  a richer causal signal is grounds for a *better alert*, not
  automatically grounds for a *wider blast radius of unattended action*.
- Deterministic, grounded, auditable — same standing bias as the rest of
  this semantic layer (`ontology.json`'s own governance rules: "no
  aspirational concepts," "deterministic and reviewable, never
  LLM-in-the-loop"). A selfheal decision consulting this graph must be
  able to cite exactly which prior incident/edge it's acting on, the same
  way `semantic_note_concepts` records `rule`+`evidence` for every
  assignment today.
- This repo has *already* shipped two real, root-caused incidents this
  week from auto-restart logic overreaching (the watchdog-vs-LOCKDOWN
  collision, the 2026-08-22 `/healthz`-timeout self-perpetuating loop —
  see CLAUDE.md's watchdog section for both). Phase 3 is exactly the kind
  of capability that could make a *future* version of that mistake harder
  to catch, not easier, if built carelessly. Treat it accordingly: this
  is a multi-day, carefully-reviewed effort, not a bolt-on.

No design has been committed to yet beyond this statement of intent and
constraints. Whoever picks this up next should re-read CLAUDE.md's whole
watchdog section first — it's the concentrated, hard-won lesson this
phase exists to eventually encode, not bypass.
