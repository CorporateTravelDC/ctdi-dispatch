# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this system is

Real-time executive travel intelligence platform for the Washington DC area. Monitors commercial flights (via FAA SWIM), Amtrak trains, and weather, then fires push alerts through ntfy. Runs as four Podman containers managed by systemd Quadlets under the `corporatetraveldc` user account.

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
PYTHONPATH=src ./venv/bin/python src/ctdc_token/cli.py create --user corey --tier admin --label admin-iphone
PYTHONPATH=src ./venv/bin/python src/ctdc_token/cli.py list
PYTHONPATH=src ./venv/bin/python src/ctdc_token/cli.py revoke --prefix ctdc_corey_
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

## Architecture overview

### Four containers

- **web** (`src/web/`) — FastAPI app. Serves tiered REST API. No auth secrets in responses.
- **poller** (`src/poller/`) — Async scheduler. Runs fetchers on intervals; invokes skills as subprocesses; watches trigger directory for admin commands.
- **pusher** (`src/pusher/`) — ntfy alert sender. Polls DB every 30s for unnotified VIP TFRs and CPS changes.
- **ingest** (`src/ingest/`) — FAA SWIM push feeds via NMS/Solace AMQP. Pending FAA credential provisioning. While credentials are absent, it starts cleanly and stamps `pending_credentials` — the poller falls back to REST automatically.

All four share the same SQLite database via WAL mode.

### Auth tiers

Defined in `src/auth/auth.py`. Four tiers enforced as FastAPI dependencies:

- **T0** — anonymous, no token required
- **T1** — Tailscale (via `Tailscale-User-Login` header or `100.x.x.x` source IP) or `cert` bearer token
- **T2 (SHARES)** — bearer token with `tier=shares`; access audit-logged
- **Admin** — bearer token with `tier=admin`; required for all `/admin/*` endpoints

Token format: `ctdc_<user>_<32-char-random>`. Only SHA-256 hash stored in DB; plaintext shown once on creation.

### Database schema

`src/common/db.py` is the single schema authority. Schema is versioned additively (`SCHEMA`, `SCHEMA_V2` … `SCHEMA_V6`) — each version is applied at startup via `init_db_v{N}()`. All new tables use `CREATE TABLE IF NOT EXISTS`. Never drop or rename columns — only `ALTER TABLE ADD COLUMN`.

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

### FAA SWIM feeds (NMS credentials pending)

When credentials arrive, add to `/etc/corporatetraveldc/dispatch-secrets.env` (see `src/ingest/README.md`), then rebuild and restart the ingest container. No code changes needed — the feed names activate automatically.
