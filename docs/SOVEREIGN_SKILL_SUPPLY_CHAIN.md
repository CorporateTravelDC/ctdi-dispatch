# Sovereign Skill Supply Chain

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.

Operator-owned skill lifecycle: fork in, own + sign + audit, run offline,
grant per agent, publish out -- under the operator's key, with no external
party able to change or silently host the operator's skills.

## Why

A regulated-operator audit trail needs to show exactly what operational logic
was in effect at any time, that each change was authorized, and that nothing
external changed it. Cloud-synced skills (claude.ai as source of truth,
one-way download, overwritable) make that impossible and place the operator's
IP on a vendor's infrastructure.

## Where skills live now

| Location | What | Integrity |
|---|---|---|
| `skills/<name>/` in this repo (8: `corporatetraveldc-dispatch-ops`, `flight-hifi-track`, `hub-arrivals-lookup`, `linkedin-export-analyzer`, `morning`, `nec-train-hifi-track`, `overwater-adsb-handoff-track`, `second-brain-remember`) | the operator's own skills | covered by the signed tree manifest; the operator's `~/.claude/skills/` copies were byte-identical on 2026-10-06 |
| `skills/vendor-pins.txt` | 16 vendor skills pinned by tree hash (`built-in-browser`, `chrome-browser`, `computer-use`, `deep-research`, `doc-coauthoring`, `docs`, `docx`, `google-workspace`, `import-memory`, `internal-comms`, `mcp-builder`, `pdf`, `pptx`, `skill-creator`, `web-artifacts-builder`, `xlsx`) | the pin file is signed; the vendor bytes are **not** committed (several are proprietary-licensed) |
| `.claude/skills/` in this repo (`dispatch-context-guardian`, `personal-export-analysis`) | project skills | signed with the tree |
| `~/.claude/skills/` (operator) | the operator's working set (26 entries) | not signed in place; snapshotted (below) |

Embedded tokens: the repo skills no longer carry a live token. The remaining
`ctdc_cowork_` strings are placeholders or a note that the old literal was
revoked 2026-08-16. A trashed copy under `~/.claude/skills/.trash/` still
contains a token-shaped string [UNVERIFIED: whether it is revoked]; it is
outside the repo and excluded from snapshots.

## Distribution to agents: skill grants (2026-10-04)

Each agent account gets skills only by grant, with clawback.
`scripts/skill-grants.sh` + `scripts/lib/skill_grants.py`; grammar in
`/etc/ctdc-skill-grants.conf` (root 0644): `grant|deny <account|*> <skill|*>
[task=...] [until=ISO+offset]`, deny wins, expiry lapses both ways. The
catalog is the signed `skills/` tree plus the pinned vendor skills plus the
project skills. The root unit `corporatetraveldc-skill-grants.timer` (hourly)
runs the installed copy `/usr/local/libexec/ctdc/skill-grants.sh apply
--execute`, which first verifies the checkout against the signed manifest and
**holds** (changes nothing) if it does not verify. It then installs
root-owned read-only copies into each agent's home, removes revoked ones,
quarantines unsanctioned or agent-owned skill directories to
`~/.claude/.ctdc-skills-quarantine`, and restores in-place edits.

