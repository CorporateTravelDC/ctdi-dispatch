# Sovereign Skill Supply Chain

~~Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.~~ (corrected 2026-10-15: re-verified against HEAD 1491bf5a plus the staged next-relay batch and live state on 2026-10-15 ~01:19Z / 2026-10-14 21:19 ET, except where marked UNVERIFIED.)

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
| `skills/<name>/` in this repo (~~8~~ (corrected 2026-10-25: superseded -- 14 since 2026-10-16, when `c4df48d9` added `adversarial-doc-refresh`, `adversarial-pentest`, `adversarial-sweep`, `adversarial-sweep-docs-first`, `adversarial-sweep-docs-first-revalidated` and `adversarial-sweep-pentest-first-revalidated`; the operator's live copies still match: `skills-sync.sh check` from the clean main tree on 2026-10-26 reports 15 tracked, 0 divergent, 0 missing-live) -- the original 8: `corporatetraveldc-dispatch-ops`, `flight-hifi-track`, `hub-arrivals-lookup`, `linkedin-export-analyzer`, `morning`, `nec-train-hifi-track`, `overwater-adsb-handoff-track`, `second-brain-remember`) | the operator's own skills | covered by the signed tree manifest; the operator's `~/.claude/skills/` copies were byte-identical on 2026-10-06 (and again on 2026-10-15, `diff -rq` per skill). The twin gate is `scripts/skills-sync.sh` (`check` exits 1 on any divergence or missing live copy; `apply --i-am-the-operator` copies repo to live; vendor dirs listed in `scripts/lib/skills-vendor-ignore.txt` are never touched); it runs in stack-refresh's audit and in `check-claude-md-drift.sh`. Provenance and licence of `morning` are not recorded (open) |
| `skills/vendor-pins.txt` | 16 vendor skills pinned by tree hash (`built-in-browser`, `chrome-browser`, `computer-use`, `deep-research`, `doc-coauthoring`, `docs`, `docx`, `google-workspace`, `import-memory`, `internal-comms`, `mcp-builder`, `pdf`, `pptx`, `skill-creator`, `web-artifacts-builder`, `xlsx`) | the pin file is signed; the vendor bytes are **not** committed (several are proprietary-licensed) |
| `.claude/skills/` in this repo (`dispatch-context-guardian`, `personal-export-analysis`) | project skills | signed with the tree |
| `.agents/skills/` in this repo (~~same two names~~ (corrected 2026-10-25: superseded -- 8 directories since 2026-10-16, `c4df48d9`: the same two names plus the six `adversarial-*` skills); added 2026-10-15) | the copy Codex reads | signed with the tree, but a **diverged, stale fork**: ~~every file of both skills differs from `.claude/skills/` (2026-10-15 `diff -rq`)~~ (corrected 2026-10-25: superseded -- at HEAD `5f7fca01` three of the five files differ from `.claude/skills/` (`dispatch-context-guardian/SKILL.md`, its `scripts/restore_dispatch_state.py`, `personal-export-analysis/SKILL.md`) while `context_hook.py` and `save_dispatch_state.py` are identical; of the six `adversarial-*` copies, `adversarial-doc-refresh/SKILL.md` differs from `skills/`), it is not in the grant catalog, and `skills-sync.sh` does not compare it. Open: regenerate it from `.claude/skills/` or mark it unmaintained |
| `~/.claude/skills/` (operator) | the operator's working set (~~27 entries with `synced` and `.trash`, re-counted 2026-10-15; 25 skills~~ (corrected 2026-10-25: superseded -- 33 entries on 2026-10-26: 31 skills, i.e. the 14 signed, `dispatch-context-guardian` and the 16 pinned vendor skills, plus `synced` and `.trash`)) | not signed in place; snapshotted (below) |

Embedded tokens: the repo skills no longer carry a live token. The remaining
`ctdc_cowork_` strings are placeholders or a note that the old literal was
revoked 2026-08-16. A trashed copy under `~/.claude/skills/.trash/` still
contains a token-shaped string ~~[UNVERIFIED: whether it is revoked]~~
(corrected 2026-10-25: its SHA-256 matches no `auth_tokens` row, so it
authenticates nothing); it is outside the repo and excluded from snapshots.
(added 2026-10-25: the same string also sits in the operator's
`~/.agents/skills/overwater-adsb-handoff-track/SKILL.md`, a live Codex skill
root that is outside `skills-sync.sh` and outside the snapshots.)

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
~~Copies go only to `~/.claude/skills`, so a codex-mode agent account's Codex
CLI receives no granted skill (2026-10-15).~~ (fixed 2026-10-16: the skill
root follows the account's login mode in `/etc/ctdc-accounts.conf`:
`~/.claude/skills` for `claude` and `ssh`, `~/.agents/skills` for `codex`.
Evidence for the Codex root: the pinned Codex 0.160.0 binary
(`ext/skills/src/host_roots.rs`) names `.agents/skills` under `$HOME` as its
user-scope skill root and `$CODEX_HOME/skills` (`~/.codex/skills`) as the
second; the operator's live `~/.agents/skills/` holds Codex's user skills
~~(26 entries)~~ (corrected 2026-10-25: superseded -- 27 entries on 2026-10-26 22:03Z) while `~/.codex/skills/.system` holds Codex's own bundled skills
and skill-installer output, so a root-owned grants root there would
quarantine Codex's files. Same root-owned read-only copies, `.ctdc-grant`
marker, manifest/pin verification, clawback, in-place-edit restore and
quarantine (`~/.agents/.ctdc-skills-quarantine`); `~/.agents` itself is
created owned by the account when missing; no `settings.json` merge in codex
mode; `~/.codex` marks the account as provisioned. Claude-mode accounts are
unchanged. Takes effect live once the root copy is refreshed
(`install-root-copies.sh`) and the hourly apply runs.)

~~Live on 2026-10-06 18:06Z it was holding ("checkout does not verify against
the signed manifest -- nothing changed") because of an unsigned working-tree
change to `scripts/stack-refresh.sh`.~~ (corrected 2026-10-15: applying normally;
the 01:08Z run reported `2 agent account(s), 0 finding(s)`. Edits now happen in
the guard worktree, so main keeps verifying.) (added 2026-10-25: the root
runs ~~still report `2 agent account(s), 0 finding(s)`, the latest at 2026-10-26
03:04:59Z~~ (corrected 2026-10-25: superseded -- reported 2 accounts up to the 2026-10-26 03:04:59Z run; every run since reports `3 agent account(s), 0 finding(s)`, latest 2026-10-26 21:02Z); a third agent-kind account, `ctdc-agent-anthropic-claude-ea`, was
added to `/etc/ctdc-accounts.conf` and the grants file (`grant
ctdc-agent-anthropic-claude-ea *`) at 03:23Z, after that run.) Tests: `tests/scripts/test_skill_grants.py`
(~~28~~ (corrected 2026-10-25: superseded -- 36 test functions at HEAD `5f7fca01`)). Design: `docs/AGENT_SEGMENTATION.md` "Skills: grants and clawback".

## Operator's Claude Code settings (`~/.claude/settings.json`, outside the repo)

| Setting | Live value |
|---|---|
| `syncClaudeAiSkills` | `false` |
| `env.DISABLE_TELEMETRY` | `"1"` |
| `env.DISABLE_ERROR_REPORTING` | `"1"` |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | **not set** (the previous revision said it was; still not set 2026-10-15) |
| `syncClaudeAiPlugins` (added 2026-10-15) | `false` |
| `env.DISABLE_AUTOUPDATER` (added 2026-10-15) | `"1"` |
| `env.CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL` (added 2026-10-15) | `"1"` |

(added 2026-10-25: since 2026-10-26 00:57Z a root-installed managed policy,
`/etc/claude-code/managed-settings.json` (root 0644), also enforces six of
these values: `syncClaudeAiSkills` and `syncClaudeAiPlugins` `false`, and the
four `env` keys above set to `"1"`. It does not set
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` either.)

`~/.claude/skills/synced/` still exists (~~one entry, dated 2026-09-18~~ (corrected 2026-10-25: one empty sync-bucket directory, mtime 2026-10-03, plus its hidden `.bucket-*` marker file dated 2026-09-18)); the
earlier expectation that it would be trashed on the next launch did not hold.
Synced plugins are preserved under `~/.claude/plugins-vendored/`. ~~A plugin-sync
off-switch is still unconfirmed.~~ (corrected 2026-10-15: `syncClaudeAiPlugins`
is `false` in the live settings.)

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
signed; the vault was not listed.] (added 2026-10-25: the local staging
directory `~/.claude/skill-snapshots/` holds one snapshot,
`skills-snapshot-20260928T182656Z` -- the tarball, its manifest and a detached
`.asc` dated 2026-09-28 20:10Z; the vault copy is still not listed.)

## Phase status

- **Phase 0 -- control + offline foundation:** done, except ~~the plugin-sync
  switch and~~ `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` (above; the plugin-sync
  switch is off as of 2026-10-15).
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
