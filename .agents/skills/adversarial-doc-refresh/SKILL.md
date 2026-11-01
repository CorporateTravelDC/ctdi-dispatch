---
name: "adversarial-doc-refresh"
description: "Rewrite the repo's documentation from the live system and the code only, then attack every claim until it can be defended. Trigger on \"doc refresh\", \"docs parity\", \"adversarial doc refresh\", \"validate the docs\", \"make the docs match the live system\"."
---

# adversarial-doc-refresh

The documentation refresh method the operator approved in August 2026 (vault: `01-Sources/manual/20260823T064349Z` through `20260824T011337Z`, the Opus blind passes of 08-25/26), written down as a procedure so it runs the same way every time. Written 2026-10-16 after the 2026-10-15 refresh broke the method (findings and other docs were fed to the rewrite agents; investor docs were touched).

## The rule in one line

The live system and the code are the only source of truth. Every existing document, CLAUDE.md included, is an unverified claim and never evidence. Check the live system first, then validate the docs against it, never the reverse.

## What this skill produces

- Every living doc rewritten so that each statement about current state is true now, with superseded text kept struck through (`~~old~~ (corrected YYYY-MM-DD: current fact)`). Dated records (filenames with a date, security reviews, research notes) are not rewritten; they get a dated status banner at the top and inline strike-throughs only where they present something as current that is not.
- A claim register, one row per statement that was changed or newly asserted: doc:line, the statement, the evidence (command run or `file:line` read, with the UTC time), the verdict label (see below).
- A defence sheet for each doc: kept / struck / unverified counts, with every count printed next to the command that produced it.
- A list of code and configuration defects found on the way, marked NEEDS OPERATOR DECISION. The doc pass never fixes code.
- Nothing published. Output lands in the guard worktree (operator session) or as a workspace contribution (agent account). The operator signs and commits.

## Scope

Every tracked `*.md` in the repo: `README.md`, `SECURITY.md`, `docs/**`, `skills/*/SKILL.md`, `.claude/skills/**`, `.agents/skills/**`, `src/**/README.md`, `addenda/**`, `scripts/**/*.md`, `systemd/**/*.md`. Skill files and `src/*/README.md` were skipped by earlier passes (vault `20260903T135237Z`, `20260831T205143Z`); they are in scope here.

Out of scope unless the operator says otherwise: `docs/investor-materials/**` (regenerated only by `adversarial-sweep*` after a pen test), and the frozen `docs/security-reviews/*` records (only their README index is updated).

## Blind-agent exclusion list

Any agent whose job is to establish truth or to refute a claim must NOT read:

- `CLAUDE.md`, `AGENTS.md`, Claude or Codex memory files (write-only logs);
- `docs/investor-materials/**/research/**`;
- any file whose name contains `AUDIT`, `REVIEW`, `DRIFT`, `LIVE_STATE`, `PENTEST`, `FINDINGS`, `PARITY`, `CHANGES-`;
- the findings or rewrite output of any other agent in the same run;
- `/etc/corporatetraveldc/*secrets*`, `*.env` (names of keys may be grepped; values never read).

Agents that rewrite docs read the truth set and the doc they are rewriting, nothing else. The coordinator never re-injects its own conclusions into a blind agent's prompt (vault `20260818T235102Z`), and shows the operator every delegation prompt before dispatch.

## Evidence rules