Live on 2026-10-06 18:06Z it was holding ("checkout does not verify against
the signed manifest -- nothing changed") because of an unsigned working-tree
change to `scripts/stack-refresh.sh`. Tests: `tests/scripts/test_skill_grants.py`
(28). Design: `docs/AGENT_SEGMENTATION.md` "Skills: grants and clawback".

## Operator's Claude Code settings (`~/.claude/settings.json`, outside the repo)

| Setting | Live value |
|---|---|
| `syncClaudeAiSkills` | `false` |
| `env.DISABLE_TELEMETRY` | `"1"` |
| `env.DISABLE_ERROR_REPORTING` | `"1"` |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | **not set** (the previous revision said it was) |

`~/.claude/skills/synced/` still exists (one entry, dated 2026-09-18); the
earlier expectation that it would be trashed on the next launch did not hold.
Synced plugins are preserved under `~/.claude/plugins-vendored/`. A plugin-sync
off-switch is still unconfirmed.

## Snapshots: `scripts/skill-snapshot.sh` (unchanged since 2026-09-28)

Distribution without GitHub: the second-brain vault plus a GPG-signed
manifest, verifiable offline.

- `skill-snapshot.sh create` -- bundles `~/.claude/skills` (excluding
  `synced/` and `.trash`) into `skills-snapshot-<UTC>.tar.gz` + a
  `.manifest.txt` (tarball hash + per-file hashes) and uploads both to
  `01-Sources/personal-research/skill-snapshots/` in the vault (outside the
  Cowork-readable scope).
- The operator seals a snapshot with `gpg --detach-sign --armor <manifest>`
  and uploads the `.asc`.
- `skill-snapshot.sh --verify <manifest>` -- tarball hash, then the GPG
  signature if present.

[UNVERIFIED: how many snapshots exist in the vault and whether they are
signed; the vault was not listed.]

## Phase status

- **Phase 0 -- control + offline foundation:** done, except the plugin-sync
  switch and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` (above).
- **Phase 1 -- growth (snapshots):** done (first cut).
- **Repo inclusion (was blocked on embedded tokens):** done -- operational
  skills are in `skills/`, signed with the tree, and distributed by grant.
- **Phase 2 -- publish-out** (signed packages with author/publisher/licence
  metadata): not started.
- **Restore at another site:** not built (download tarball + manifest +
  `.asc`, `--verify`, extract).

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-security.md`.


### Sovereign Skill Supply Chain

~~Operator-owned skill/plugin lifecycle: fork-in, own + sign + audit, run offline, grow in the second brain, publish out — all under the operator's key, with no external party able to mutate or silently host the operator's IP. This is the fork-first doctrine (`pihole-unbound-selinux` model) applied to Claude skills.~~

~~See also: memory `project_sovereign_skill_supply_chain`, `feedback_skills_local_vendor_fork`, `feedback_silent_cloud_egress_callout`.~~

**~~Why (regulated-operator rationale)~~** *(former heading)*


### Sovereign Skill Supply Chain › Why (regulated-operator rationale)

~~A Part 135-grade audit trail requires the operator to attest to exactly what operational logic was in effect at any time, that every change was authorized, and that nothing external mutated it. Cloud-synced skills (claude.ai as source of truth, one-way download, overwritable) make that attestation impossible and place the operator's IP on Anthropic infrastructure. This chain removes both problems: skills are local, versioned, GPG-signed, and distributed by the operator.~~

**~~Phase status (2026-09-28)~~** *(former heading)*


### Sovereign Skill Supply Chain › Phase status (2026-09-28)

- ~~**Phase 0 — control + offline foundation.**~~
  - ~~claude.ai skill sync **disabled** (`~/.claude/settings.json` `syncClaudeAiSkills:false`); all 25 skills vendored to `~/.claude/skills/` (non-synced). Takes full effect next launch (synced/ trashed then).~~
  - ~~Non-essential traffic **off** (`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING` in settings `env`).~~
  - ~~Synced **plugins preserved** to `~/.claude/plugins-vendored/`. Plugin-sync off-switch: **OPEN item** (no confirmed separate toggle found; `syncClaudeAiSkills` governs skills only — confirm via `/config`).~~
  - ~~**Distribution path** (below) replaces GitHub for multi-site.~~
- ~~**Phase 1 — growth: DONE (first cut).** `scripts/skill-snapshot.sh` produces signed, offline-verifiable snapshots into the vault; first snapshot persisted.~~
- ~~**Phase 2 — publish-out (pending):** package-sign-and-publish with author/publisher/license metadata (internal / external / public marketplace), IP retained. **Blocker:** parameterize embedded admin tokens (`ctdc_cowork_...` in flight-hifi-track / corporatetraveldc-dispatch-ops) out to env/secrets first.~~
- ~~**Phase 3 — docs (this file + vault):** in progress.~~

**~~Mechanism — `scripts/skill-snapshot.sh`~~** *(former heading)*


### Sovereign Skill Supply Chain › Mechanism — `scripts/skill-snapshot.sh`

~~Distribution without GitHub: the second-brain vault + a GPG-signed manifest. Any site pulls the artifacts and verifies **offline** (sha256 + operator GPG), then installs. No Anthropic sync, no external mutation, full audit trail.~~

- ~~`scripts/skill-snapshot.sh create` — bundles `~/.claude/skills` (excludes `synced/`, `.trash`), writes `skills-snapshot-<UTC>.tar.gz` + `.manifest.txt` (tarball hash + per-file hashes), and uploads both to `01-Sources/personal-research/skill-snapshots/` (deliberately OUTSIDE Cowork read scope while tokens remain embedded).~~
- ~~**Operator sign (your key):** `gpg --detach-sign --armor <manifest>` → upload the `.asc` to seal the snapshot.~~
- ~~`scripts/skill-snapshot.sh --verify <manifest>` — offline integrity check (tarball hash, then GPG signature if the `.asc` is present).~~
- ~~**Restore at another site (Phase 0.3 companion, to build):** download tarball + manifest + `.asc`, `--verify`, then extract into `~/.claude/skills/`.~~

**~~Audit procedure~~** *(former heading)*


### Sovereign Skill Supply Chain › Audit procedure

1. ~~Every skill change → new signed snapshot (create + operator sign).~~
2. ~~Snapshots are immutable, timestamped, and accumulate in the vault (the "state of growth") — the versioned record of what logic existed when.~~
3. ~~Any site proves integrity offline via `--verify` before loading skills.~~
4. ~~Operational skills' audit-grade home is the repo `.claude/skills/` (git + signed manifest) once tokens are parameterized out (Phase 2 blocker).~~

**~~Open items~~** *(former heading)*


### Sovereign Skill Supply Chain › Open items

- ~~Plugin-sync off-switch (confirm/close).~~
- ~~Token parameterization (unblocks repo-inclusion, broader vault scope, publish-out).~~
- ~~Restore/pull script for multi-site install.~~
- ~~Phase 2 publish-out packaging.~~
