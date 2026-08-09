# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this system is

Real-time executive travel intelligence platform for the Washington DC area. Monitors commercial flights (via FAA SWIM), Amtrak trains, and weather, then fires push alerts through ntfy. Runs as a set of Podman containers managed by systemd Quadlets under the `corporatetraveldc` user account -- core stack (web/poller/pusher/ingest, ingest itself split into 7 per-feed containers, see below) plus a growing number of standalone timer-triggered skill containers and auxiliary services (ACARS/ADS-B stack, second-brain, daily category watches, etc.). Don't hardcode a total container count here -- it drifts fast; check `systemctl --user list-units 'corporatetraveldc-*' --all` for the live picture.

## Key paths

| Path | Purpose |
|------|---------|
| `/opt/corporatetraveldc/src/` | All Python source |
| `/var/lib/corporatetraveldc/corporatetraveldc.db` | SQLite database |
| `/etc/corporatetraveldc/dispatch.env` | Non-secret config |
| `/etc/corporatetraveldc/dispatch-secrets.env` | Secrets (mode 0600) |
| `/var/lib/corporatetraveldc/api-usage.csv` | SR-1 Anthropic usage log |
| `/var/lib/corporatetraveldc/skill-state/` | SR-2 hash gate state |
| `/run/corporatetraveldc/triggers/` | Admin trigger files |

## Development commands

All Python commands must be run from `/opt/corporatetraveldc` with `PYTHONPATH=src`:

```bash
cd /opt/corporatetraveldc

# Run a skill manually (--force bypasses SR-2 hash gate)
PYTHONPATH=src ./venv/bin/python src/poller/skills/cps_recompute.py --force
PYTHONPATH=src ./venv/bin/python src/poller/skills/route_impact.py --force
PYTHONPATH=src ./venv/bin/python src/poller/skills/tfr_enrichment.py --force

# Run a fetcher manually
PYTHONPATH=src ./venv/bin/python src/poller/fetchers/metar.py
PYTHONPATH=src ./venv/bin/python src/poller/fetchers/tfr.py

# Token management
PYTHONPATH=src ./venv/bin/python src/ctdc_token/cli.py create --user operator --tier admin --label admin-iphone
PYTHONPATH=src ./venv/bin/python src/ctdc_token/cli.py list
PYTHONPATH=src ./venv/bin/python src/ctdc_token/cli.py revoke --prefix ctdc_operator_
PYTHONPATH=src ./venv/bin/python src/ctdc_token/cli.py show-cost

# Run tests
python -m pytest src/poller/ -x --tb=short
```

## Container lifecycle

```bash
cd /opt/corporatetraveldc

# Build all four images (web, poller, pusher, ingest)
bash build-images.sh

# After a build, reload and restart
systemctl --user daemon-reload
systemctl --user restart corporatetraveldc-web
systemctl --user restart corporatetraveldc-poller
systemctl --user restart corporatetraveldc-pusher

# Service logs
journalctl --user -u corporatetraveldc-poller --no-pager -n 50
journalctl --user -u corporatetraveldc-web    --no-pager -n 50
podman logs corporatetraveldc-poller

# Health check
curl http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/api/v1/feeds

# Direct DB inspection
sqlite3 /var/lib/corporatetraveldc/corporatetraveldc.db "SELECT * FROM cps_scores ORDER BY computed_at DESC LIMIT 3;"
```

### Container resource limits

> ⚠️ **SINGLE-EDGE-UNIT ASSUMPTION.** Every value in this section (Memory /
> CPUQuota / CPUWeight), plus the Ollama `OLLAMA_TIMEOUT=240` brief timeout,
> `OLLAMA_PREFLIGHT_COOL_TARGET_C`, the thermal governor, and `MAX_LOADED=1`, is
> tuned for **shared-resource contention on ONE Raspberry Pi 5** (4 cores, 16 GB,
> no GPU, one thermal envelope). If the stack is ever de-consolidated (Ollama on
> dedicated hardware, DNS/Tailscale on a separate device, containers spread across
> nodes), **most of these numbers become obsolete or overly conservative and must
> be re-measured per node — do not carry them forward blindly.** Full rationale +
> what changes on each topology shift: `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md`.

