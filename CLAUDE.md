# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository. **Rewritten 2026-08-11; every claim verified against
the live system on that date.**

## What this system is

Real-time executive travel intelligence platform for the Washington DC area.
Monitors commercial flights (FAA SWIM), Amtrak trains, and weather, then fires
push alerts through ntfy. Runs as rootless Podman containers managed by
systemd Quadlets under the `corporatetraveldc` user — core stack
(web/poller/pusher + ingest split into 7 per-feed containers) plus a large set
of timer-triggered skill containers and auxiliary services (SDR/ACARS/ADS-B
stack, second-brain, Nextcloud, ntfy, daily category watches, MCP bridges).
Don't hardcode a total container/unit count — it drifts fast; check
`systemctl --user list-units 'corporatetraveldc-*' --all` (145 loaded units at
this snapshot) and `podman ps`.

## Key paths

| Path | Purpose |
|------|---------|
| `/opt/corporatetraveldc/private/ctdi-dispatch-internal/` | This repo (canonical; `/opt/corporatetraveldc/ctdi-dispatch-internal` is a symlink) |
| `/var/lib/corporatetraveldc/corporatetraveldc.db` | SQLite database (WAL) |
| `/etc/corporatetraveldc/dispatch.env` | Non-secret config |
| `/etc/corporatetraveldc/dispatch-secrets.env` | Secrets (mode 0600) |
| `/var/lib/corporatetraveldc/api-usage.csv` | SR-1 skill usage log |
| `/var/lib/corporatetraveldc/skill-state/` | SR-2 hash gate state |
| `/run/corporatetraveldc/triggers/` | Admin trigger files |
| `.config/containers/systemd/` (repo) → `~/.config/containers/systemd/` (live) | Quadlet files |
| `.config/systemd/user/` (repo) → `~/.config/systemd/user/` (live) | Plain user units + timers |

## Development commands

All Python commands run from the repo root with `PYTHONPATH=src`:

```bash
cd /opt/corporatetraveldc/private/ctdi-dispatch-internal

# Run a skill manually (--force bypasses SR-2 hash gate)
PYTHONPATH=src python3 src/poller/skills/cps_recompute.py --force
PYTHONPATH=src python3 src/poller/skills/tfr_enrichment.py --force

# Run a fetcher manually
PYTHONPATH=src python3 src/poller/fetchers/metar.py

# Token management (CLI name: ctdc-token — there is no `rotate`; revoke+create)
PYTHONPATH=src python3 src/ctdc_token/cli.py create --user operator --tier admin --label admin-iphone
PYTHONPATH=src python3 src/ctdc_token/cli.py list
PYTHONPATH=src python3 src/ctdc_token/cli.py revoke --prefix ctdc_operator_
PYTHONPATH=src python3 src/ctdc_token/cli.py show-cost

# Run tests
python -m pytest tests/ -x --tb=short
```

**Signed-manifest integrity:** container entrypoints and `llm.py` run
`scripts/verify-manifest.sh` before executing. After changing tracked code,
re-sign with `scripts/sign-manifest.sh` (operator GPG key) or rebuilt
containers/skills will refuse to run. Never bypass this to "make it work".

## Container lifecycle

```bash
bash build-images.sh                      # all images; pass a name for one
systemctl --user daemon-reload
systemctl --user restart corporatetraveldc-web corporatetraveldc-poller corporatetraveldc-pusher

# Logs / health
journalctl --user -u corporatetraveldc-poller --no-pager -n 50
curl http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/api/v1/feeds

# Ingest is 7 per-feed containers — use the control script, staggered:
scripts/ingest-feed-ctl.sh restart all --order=lightest-first --stagger=15
sqlite3 /var/lib/corporatetraveldc/corporatetraveldc.db "SELECT * FROM cps_scores ORDER BY computed_at DESC LIMIT 3;"
```

### Container resource limits

