---
name: dispatch-context-guardian
description: This skill should be used when setting up or troubleshooting automatic context-window management for a Claude Code session working against a dispatch platform deployment -- it explains the Stop-hook that saves platform state before a 900k-token compact and restores situational awareness after.
version: 1.0.0
---

# Dispatch Context Guardian

## What this is

A Claude Code `Stop` hook, not a slash-command skill. It has no interactive
behavior of its own -- it runs automatically at the end of every turn once
installed, and exists to solve one problem: a long working session against a
dispatch platform deployment can run its context window up toward the model's
compaction threshold, and losing the platform's live situational picture
(feed health, active TFRs, weather, alerts, CPS state) at that moment is
worse than losing ordinary conversation history, because re-deriving it costs
real tool calls and real time before Claude is useful again.

## How it works

`scripts/context_hook.py` reads the hook payload Claude Code passes on stdin,
sums `inputTokens + cacheReadInputTokens + outputTokens`, and:

- Below 800,000 tokens: does nothing, exits 0.
- At/above 800,000 (`WARN_LIMIT`): saves a dispatch-state snapshot, prints a
  warning, exits 0 (does not block the turn from ending).
- At/above 900,000 (`HARD_LIMIT`): saves a snapshot, prints instructions to
  run `/compact`, and exits 1 -- which blocks the Stop and surfaces the
  message, so the operator sees it before continuing.

`scripts/save_dispatch_state.py` polls the dispatch platform's Tier-0 GET
endpoints (`/healthz`, `/api/v1/feeds`, `/api/v1/tfr`, `/api/v1/weather`,
`/api/v1/alerts`, `/api/v1/cps`, `/api/v1/amtrak`) plus the Tier-1
`/api/v1/runsheet` (requires `DISPATCH_ADMIN_TOKEN` in the environment, warns
and skips if absent rather than failing the whole snapshot), and writes the
result to `~/.config/Claude/dispatch_state_snapshot.json`. It also snapshots
the `~/.ssh/cowork_ed25519.pub` public key if present, so the restore step
can detect and flag a key change/regeneration across a compact.

`scripts/restore_dispatch_state.py` reads that snapshot and prints a
formatted situational brief -- service health, feed status, CPS, active
TFRs, NWS alerts, weather, Amtrak, runsheet, and the SSH-key check. Run it
manually after a `/compact` (the hard-limit message tells you the exact
command).

## Install

Hooks are not auto-registered by dropping a skill into `.claude/skills/` --
add this explicitly to `hooks.Stop` in `settings.json` (user-scope
`~/.claude/settings.json` for every session, or project-scope
`.claude/settings.json` for one repo only):

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/dispatch-context-guardian/scripts/context_hook.py"
          }
        ]
      }
    ]
  }
}
```

Use the absolute path to *your* clone's `scripts/context_hook.py` -- the
script derives its own `SKILL_DIR` from `__file__`, so the rest of the paths
it prints/uses resolve correctly regardless of where you cloned it.

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
> `scripts/scrub-public-tree.py:140` substitutes the literal
> `100.x.x.x` to `100.x.x.x`, so the public copy reads
> `http://100.x.x.x:8000`. Separately, `ops.example.com` was
> **retired 2026-08-02** and is hard-404'd by hostname in
> `src/runner/main.py`'s `_RETIRED_HOSTNAMES`, so an `ops.`-shaped default
> would be pointing at a dead name regardless of scrubbing. Nothing needs
> configuring on this box; the default already resolves correctly here.

## Known limitations (not yet fixed)

- `restore_dispatch_state.py`'s display formatters assume field names that
  don't match this platform's actual `/api/v1/*` JSON response shapes -- the
  raw snapshot file has correct data, but the formatted printout shows
  placeholder `?` values instead of real ones. The snapshot JSON itself is
  reliable; only the pretty-print step needs a field-mapping fix.

  **Re-verified and widened 2026-08-23** by diffing each formatter against
  the live endpoints' real payloads (`curl` against
  `http://127.0.0.1:8000/api/v1/*`). The problem is broader than the three
  formatters this list originally named -- `fmt_cps` and `fmt_amtrak` are
  affected too, which matters because CPS is the headline line of the
  restore brief:

  | Formatter | Reads | Live payload actually has | Effect |
  |---|---|---|---|
  | `fmt_cps` | `state`, `go_no_go` | `label` (no `state`, no `go_no_go`) | headline renders `? (score=GREEN, state=?)` -- `score` is the only field that works |
  | `fmt_feeds` | `name`, `healthy`/`ok` | `feed_name`; no health boolean at all (derive from `age_seconds` vs `stale_threshold_seconds` plus `error`) | every feed prints `✗ ?`; only `age_seconds` resolves |
  | `fmt_tfr` | `notam_id`/`id`, `type` | `tfr_id`, `is_vip` | every TFR prints `• ? [?]` |
  | `fmt_weather` | `station_id`/`icao`, `raw_text`/`metar` | `station`, and **no raw METAR text at all** (parsed only: `ceiling_ft`, `visibility_sm`, `wind_kt`, `precip_code`) | prints `?:` with an empty observation; a raw-text remap is impossible, this one needs reformatting against the parsed fields |
  | `fmt_amtrak` | `status`/`board_status` | `available`, `summary`, `trains` | prints `?` (`summary` is the field wanted) |

  `fmt_alerts` could not be confirmed either way -- `/api/v1/alerts` returned
  an empty `alerts` list at check time, so its `event`/`areaDesc` assumptions
  are untested, not verified-good. `fmt_runsheet` was likewise not exercised
  (Tier-1, needs `DISPATCH_ADMIN_TOKEN`).