Every `.container` file carries the same four resource-control lines -- copy
them into any new container's quadlet file (this is the "going forward"
convention, not just what's on the box today):

```
[Container]
Memory=1536m
PodmanArgs=--memory-swap=1536m

[Service]
CPUWeight=100
CPUQuota=300%
```

- `Memory=1536m` -- hard RAM ceiling (1.5GiB). `PodmanArgs=--memory-swap=1536m`
  sets swap-equals-memory, i.e. zero additional swap: without it a leaking
  container will swap-thrash on the host's zram device instead of getting a
  clean OOM-kill, which burns CPU and never actually frees anything. Verify
  with `memory.max` / `memory.swap.max` under the container's payload cgroup
  (`/proc/<pid>/cgroup`, find the `libpod-payload-*` path under
  `/sys/fs/cgroup/...`).
- `CPUWeight=100` -- proportional CPU share (cgroup v2 `cpu.weight`), the
  systemd default. Only matters when something else is actually contending
  for CPU right now; on an idle box a container can still burst across all
  4 cores for free. Host `ollama.service` runs at `CPUWeight=500` (see
  `scripts/ollama_governor.sh`), so under real contention Ollama wins
  roughly 5x the contested time -- clearly preferred without hard-starving
  the dispatch stack.
- `CPUQuota=300%` -- hard ceiling regardless of contention: no single
  container (or Ollama) may ever claim more than 3 of the Pi's 4 cores,
  even when the box is otherwise idle.
- These are `[Service]` directives on the systemd unit quadlet generates,
  not `[Container]` -- they land on the parent `<service>.service` cgroup,
  not the podman-managed payload sub-cgroup nested under it. cgroup v2
  enforces hierarchically, so the payload cgroup showing `cpu.max: max`
  locally is expected; check the parent (`dirname` of the payload path) to
  see the real value.
- After editing any `.container` file: `systemctl --user daemon-reload`
  then `systemctl --user restart <name>.service` -- edits don't take effect
  on a running container until it's recreated.

`container-mem-watch.sh` (user timer, every 2min) watches for sustained
high memory pressure and real OOM-kills across all live containers and
alerts to ntfy `ops-health` -- observation only, never restarts anything.
Check current state any time with `container-mem-watch.sh --status`.

## Architecture overview

### Core containers

- **web** (`src/web/`) — FastAPI app. Serves tiered REST API. No auth secrets in responses.
- **poller** (`src/poller/`) — Async scheduler. Runs fetchers on intervals; invokes skills as subprocesses; watches trigger directory for admin commands.
- **pusher** (`src/pusher/`) — ntfy alert sender. Polls DB every 30s for unnotified VIP TFRs and CPS changes.
- **ingest** (`src/ingest/`) — FAA SWIM push feeds via NMS/Solace AMQP. Split into 7 per-feed containers (`corporatetraveldc-ingest-core`, `-fdps`, `-stdds`, `-tfms`, `-tbfm`, `-itws`, `-notam`). SWIM_NMS_* credentials are provisioned in `dispatch-secrets.env` and all 7 are live (confirmed 2026-08-07: `feed_state` shows push:fdps/stdds/tfms/tbfm/itws/fns all fresh, zero errors). The REST `notam` fetcher specifically still needs `FAA_NOTAM_API_KEY` (separate from SWIM credentials) -- see the FAA SWIM feeds section below.

Plus many standalone timer-triggered skill containers (daily/weekly watches, second-brain, health checks) and auxiliary services (ACARS/ADS-B stack, Nextcloud, ntfy) beyond this core set. All share the same SQLite database via WAL mode.

### Auth tiers

Defined in `src/auth/auth.py`. Four tiers enforced as FastAPI dependencies:

- **T0** — anonymous, no token required
- **T1** — Tailscale (via `Tailscale-User-Login` header or `100.x.x.x` source IP) or `cert` bearer token
- **T2 (SHARES)** — bearer token with `tier=shares`; access audit-logged
- **Admin** — bearer token with `tier=admin`; required for all `/admin/*` endpoints

