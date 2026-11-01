# Shared platform context migration

This source change removes the repository CLAUDE.md. Account-owned/root-owned
onboarding pamphlets are separate identity documents and remain unchanged.

The reviewed private source is config/platform-context.md. The operator installs
its signed revision at /srv/ctdc-context/PLATFORM_CONTEXT.md with directory
mode 2750 and file mode 0640, operator ownership and ctdc-dev group read access.
Agents contribute proposed changes; the operator maintains the installed revision.
The shared directory is a deployment location. Durable decisions/history remain
in the canonical vault; the versioned source restores a lost installation.

Local Codex sessions follow the AGENTS.md adapter. Claude sessions receive the
same bytes through the project SessionStart hook. Both use scripts/platform-context.py;
a missing, writable, symlinked or divergent installation fails closed. The reader
checks the manifest entry and host source; installation also invokes native signature
verification. The web image reads the same installed file through a read-only mount
and binds it to its signed manifest entry, without a second instructions copy.

Cloud agents use the existing research_read connector tool with the exact resource
name platform-context. The existing authenticated HTTP research route serves that
same resource with a SHA256 and source_kind=shared_file. It is not a vault note,
does not accept a filesystem path and does not widen other research scopes. The
existing CUI/PII scrub gate still applies. Unauthenticated reads remain denied.

The neutral checker is scripts/check-platform-drift.sh. All 13 prior deterministic
checks are retained. Old checker/daily script names remain compatibility entry points
for installed callers; the existing daily timer identity is retained so no duplicate
schedule is enabled. The signing gate now refuses a missing checker or a requested
drift bypass. Native callers use the neutral name. Weekly session-memory consolidation
archives and resets session memory only, after a confirmed retrievable vault write;
it cannot clear shared instructions or recreate the retired repo scratchpad.

The public scrub producer excludes the new private context source and native loader
files by exact path. It retains the retired filename exclusion, all prior exclusions,
substitutions, allowlists, filename scanning and independent final output verification.
The public catalog's upstream inventory is unchanged by these exact root-path drops.

## Compiler recovery acceptance

The graph builder records semantic_compile_seq, semantic_compiled_at and its actual
semantic_note_links count from one read-only repeatable-read database snapshot.
It accepts both account-relative and business-relative indexed paths only when they
resolve to curated graph nodes. Missing data, an unavailable semantic overlay or
zero matched semantic note links cannot replace the prior graph. All output rendering
finishes before per-file atomic replacement; JSON metadata replaces last. The pair
of output files is not a multi-file transaction. Publication errors fail the unit.

The graph path trigger follows the semantic-compiled sentinel, written only after
a semantic commit, and graph startup orders after an already-starting semantic job.
Existing scheduled timer grids remain unchanged.

scripts/brain-recovery-proof.py is operator-only. It pauses the four compiler trigger
units, selects the reviewed validation image through temporary Quadlet drop-ins, and
runs semantic compilation before graph compilation. It rejects old successes,
non-advancing compile sequences, mismatched ledger rows, stale graph revisions and
missing semantic note links. A failed validation never authorizes publication. The
four triggers remain paused until promotion/resumption; no running compiler is killed.
On failure, inspect the sanitized failure receipt and preserve the candidate.

After promotion, source/context parity and current compiler state are rechecked.
The relay publishes a sanitized BRAIN-RECOVERY-RECEIPT.json beside the shared context
and keeps private detailed receipts in operator state. RELEASE-PROGRESS.json reports
phase names only. These are dated observations, not guarantees about future runs.
Actual recovery is established only by a passing live receipt, not by isolated tests,
a document entry, a reset-failed or a passing drift checker.
