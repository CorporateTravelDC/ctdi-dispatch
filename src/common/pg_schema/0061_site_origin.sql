-- Migration 0061: site-of-origin marking on the semantic-layer graph tables.
--
-- Added 2026-09-21 ahead of geometric reasoning Phase 2/3, on the finding in
-- docs/ANP_FEDERATION_RESEARCH_2026-09-21.md: adding this column now is free
-- (every existing row takes the DEFAULT in a single ALTER), whereas adding it
-- after Phase 2/3 have generated causal/cluster edges means backfilling a
-- provenance value that is no longer trivially knowable -- a derived edge's
-- origin site cannot be reconstructed from the edge itself once multiple
-- sites contribute. Cheap now, expensive later; that asymmetry is the whole
-- reason this lands before the phases rather than with them.
--
-- This is NOT a commitment to ANP or to federation. That research concluded
-- ANP's identity layer is still at MVP stage and not a safe production bet
-- before 2027, and the federation buildout lives on its own branch. This
-- column is the one piece worth doing on main regardless, because its cost
-- is asymmetric in time and it is protocol-independent: "which site produced
-- this row" is a meaningful provenance question for a single-site deployment
-- too (it makes a restored backup, a migrated host, or a rebuilt vault
-- distinguishable in the graph).
--
-- Value semantics: 'local' is the sentinel for "produced by this
-- deployment, before any site identity was configured." Once a deployment
-- has a real site identifier, compile.py should write that instead; the
-- DEFAULT exists to make the ALTER free and to keep single-site deployments
-- working without configuring anything. Deliberately TEXT and not an
-- enum/FK -- site identifiers in a federated topology are assigned by
-- whoever runs the federation, not by this schema.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS.

-- The derivation graph itself: derivation / chronological / geometric edges
-- today, causal / cluster edges once Phase 2/3 land.
ALTER TABLE semantic_note_derivations
    ADD COLUMN IF NOT EXISTS site_origin TEXT NOT NULL DEFAULT 'local';

-- The Phase 0 instance-reference table (flight/train identifiers extracted
-- from note text, with resolved positions). Same reasoning: a federated
-- deployment needs to know which site's extractor produced a reference
-- before it can decide whether to trust or dedupe it against its own.
ALTER TABLE semantic_note_instance_refs
    ADD COLUMN IF NOT EXISTS site_origin TEXT NOT NULL DEFAULT 'local';

-- Not indexed on purpose. Every row in a single-site deployment carries the
-- same value, so an index would be pure write overhead with no selectivity.
-- Add one when a second site actually contributes rows and queries start
-- filtering on it -- not before.
