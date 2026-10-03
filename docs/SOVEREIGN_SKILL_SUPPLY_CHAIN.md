# Sovereign Skill Supply Chain

Operator-owned skill/plugin lifecycle: fork-in, own + sign + audit, run offline,
grow in the second brain, publish out — all under the operator's key, with no
external party able to mutate or silently host the operator's IP. This is the
fork-first doctrine (`pihole-unbound-selinux` model) applied to Claude skills.

See also: memory `project_sovereign_skill_supply_chain`,
`feedback_skills_local_vendor_fork`, `feedback_silent_cloud_egress_callout`.

## Why (regulated-operator rationale)
A Part 135-grade audit trail requires the operator to attest to exactly what
operational logic was in effect at any time, that every change was authorized,
and that nothing external mutated it. Cloud-synced skills (claude.ai as source
of truth, one-way download, overwritable) make that attestation impossible and
place the operator's IP on Anthropic infrastructure. This chain removes both
problems: skills are local, versioned, GPG-signed, and distributed by the
operator.

## Phase status (2026-09-28)
- **Phase 0 — control + offline foundation.**
  - claude.ai skill sync **disabled** (`~/.claude/settings.json` `syncClaudeAiSkills:false`); all 25 skills vendored to `~/.claude/skills/` (non-synced). Takes full effect next launch (synced/ trashed then).
  - Non-essential traffic **off** (`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING` in settings `env`).
  - Synced **plugins preserved** to `~/.claude/plugins-vendored/`. Plugin-sync off-switch: **OPEN item** (no confirmed separate toggle found; `syncClaudeAiSkills` governs skills only — confirm via `/config`).
  - **Distribution path** (below) replaces GitHub for multi-site.
- **Phase 1 — growth: DONE (first cut).** `scripts/skill-snapshot.sh` produces signed, offline-verifiable snapshots into the vault; first snapshot persisted.
- **Phase 2 — publish-out (pending):** package-sign-and-publish with author/publisher/license metadata (internal / external / public marketplace), IP retained. **Blocker:** parameterize embedded admin tokens (`ctdc_cowork_...` in flight-hifi-track / corporatetraveldc-dispatch-ops) out to env/secrets first.
- **Phase 3 — docs (this file + vault):** in progress.

## Mechanism — `scripts/skill-snapshot.sh`
Distribution without GitHub: the second-brain vault + a GPG-signed manifest.
Any site pulls the artifacts and verifies **offline** (sha256 + operator GPG),
then installs. No Anthropic sync, no external mutation, full audit trail.

- `scripts/skill-snapshot.sh create` — bundles `~/.claude/skills` (excludes
  `synced/`, `.trash`), writes `skills-snapshot-<UTC>.tar.gz` +
  `.manifest.txt` (tarball hash + per-file hashes), and uploads both to
  `01-Sources/personal-research/skill-snapshots/` (deliberately OUTSIDE Cowork
  read scope while tokens remain embedded).
- **Operator sign (your key):** `gpg --detach-sign --armor <manifest>` →
  upload the `.asc` to seal the snapshot.
- `scripts/skill-snapshot.sh --verify <manifest>` — offline integrity check
  (tarball hash, then GPG signature if the `.asc` is present).
- **Restore at another site (Phase 0.3 companion, to build):** download
  tarball + manifest + `.asc`, `--verify`, then extract into `~/.claude/skills/`.

## Audit procedure
1. Every skill change → new signed snapshot (create + operator sign).
2. Snapshots are immutable, timestamped, and accumulate in the vault (the
   "state of growth") — the versioned record of what logic existed when.
3. Any site proves integrity offline via `--verify` before loading skills.
4. Operational skills' audit-grade home is the repo `.claude/skills/`
   (git + signed manifest) once tokens are parameterized out (Phase 2 blocker).

## Open items
- Plugin-sync off-switch (confirm/close).
- Token parameterization (unblocks repo-inclusion, broader vault scope, publish-out).
- Restore/pull script for multi-site install.
- Phase 2 publish-out packaging.
