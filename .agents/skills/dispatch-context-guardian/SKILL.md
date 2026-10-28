---
name: dispatch-context-guardian
description: This skill should be used when setting up or troubleshooting automatic context-window management for a session working against a dispatch platform deployment -- it explains the Stop-hook that keeps platform state saved ahead of a 900k-token auto-compact and restores situational awareness after.
version: 1.0.0
---

# Dispatch Context Guardian

> **Rewritten 2026-10-15** to match the maintained copy in
> `.claude/skills/dispatch-context-guardian/SKILL.md`. The `scripts/` in
> this directory are re-synced from that copy in the same batch. Before
> the re-sync, this directory held a stale copy whose
> `restore_dispatch_state.py` generated a passphraseless
> `~/.ssh/cowork_ed25519` key when none existed, and whose
> `context_hook.py` summed usage fields the Stop payload does not carry
> (so it always read 0). Neither is true of the re-synced scripts. If
> `scripts/` here ever differs from the `.claude` copy, do not run it;
> ask the operator to re-sync.
>
> (corrected 2026-10-25: this copy no longer matches the maintained copy.
> `context_hook.py` and `save_dispatch_state.py` are byte-identical to the
> `.claude` copies, but `restore_dispatch_state.py` differs: the `.claude`
> copy gained `_idle_by_design()` on 2026-10-20 (push-covered / optional /
> awaiting-credentials feeds shown as idle, not failing) and this copy did
> not (`cmp`, 15:41:56Z). Per the rule above, do not run this copy; the
> re-sync is an operator decision recorded in the sweep register. Neither
> copy generates an SSH key or edits `authorized_keys`.)

## What this is

A `Stop` hook, not a slash-command skill. It has no interactive behavior
of its own -- it runs at the end of every turn once installed, and exists
to solve one problem: a long working session against a dispatch platform
deployment can run its context window up toward the compaction threshold,
and losing the platform's live situational picture (feed health, active
TFRs, weather, alerts, CPS state) at that moment is worse than losing
ordinary conversation history, because re-deriving it costs real tool
calls and real time.

The scripts are written for Claude Code: they read a Claude Code session
transcript, check `~/.claude/settings.json` for the auto-compact window,
and keep their files under `~/.config/Claude/`. Under Codex they have not
been verified (2026-10-15); see "Install" below.

## How it works

~~`scripts/context_hook.py` reads the hook payload Codex passes on stdin,
sums `inputTokens + cacheReadInputTokens + outputTokens`, and:~~
(corrected 2026-10-15: the Stop payload carries no usage block, only
`session_id` / `transcript_path` / `stop_hook_active`, so that sum was
always 0. The hook reads the tail (last 4 MB) of the session transcript at
`transcript_path` and takes the most recent assistant message's usage:
input + cache-read + cache-creation tokens; output tokens are not context.
Then:)

- Below 800,000 tokens: does nothing, exits 0.
- At/above 800,000 (`WARN_LIMIT`): saves a dispatch-state snapshot every
  turn and shows a one-line `systemMessage` saying whether Claude Code will
  compact by itself (always exits 0, never blocks a turn).
