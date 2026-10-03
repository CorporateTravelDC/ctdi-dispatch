# Agent segmentation on the dispatch Pi

Status 2026-10-03: **resource isolation shipped** (`agents.slice`), **privilege
separation designed, not implemented** (the `ctdc-agent` user). Backlog item
"ctdc-agent privilege separation" (open since 2026-09-23) promoted after the live
case below. The second brain is the authority for the rules referenced here; this
file is the engineering design.

## Threat model -- what actually happened, twice

1. **Credential exposure (2026-09-22).** An agent passed a Cloudflare API token as
   an HTTP header via urllib; the library embedded the whole header in an
   exception, which became ordinary tool output in a session transcript on disk.
   Nothing was mishandled at rest. The token had to be rotated. Root condition:
   every agent runs as `corporatetraveldc` and can read
   `/etc/corporatetraveldc/dispatch-secrets.env` (0600, 62 keys) in full.
2. **Resource exhaustion (2026-10-03).** Agent sessions ran unconfined: the
   remote-control unit in `app.slice` (no caps), SSH-started `claude`/`codex` in
   `session-*.scope` -- a *sibling* of `user@1000.service`, so one agent session
   competed at equal CPU weight with all of production combined. A 14-day
   `journalctl` scan from an agent took load1 from ~20 to 40.09;
   `thermal-ingest-guard` tripped LOCKDOWN and shed all seven SWIM ingest
   containers plus poller/pusher/runner. Resume requires load1 < 12 for 300 s,
   which the daytime baseline rarely reaches, so the shed persisted until a manual
   restore.

Both cases share one cause: agents are indistinguishable from the operator.

## Known agents on this box

| Agent | How it runs | Scratchpad (write-only, never authority) |
|---|---|---|
| Claude Code | `corporatetraveldc-claude-remote-control.service` (user unit, now `Slice=agents.slice`); plus `claude --resume` sessions from SSH (phone over Tailscale) | `CLAUDE.md`, Claude's auto-memory dir |
| Codex (OpenAI) | `codex app-server` daemons + `codex-code-mode-host`, launched from SSH sessions; one vault note records cwd `/app` | `AGENTS.md`, Codex persistent memory |
| Cowork | reaches the board over HTTP with a scoped board-write token (nonce enrollment, weekly GPG presence attestation); no shell on the box | board threads only |

Cross-agent coordination is the board (`coord` / `research` threads). None of the
scratchpads is read back for decisions; the second brain is.

## Part 1 -- resource isolation (shipped)

`.config/systemd/user/agents.slice` (tracked, installed live):

| Setting | Value | Why |
|---|---|---|
| `CPUWeight` | 25 | `production.slice`/`app.slice` are at the default 100 -> agents get ~11% under full contention, unlimited burst when idle |
| `CPUQuota` | 100% | one core for *all* agents. llama is hard-capped at 200%, ingest+poller+web+pusher+postgres need ~1 core; the fourth core is the headroom that keeps load1 under the guard's bars. Throttled tasks leave the run queue, so bursts stop inflating load1 |
| `MemoryHigh` / `MemoryMax` | 3 G / 4 G | a Claude session is 0.5-1.1 GB RSS, Codex daemons ~0.2 GB; 3-4 sessions fit; `production.slice` keeps `MemoryLow=6G` so reclaim hits agents first |
| `TasksMax` | 1024 | a runaway fork tree from a tool call stops here |

Membership:
- `corporatetraveldc-claude-remote-control.service` (live-only unit in
  `~/.config/systemd/user`, not tracked): `Slice=agents.slice` added; applies on
  its next restart, which must be done from a session that is **not** that unit.
- SSH sessions: `scripts/agent-run.sh <claude|codex> [args]` wraps the command in
  `systemd-run --user --scope --slice=agents.slice`. Operator may alias it in their
  own shell profile; no agent edits that file.

Runtime override without a reload: `systemctl --user set-property --runtime
agents.slice CPUQuota=200%`. Rollback: delete the slice file, `daemon-reload`,
remove the `Slice=` line.

Not covered by this part: an agent reading secrets, writing to the repo, or
signing anything. That is Part 2.

## Part 2 -- privilege separation (design)

Goal: agents run as `ctdc-agent`, a separate user, with read access to the
*minimum* secrets they need, no access to the rest, and no ability to sign.

### Secrets: what agents actually need

Of the 62 keys in `dispatch-secrets.env.template` (names only -- values never
appear in any tracked file):