- Evidence is a command run in this session on the live box (`systemctl --user`, `systemctl`, `podman ps`, `git log`, `ls`, read-only API GETs, SELECT-only SQL via ~~`podman exec corporatetraveldc-pgsql psql -At`~~ (corrected 2026-10-25: that bare form fails, because `config/pg_hba.conf:53` requires `scram-sha-256` on the local socket. Pass the container's own credentials: `podman exec corporatetraveldc-pgsql sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SET statement_timeout=30000;" -c "<SELECT>"'`)), or source read in this session with `file:line`. Nothing else counts. Docstrings and comments are not evidence.
- Read-only: no writes to the DB, no restarts, no pushes, no `sudo`, 1-2 requests per endpoint, no retries, no fuzzing.
- Never print or copy a secret value, token, ping URL, LADD-listed tail, hex, the ultrafeeder config line, or a process command line. Counts only for LADD. Mask tails and hexes.
- Every count is printed with the command that produced it and the UTC time (vault `20260824T011337Z`).
- Times are UTC, or UTC + ET; never ET alone.
- "Committed is not running": before the truth-gathering stage, confirm that the running containers were built from the signed tree (`scripts/scheduled-integrity-sweep.sh` provenance~~, or `podman inspect` labels~~ (corrected 2026-10-25: the image labels carry no source commit. The first-party images have only `build-date`, `service`, `io.buildah.version`, the OCI title and description, and on stack-refresh builds `stack-refresh`; one image (acars-watcher) also carries `org.opencontainers.image.version`, which is not a commit; there is no `org.opencontainers.image.revision` (re-checked 2026-10-26 22:05Z). Only the provenance receipts that `scripts/build/provenance.py verify --running` checks tie a running image to the signed commit)). If not, stop and ask the operator for a full-load rollout first.

## Verdict labels

For each claim: `HOLDS` (evidence matches), `CORRECTED` (struck and rewritten, evidence attached), `UNVERIFIED` (cannot be checked from code or live state in this session; marked in the doc as such), `NEEDS-OPERATOR-DECISION` (a code or config problem, not a doc problem). In the supervisory stage: `CONFIRMED-BY-INDEPENDENT-RECHECK`, `COULD-NOT-REPRODUCE`, `NEEDS-HUMAN-REVIEW`, `RESOLVED-SINCE-REPORT`.

## Stages (each ends at a checkpoint; the operator approves before the next starts)

### 0. Preflight

- Record the commit (`git rev-parse HEAD`), the worktree used (guard worktree for the operator session; a workspace contribution for agents), and the UTC time.
- Confirm images match the signed tree (see Evidence rules). Confirm the drift gate passes (`bash scripts/check-claude-md-drift.sh`).
- Build the doc inventory: `git ls-files '*.md'`, split into groups of 15-25 files with no file in two groups. Dated records form their own groups.
- Show the operator the stage-1 and stage-3 delegation prompts and the exclusion list. Wait.

### 1. Truth set, from the system only (parallel, blind)

One agent per subsystem, none of them opens any doc: ingest and SWIM feeds; poller, pusher and alerts; web API, auth, tiers, routes; agents, governance, gateway, liveness, grants; second brain and semantic layer; infrastructure (units, timers, containers, quadlets, network, backups, root copies); security and secrets handling (names only); data sources and external endpoints; skills and personas.

Each returns a fact list: fact, evidence (`file:line` or command + UTC), confidence. Facts that depend on root-only state are marked as such. The coordinator merges them into `truth-set.md` in the run directory with no editing of substance.

Checkpoint: coverage by subsystem, gaps, UNVERIFIED count.

### 2. Claim extraction

For each doc group, one agent lists every statement that asserts current state (behaviour, path, unit, schedule, count, route, threshold, status). Statements inside struck-through or SUPERSEDED text are not claims. Output: `claims/<group>.md`, one row per claim with doc:line.

### 3. Rewrite against the truth set (parallel)

One agent per doc group. Inputs: the group's claims file, `truth-set.md`, and the docs themselves. For each claim: HOLDS -> leave; CORRECTED -> strike and rewrite with the date; UNVERIFIED -> mark; code defect -> record under NEEDS-OPERATOR-DECISION, do not fix. Dated records get the banner treatment. The agent may run read-only live commands to settle a claim the truth set does not cover, and must add the evidence to the register.

Checkpoint: per-doc kept / struck / unverified counts, the defect list.

### 4. Blind refutation (parallel, fresh agents)

Fresh agents, never shown stage 3's reasoning, each take a doc group and try to disprove every statement that now stands, against the live system and the code. High-impact docs (`README.md`, `SECURITY.md`, `docs/INFRA_MAP.md`, `docs/ALERT_REFERENCE.md`, `docs/AGENT_SEGMENTATION.md`, every `SKILL.md`) get two refuters. A refutation names the evidence that contradicts the statement. Default when uncertain: refuted.

Loop: every refuted statement goes back to stage 3 (a new agent), then the refuters run again on the changed statements only, until a round returns nothing new (two consecutive dry rounds).

### 5. Supervisory pass (one agent, fed only stage 4's refute log)

Re-derives each refutation from live state and source, independently, and labels it `CONFIRMED-BY-INDEPENDENT-RECHECK`, `COULD-NOT-REPRODUCE`, or `NEEDS-HUMAN-REVIEW`. Severity follows reachability. The reporting agent is never asked to defend itself. This stage is not optional: the 08-26 review skipped it and the vault records that as "the single biggest gap remaining" (`20260826T213148Z`).

### 6. Cross-doc consistency (one agent)

Reads the rewritten docs only and lists contradictions between docs (the 08-23 third pass found nine that no doc-vs-live check could see). Each goes back to stage 3.

### 7. Hand-off

- Operator session: changes sit in the guard worktree. Run `scripts/relay-preflight.sh <relay>` ~~(lint, drift gate, tests, public scrub of the staged tree, transcript leak scan)~~ (corrected 2026-10-25: eight checks in order: relay lint, drift gate, dependency audit, APT snapshot pin age, tests, public scrub of the staged tree, transcript leak scan, and a heavy-lock queue check that only warns; `scripts/relay-preflight.sh:25-94`). Post the relay only on PASS. Public-scrub failures are fixed by defanging values or quoting allowlisted ones, never by weakening the scrub.
- Agent account: write the rewritten docs, the register and the defence sheets as a workspace contribution (`scripts/workspace-contribute.sh` or the gateway's `workspace_contribute`). Agents draft; they never publish, commit, sign or approve.
- Record the run in the vault (`second-brain-remember`, or the operator's `remember_text`) with the commit, the counts and the open decisions. Append the drift-job reports after the next scheduled run and confirm they come back clean.

## What this skill never does

- Never treats a doc, a prior audit, a security review or CLAUDE.md as evidence.
- Never edits `docs/investor-materials/**` (that is `adversarial-sweep*` after a pen test).
- Never edits a frozen security-review record.
- Never fixes code, config or units. It reports them.
- Never pushes, signs, publishes, restarts a service, or writes to the live database.
- Never reads a secret value or prints an identifier the standing rules protect.

## Lessons this procedure encodes (vault sources)

Contaminated blind agents (08-18); committed is not running (08-26); skipped scope: skills and `src/*/README.md` (09-03, 08-31); inverted claims missed (09-03); CLAUDE.md stale about its own status (08-26); docs tripping the public scrub (08-26); interrupted passes, so every stage writes its output to the run directory and can resume (08-23, 09-20); verification that checks the wrong thing (`--path-as-is`, Host header; 08-26, 10-13); the supervisory pass skipped (08-26); counts drift (08-24).