- ~~At/above 900,000 (`HARD_LIMIT`): saves a snapshot, prints instructions
  to run `/compact`, and exits 1.~~ (corrected 2026-10-15: there is no hard
  limit and the hook never blocks; it was removed 2026-10-05. Compaction is
  the client's own auto-compact at its configured window.)
- Every decision is logged to `~/.config/Claude/dispatch_context_guardian.log`
  (rotated to `.log.1` past 200 KB).

The hook does NOT compact -- no hook can run `/compact`.

`scripts/save_dispatch_state.py` polls the dispatch platform's Tier-0 GET
endpoints (`/healthz`, `/api/v1/feeds`, `/api/v1/tfr`, `/api/v1/weather`,
`/api/v1/alerts`, `/api/v1/cps`, `/api/v1/amtrak`) plus the Tier-1
`/api/v1/runsheet` (needs a Tier-1 token such as `DISPATCH_ADMIN_TOKEN` in
the environment; ~~it warns and skips if absent~~ (corrected 2026-10-25: the Tier-1 GET is always sent, without an `Authorization` header when no token is set; the route then answers 403, `fetch()` prints a WARN, stores `null` for `runsheet` and carries on, `save_dispatch_state.py:76-80,112-117`) rather than failing the
whole snapshot), and writes the result to
~~`~/.config/Codex/dispatch_state_snapshot.json`~~
`~/.config/Claude/dispatch_state_snapshot.json` (corrected 2026-10-15: the
path is fixed in the script). It also snapshots the account's own public
key, ~~`~/.ssh/cowork_ed25519.pub`~~ `~/.ssh/<login user>_ed25519.pub`
(corrected 2026-10-15), if present, so the restore step can flag a key
change across a compact.

`scripts/restore_dispatch_state.py` reads that snapshot and prints a
formatted situational brief -- service health, feed status, CPS, active
TFRs, NWS alerts, weather, Amtrak, runsheet, and the signing-key check.
The key check only reports: it never generates a key and never suggests
editing `authorized_keys`. A missing or changed key means stop and tell
the operator; keys are provisioned by the operator (`plan.sh`).

## Install

Hooks are not auto-registered by dropping a skill into ~~`.Codex/skills/`~~
`.agents/skills/` (corrected 2026-10-15). They must be registered in the
client's own configuration.

- Claude Code: see `.claude/skills/dispatch-context-guardian/SKILL.md`
  (hooks in `~/.claude/settings.json`, `autoCompactWindow` 900000).
- Codex: ~~`~/.Codex/settings.json` / `.Codex/settings.json`~~ (corrected
  2026-10-15: Codex has no `settings.json`; its configuration lives under
  `~/.codex/`, chiefly `~/.codex/config.toml`). On this box (2026-10-15)
  no hook is registered in `~/.codex/`, so this skill is not installed for
  Codex. Do not register it there without the operator: the scripts have
  not been checked against Codex's hook payload or transcript format, and
  if the transcript has no Claude-style usage block the hook stays silent.

Use the absolute path to *your* clone's `scripts/context_hook.py` -- the
script derives its own `SKILL_DIR` from `__file__`, so the rest of the
paths it uses resolve regardless of where you cloned it.

## Configuration

`save_dispatch_state.py`'s `DISPATCH_BASE` is read from the `DISPATCH_BASE`
env var and falls back to a hardcoded default -- override the env var to
point at your own deployment. Prefer a Tailscale/private-network address
over a public Cloudflare-fronted hostname if your deployment gates API
routes behind Cloudflare Access -- Access redirects (302) are
indistinguishable from a dead endpoint to this script's JSON parser, so
every endpoint will silently report "failed" if pointed at a gated public
hostname. The script's own comment records that this is exactly why the
default is a tailnet address.

> **Corrected 2026-08-23.** This section used to say the default is
> `https://ops.example.com` "in the public copy of this repo". Both halves
> are wrong. The real default is the tailnet address
> `http://100.x.x.x:8000`
> (`scripts/save_dispatch_state.py:34`), and the public mirror does not
> rewrite it to any `ops.example.com` placeholder --
> ~~`scripts/scrub-public-tree.py:140`~~ ~~`scripts/scrub-public-tree.py:279`
> (line corrected 2026-10-15)~~ `scripts/scrub-public-tree.py:306` (line
> corrected 2026-10-25; a regex sweep at `:449` covers the whole
> `100.x.x.x/24` shape) substitutes the literal `100.x.x.x` to
> `100.x.x.x`, so the public copy reads `http://100.x.x.x:8000`.
> Separately, `ops.example.com` was **retired 2026-08-02** and
> is hard-404'd by hostname in `src/runner/main.py`'s `_RETIRED_HOSTNAMES`
> (`:361`, re-checked 2026-10-25),
> so an `ops.`-shaped default would be pointing at a dead name regardless
> of scrubbing. Nothing needs configuring on this box; the default already
> resolves correctly here.

## Known limitations (~~not yet fixed~~ formatters fixed, corrected 2026-10-15)

> **Corrected 2026-10-15:** the formatter mismatches below are fixed in
> the re-synced `scripts/restore_dispatch_state.py`: `fmt_cps` reads
> `score`/`label`, `fmt_feeds` reads `feed_name` and derives health from
> `age_seconds` vs `stale_threshold_seconds` plus `error`, `fmt_tfr` reads
> `tfr_id`/`is_vip`, `fmt_weather` reads `station` and falls back to the
> parsed `ceiling_ft`, `visibility_sm`, `wind_kt` and `precip_code` when
> there is no raw METAR text, and `fmt_amtrak` reads `summary` and
> `trains`. The table is kept as the record of what was wrong.

- `restore_dispatch_state.py`'s display formatters assumed field names that
  did not match this platform's actual `/api/v1/*` JSON response shapes --
  the raw snapshot file had correct data, but the formatted printout
  showed placeholder `?` values instead of real ones.

  **Re-verified and widened 2026-08-23** by diffing each formatter against
  the live endpoints' real payloads (`curl` against
  `http://127.0.0.1:8000/api/v1/*`):

  | Formatter | Reads | Live payload actually has | Effect |
  |---|---|---|---|
  | `fmt_cps` | `state`, `go_no_go` | `label` (no `state`, no `go_no_go`) | headline renders `? (score=GREEN, state=?)` -- `score` is the only field that works |
  | `fmt_feeds` | `name`, `healthy`/`ok` | `feed_name`; no health boolean at all (derive from `age_seconds` vs `stale_threshold_seconds` plus `error`) | every feed prints `✗ ?`; only `age_seconds` resolves |
  | `fmt_tfr` | `notam_id`/`id`, `type` | `tfr_id`, `is_vip` | every TFR prints `• ? [?]` |
  | `fmt_weather` | `station_id`/`icao`, `raw_text`/`metar` | `station`, and **no raw METAR text at all** (parsed only: `ceiling_ft`, `visibility_sm`, `wind_kt`, `precip_code`) | prints `?:` with an empty observation |
  | `fmt_amtrak` | `status`/`board_status` | `available`, `summary`, `trains` | prints `?` (`summary` is the field wanted) |

  `fmt_alerts` could not be confirmed either way -- `/api/v1/alerts`
  returned an empty `alerts` list at check time, so its `event`/`areaDesc`
  assumptions are untested, not verified-good. `fmt_runsheet` was likewise
  not exercised (Tier-1, needs a token).