- **Agents need (read, via scripts that consume them internally):**
  `DISPATCH_ADMIN_TOKEN` (API calls into dispatch), `NTFY_TOKEN` (ops pings),
  `DISPATCH_PG_PASSWORD` (read-only diagnostics -- better: a read-only PG role
  with its own password), `AIRFRAMES_TOKEN` / `ACARSDRAMA_JUMPSEAT_TOKEN` only if
  the feeder scripts stay agent-run (they should not).
- **Agents never need:** every `SWIM_NMS_*` triplet (ingest containers only),
  `NWWS_*`, `FAA_LADD_*`, `JASDAT_*`, `NAIPS_*`, `EUROCONTROL_*`, `AIS_*`,
  `KPLER_*`, `JMA/KMA/CMA/METEOFRANCE/FLIGHTAWARE` keys, `PUSHOVER_*`,
  `NTFY_TWILIO_*`, the three `*_WEBHOOK_SECRET`s, `ULTRAFEEDER_LAT/LON`,
  `JUMPSEAT_API_KEY`, `ACARS_DISPATCH_ADMIN_TOKEN`, the Cloudflare management
  token (`CF_MANAGEMENT_API_TOKEN`, not in the template -- it must move behind an
  operator-run script, never agent-readable; today `cf-dns-record.sh` reads it).

Mechanism: split `dispatch-secrets.env` into `dispatch-secrets.env` (production,
root:corporatetraveldc 0640) and `agent-secrets.env` (root:ctdc-agent 0640, the
short list above). Containers keep reading the production file via
`EnvironmentFile=` (59 Quadlets); nothing in a container changes.

### Repo and signing

- Repo stays owned by `corporatetraveldc`; `ctdc-agent` gets write via a shared
  group (`ctdc-dev`, setgid dirs, `core.sharedRepository=group`). Agents already
  cannot commit (GPG key + passphrase are the operator's); that stays. Manifest
  signing stays operator-only. The verified-exec gate (`verify-manifest.sh`) is
  read-only and works unchanged for the agent user.
- Agent home: `/home/ctdc-agent` for CLI state (`~/.claude`, `~/.codex`); skills
  synced from the repo (closes part of twin-gate 2A by making the repo the only
  source the agent user can read).

### What breaks on the switch (and the fix)

- 38 scripts under `scripts/` and ~17 modules under `src/` read
  `dispatch-secrets.env` directly. Those an agent legitimately runs (watchdog
  `--status` modes, `cf-dns-record.sh --show`, `scheduled-*.sh --status`,
  `gui-window.sh`) must read `agent-secrets.env` or run as `corporatetraveldc` via
  a sudoers rule scoped to the exact script path. The rest are timer-run as
  `corporatetraveldc` and are unaffected.
- `sudo` for agents: none. Root actions stay relayed to the operator, as now.
- Board, second brain (`webdav_client` uses `NEXTCLOUD_*` -- those move to the
  agent file), ntfy: continue to work from the agent user.

### Phased migration

1. **Now (done):** `agents.slice`; remote-control unit in it; `agent-run.sh`.
2. **Inventory:** tag each `dispatch-secrets.env` reader as container / timer /
   agent-interactive. Create the read-only PG role.
3. **Split the env file** (operator writes both; agents write neither). Point the
   agent-interactive scripts at `agent-secrets.env` with the production file as a
   fallback for the operator's own shell.
4. **Create `ctdc-agent`**, group `ctdc-dev`, home, synced skills; move the
   remote-control unit to a `ctdc-agent` user manager (`loginctl enable-linger
   ctdc-agent`); `agent-run.sh` becomes `sudo -u ctdc-agent` for the operator's
   SSH sessions (or the operator logs in as that user to drive agents).
5. **Rotate** every key an agent could have read before the split, in the order
   the 2026-09-22 note prescribes.
6. **Codex:** same user, same slice; its daemons stop living in SSH session scopes.

Rollback at any step: the production env file is never modified in place (the
split is a copy), the slice is runtime-reversible, and the agent user can be
disabled with `loginctl terminate-user ctdc-agent` without touching production.

### Open questions for the operator

- Does the Cowork board key need the agent user, or stay as is (HTTP-only)?
- Read-only PG role name and which tables (default: everything except
  `audit_log` and `board_*`).
- Whether `gui-window.sh` should run as the agent user (it owns no secrets besides
  the operator-created VNC password; it could).