> ⚠️ **SINGLE-EDGE-UNIT ASSUMPTION.** Every value here (Memory/CPUQuota/
> CPUWeight, `OLLAMA_TIMEOUT=240`, `OLLAMA_PREFLIGHT_COOL_TARGET_C=70`, the
> thermal governor, single-model residency) is tuned for shared-resource
> contention on ONE Raspberry Pi 5 (4 cores, 16 GB, no GPU). If the stack is
> de-consolidated, re-measure per node — see
> `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md`.

Core containers carry `Memory=1536m` + `PodmanArgs=--memory-swap=1536m`
(swap=memory ⇒ clean OOM-kill instead of zram thrash), `CPUWeight=100`,
`CPUQuota=300%` (never more than 3 of 4 cores). The 7 ingest containers are
sized individually (256m–448m, 60%–150% quota — see each Quadlet). Host
`ollama.service` runs at `CPUWeight=500` so it wins ~5:1 under contention.
CPU directives are `[Service]`-level (parent cgroup), so the podman payload
cgroup showing `cpu.max: max` is expected — check the parent. After editing a
`.container` file: `systemctl --user daemon-reload && systemctl --user
restart <name>` (edits don't apply to a running container).

`container-mem-watch.sh` (timer, 2 min) alerts on memory pressure/OOM-kills
to `ops-health` — observation only, never restarts anything.

## Architecture overview

### Core containers

- **web** (`src/web/`) — FastAPI, tiered REST API, port 8000 (published
  127.0.0.1 + 100.x.x.x).
- **poller** (`src/poller/`) — async scheduler; fetchers on intervals, skills
  as subprocesses (independent SR-1/SR-2 state), watchlist sweeps, trigger
  directory watcher.
- **pusher** (`src/pusher/`) — ntfy sender; polls DB every 30 s.
- **ingest** (`src/ingest/`) — push feeds via NMS/Solace. 7 per-feed Quadlets:
  `corporatetraveldc-ingest-{core,fdps,stdds,tfms,tbfm,itws,notam}` (unit
  `notam` = feed `fns`). All six SWIM feeds live since 2026-07-20. The REST
  `notam` fetcher separately still needs `FAA_NOTAM_API_KEY`/`_SECRET`
  (`awaiting_credentials`) — NOTAM data flows via push:fns regardless.
- **runner** (`src/runner/`) — ops dashboard SPA + API, port 8001,
  tailnet-only (public `ops.` hostname retired 2026-08-02, hard-404'd via
  `_RETIRED_HOSTNAMES`). Demo instance on :8005 is public at
  `dispatch-runner.example.com`, password-gated (`DEMO_MODE`).

### Auth tiers

Defined in `src/auth/auth.py` (`resolve_tier()`), enforced as FastAPI
dependencies. **Bearer-token only — network origin grants no tier** (the old
Tailscale-header/IP grant was removed as spoofable; don't reintroduce it):

- **T0** — anonymous; also forced for any request with `X-CTDI-Public: 1`
  (stamped by public nginx vhosts), regardless of token
- **T1** — bearer token `tier=cert`
- **T2 (SHARES)** — bearer token `tier=shares`; audit-logged
- **Admin** — bearer token `tier=admin`; all `/admin/*` endpoints

Token format `ctdc_<user>_<32-char-random>`; only the SHA-256 hash is stored.

### Local LLM (Ollama)

- `src/common/llm.py` is the single entry point (`generate()` →
  `ollama_post_with_retry()`), with thermal + load pre-flight gates, a
  client-side slot lock, and signed-manifest verification of the calling
  skill and its Modelfile before inference.
- **`_abandon_ollama_generation()` (2026-08-11):** a client timeout does NOT
  stop the server-side generation — on any transport error/timeout llm.py now
  POSTs `{"model": …, "prompt": "", "keep_alive": 0}` to unload it (orphaned
  generations once piled up to a 52 load average). `build-models.sh`'s smoke
  test does the same.
- 16 dedicated models (`corporatetraveldc-pi5-*`) built by `build-models.sh`
  from repo-root Modelfiles (`corporatetraveldc.<task>`). The 4 brief-class
  models (`ops-brief`, `ops-brief-trend`, `ep-advance`, `ep-advance-trend`)
  are **`FROM phi3:mini`**; everything else is `gemma3:4b`.
  `build-models.sh` hard-blocks gemma2/gemma3 bases for brief models
  (`SWA_DENYLIST_REGEX` — SWA breaks KV-cache reuse and blew the 240 s
  timeout) and only promotes brief models to `:latest` after a 200 s
  smoke test passes. Don't switch brief models back to gemma without
  reading that guard.
- Cloud fallback: `ANTHROPIC_FALLBACK_ENABLED` **defaults true** (it is NOT
  set to false in dispatch.env, despite an old llm.py docstring claim);
  brief skills pass `allow_anthropic=False`. Ollama-unavailable ⇒ skills
  fall back to deterministic templates; `brief-fallback-monitor` (hourly)
  alerts loudly when that happens.

### Database schema

`src/common/db.py` is the single schema authority, versioned additively
(`SCHEMA`, `SCHEMA_V2`, … — check the file for the current top; V31 exists as
of this snapshot). New tables `CREATE TABLE IF NOT EXISTS`; never drop or
rename columns — only `ALTER TABLE ADD COLUMN`.

### Skill runtime rules (SR-1 / SR-2)

Every skill that calls an LLM must follow both:

- **SR-1** (`src/common/sr1_log.py`): `log_usage()` in a `finally` block.
- **SR-2** (`src/common/sr2_gate.py`): `hash_gate()` before the call; hash
  content-bearing fields only; on `"skipped"` → `sys.exit(0)`; support
  `--force`.

### Poller push/pull failover

Push feeds stamp `push:<feed>` heartbeats in `feed_state` every 30 s. Before
each REST poll with a push-primary, `FetchLoop` checks
`failover.push_is_healthy(feed, max_age=90s)` and skips REST when push is
fresh. Note: TFR and NAS have **no** push-primary (a 2026-06-07 POC mapping
that suppressed them was removed 2026-07-23) — they always REST-poll.

### Watchlist system

`src/shared/watchlist.py` — permanent + transient entries in
`watchlist_entries`; dual ntfy push (domain topic + `dispatch`), 5-min
content-aware dedup. Permanent entries are **JSON** files (not YAML) in
`/opt/corporatetraveldc/watchlists/` — `permanent_flights.json`,
`permanent_trains.json`, `permanent_vessels.json` (MMSI) — hot-reloaded by
`WatchlistFileWatcher` (60 s mtime poll). Transient entries auto-expire via
`auto_remove_at` (60 s sweep). OOOI phases never revert. Details:
`src/shared/watchlist_README.md`.

### ntfy topics

Core: `tfr-alert`/`hot-alerts` (5), `flight-alerts`/`train-alerts`/
`vessel-alerts`, `dispatch` (concise everything-feed), `cps`, `wx-alerts`,
`nas-alerts`, `ops-brief`/`ep`/`ep-advance`, `ops-health`, `approval-gate`,
plus the escalating family/zone topics (`tfms-alerts`, `tbfm-zdc`, …) with
per-topic throttles. Full catalog: `docs/ALERT_REFERENCE.md`; rationale:
`docs/ALERT_ARCHITECTURE.md`.

### FAA SWIM feeds

`SWIM_NMS_*` credentials (per-feed `<KEY>` ∈ FDPS/STDDS/TFMS/AIM/TBFM/ITWS)
live in `/etc/corporatetraveldc/dispatch-secrets.env`; all six push feeds are
live. Re-provisioning a feed = update vars + restart that one container
(`scripts/ingest-feed-ctl.sh restart <feed>`), no code changes.

## Operational conventions for agents

- **Never commit or push** — stage-only; the operator runs all commits
  (signed) himself.
- Durable findings also go to the second brain via
  `PYTHONPATH=src python3 -m second_brain.remember --stdin --tags <tags>`
  (scrub-gated), in addition to updating repo docs.
- Before touching shared live state (containers, nginx/tunnel config, build
  scripts), check `06-AI-Memory/notepad/` in the vault for concurrent-session
  coordination notes, and drop a checkpoint note after.
- SWIM/FIDS timestamps are UTC — always convert to America/New_York when
  presenting arrival times.