Token format: `ctdc_<user>_<32-char-random>`. Only SHA-256 hash stored in DB; plaintext shown once on creation.

### Database schema

`src/common/db.py` is the single schema authority. Schema is versioned additively (`SCHEMA`, `SCHEMA_V2` … currently through `SCHEMA_V30`, open-ended -- check `src/common/db.py` for the actual current top version rather than trusting a number in this doc) — each version is applied at startup via `init_db_v{N}()`. All new tables use `CREATE TABLE IF NOT EXISTS`. Never drop or rename columns — only `ALTER TABLE ADD COLUMN`.

### Skill runtime rules (SR-1 and SR-2)

Every skill that calls the Anthropic API must follow both rules:

- **SR-1** (`src/common/sr1_log.py`): call `log_usage()` in a `finally` block — always, including on error.
- **SR-2** (`src/common/sr2_gate.py`): call `hash_gate()` before the API call. Hash only content-bearing fields (never timestamps). If gate returns `"skipped"`, call `sys.exit(0)` immediately. Support `--force` flag to bypass.

The poller runs skills as subprocesses so each has independent SR-1/SR-2 state and its own log entries.

### Poller push/pull failover

The ingest container stamps heartbeats for `push:fdps` and `push:stdds` in `feed_state` every 30s. Before each REST poll, `FetchLoop` calls `failover.push_is_healthy(feed, max_age=90s)`. If the push is healthy, the REST poll is skipped — ingest owns that feed. When ingest disconnects, the heartbeat ages out and REST polling resumes automatically.

### Watchlist system

`src/shared/watchlist.py` manages permanent and transient watch entries in `watchlist_entries` table. Events fire dual ntfy pushes: domain topic (`flight-alerts` / `train-alerts`) for full detail and `dispatch` for the concise bottom line. 5-minute dedup window prevents re-firing the same event type for the same entry.

Permanent entries live in `/opt/corporatetraveldc/watchlists/` as YAML files and are watched by `WatchlistFileWatcher`. Transient entries have an `auto_remove_at` timestamp and are swept by `WatchlistSweep` every 60s.

Flight monitoring uses `airplanes.live` free API by default, with fallback to FlightAware AeroAPI (if `FLIGHTAWARE_API_KEY` set) and schedule inference when ADS-B is dark. OOOI phase state machine: `pre_departure → out → off → on → in` — phases never revert.

### ntfy topics

| Topic | Content | Priority |
|-------|---------|---------|
| `tfr-alert` | VIP/POTUS TFR | 5 (max) |
| `flight-alerts` | OOOI events, diversions | 4–5 |
| `train-alerts` | Amtrak delay events | 4–5 |
| `dispatch` | Concise bottom line for all events | mirrors source |
| `cps` | CPS score changes | 3–5 |
| `ops-brief` | Daily/weekly brief | 3 |
| `ops-health` | Freshness audit | 2 |

### FAA SWIM feeds

SWIM_NMS_* credentials (FDPS/STDDS/TFMS/AIM/TBFM/ITWS) are provisioned in `/etc/corporatetraveldc/dispatch-secrets.env` and all six SWIM push feeds are live -- confirmed 2026-08-07 via `feed_state` (push:fdps/stdds/tfms/tbfm/itws/fns all fresh, zero consecutive_failures). If a SWIM feed ever needs re-provisioning, add/update the relevant `SWIM_NMS_USER/PASS/QUEUE_<FEED>` vars, then rebuild and restart the matching per-feed ingest container (`corporatetraveldc-ingest-<feed>`) -- no code changes needed, feed names activate automatically.

Separately, the REST `notam` fetcher (`src/poller/fetchers/notam.py`, distinct from the SWIM AIM/FNS NOTAM push feed) genuinely still needs `FAA_NOTAM_API_KEY`/`FAA_NOTAM_API_SECRET` -- `feed_state` shows `notam: awaiting_credentials`. This is NOT the same gap as the SWIM credentials above; live NOTAM data is already flowing via `push:fns` -> `ingest/parsers/aim_parser.py` -> the `notams` table regardless of this REST fetcher's status.
