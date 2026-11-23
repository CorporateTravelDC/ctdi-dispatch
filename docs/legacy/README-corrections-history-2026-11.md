# README correction history (moved 2026-11-23)

Moved verbatim from README.md on 2026-11-23 (operator: superseded material to docs/legacy). History only; the README is the current authority.

Each entry below is the original README.md text, copied byte for byte from the tree before the move, with its struck
(`~~...~~`) passages and dated "(corrected ...)" / "(superseded ...)" wrappers intact. Line numbers refer to that
original file. Entries are grouped under the README section they came from. The README now keeps only the current
statement from each one. Older superseded sections are in [`README-superseded-2026-11.md`](README-superseded-2026-11.md).

## Opening section (title block, introduction and counts table)

### Entry 1: original lines 6–7

````text
`1491bf5a` plus the batch staged for the next relay, and against the live system. Corrections are dated
"(corrected 2026-10-15 …)" in place.
````

### Entry 2: original lines 8–10

````text
Re-verified 2026-10-25 15:39–15:50Z / 11:39–11:50 ET against HEAD `5f7fca01` and the live system
(docs-first revalidated sweep, stage 3); corrections are dated "(corrected 2026-10-25 …)" or
"(superseded 2026-10-25 …)" in place.
````

### Entry 3: original lines 11–13

````text
Opening section re-checked 2026-11-21 23:55Z / 18:55 ET against HEAD `703d8b70` (clean tree), the
operator status snapshot `/srv/ctdc-handoff/status/status.json` (generated 23:54Z) and the public
dispatch GETs; corrections are dated "(corrected 2026-11-21 …)" in place. Live figures that the
````

### Entry 4: original lines 17–36

````text
Multi-region real-time travel intelligence platform. Monitors commercial
aviation (~~six~~ seven (corrected 2026-11-21: TFDM added 2026-11-19, `e2fb66b0`) FAA SWIM push feeds plus REST fallbacks), rail, weather and
airspace restrictions, and pushes an alert when something operationally
relevant changes. Runs on one Raspberry Pi 5 as rootless Podman containers
managed by systemd Quadlets under one deployment user, alongside
timer-driven skill containers, a local SDR receive stack and one host-level
llama.cpp (`llama-server`) inference unit. ~~All LLM inference is local; no
cloud LLM is called from this deployment.~~ (corrected 2026-10-15: the
platform's own runtime inference is local, and the `src/common/llm.py` cloud
fallback is closed (see "Local LLM" below), but this deployment does call a
cloud model on a schedule. `corporatetraveldc-second-brain-weekly-dump.timer`
(enabled; ~~Sun 02:00Z~~ (corrected 2026-10-25: `OnCalendar=Sun *-*-* 02:00:00 America/New_York`,
i.e. 06:00Z while EDT / 07:00Z while EST, `.config/systemd/user/corporatetraveldc-second-brain-weekly-dump.timer:7`)) runs `claude --model opus` over `CLAUDE.md` and Claude's
memory files (`scripts/second-brain-weekly-dump.sh:93`, corrected 2026-11-21 from `:139`). When that run fails,
a local Llama backstop takes over
(`corporatetraveldc-claude-weekly-llama-fallback.timer`, 00/06/12/18Z; ~~it cannot
generate as configured, see "Local LLM"~~ (superseded 2026-10-25: its persona
`claude-weekly-fallback` now exists in `src/common/personas.py:807` and
`persona_key_for()` maps the default model name to it, see "Local LLM")). The operator's agents (Claude Code and Codex on the box, ChatGPT and Cowork
through the agent gateway) run on vendor-hosted models.)
````

### Entry 5: original line 42

````text
| Containers running | ~~35 long-running (36 at measurement: one oneshot skill mid-run)~~ ~~(corrected 2026-10-15 00:51Z: 38 = 37 long-running, now including the two demo portals, + 1 oneshot skill mid-run)~~ ~~(superseded 2026-10-25 15:39:55Z: 39 running; the two demo portals are **not** among them, see Status)~~ (corrected 2026-11-21 23:54Z: 38 running per `status.json`; the two demo portals are still not among them) | `podman ps --format '{{.Names}}' \| wc -l` |
````

### Entry 6: original line 43

````text
| `.container` Quadlets tracked in this repo | ~~76 (38 long-running, 38 `Type=oneshot` skills/jobs)~~ (corrected 2026-10-15: 77 = 38 long-running + 39 `Type=oneshot` skills/jobs) ~~(2026-10-20: 76 after the cleanup relay retired `corporatetraveldc-ops-brief-deferred` and `corporatetraveldc-nms-v240-check` to `retired-20261020/`, `docs/RETIRED.md`)~~ ~~(corrected 2026-10-25: 77 top-level tracked files and 77 installed live; the 2026-10-20 cleanup retired two to `retired-20261020/` and `corporatetraveldc-opensky.container` was added afterwards. 38 of them run through `verified-exec.sh`: 37 `Type=oneshot` skills/jobs + `demo-api`)~~ (corrected 2026-11-21: 78 top-level tracked: the 2026-10-25 count of 77 plus `corporatetraveldc-ingest-tfdm.container`, added 2026-11-19 in `e2fb66b0`; 38 of them run through `verified-exec.sh`: 37 `Type=oneshot` skills/jobs + `demo-api`. Installed count not re-measured) | `git ls-files ':(glob).config/containers/systemd/*.container' \| wc -l` |
````

### Entry 7: original line 44

````text
| Long-running Quadlets not installed live | ~~2: `corporatetraveldc-demo-portal-{client,personal}` (tracked, never installed). The `corporatetraveldc-client-demo@.container` template is now installed but has no instance~~ (corrected 2026-10-15: 0. Both demo portals have been installed and active since 2026-10-12 (see Status). The `corporatetraveldc-client-demo@.container` template is installed with no instance. The diff now shows only the repo-side `amtrak-tracker.container.disabled` and `corporatetraveldc-acarsdec.container.disabled` (these create no unit) and, live-only, two `corporatetraveldc-ultrafeeder.container.bak-*` files and `nextcloud-net.network`.) (2026-10-20: `amtrak-tracker.container.disabled` retired; `nextcloud-net.network` now tracked; the two `.bak-*` files removed live by the cleanup relay; the diff shows only `corporatetraveldc-acarsdec.container.disabled` and the `retired-20261020/` folder.) | `diff <(ls .config/containers/systemd) <(ls ~/.config/containers/systemd)` |
````

### Entry 8: original line 45

````text
| Active user timers | ~~64 (59 `corporatetraveldc-*`)~~ ~~(corrected 2026-10-15: 72 listed, 67 of them `corporatetraveldc-*`; 75 `.timer` files tracked)~~ ~~(superseded 2026-10-25 15:39:55Z: 79 listed, 74 of them `corporatetraveldc-*`; 78 top-level `.timer` files tracked)~~ ~~(corrected 2026-10-26: 80 listed, 75 of them `corporatetraveldc-*`; 78 top-level `.timer` files tracked, 79 installed; `systemctl --user list-timers --all`, `git ls-files`, `ls` 22:36Z)~~ (corrected 2026-11-21 23:54Z: 84 `corporatetraveldc-*` timers active per `status.json`; 90 top-level `.timer` files tracked. The full `list-timers` total and the installed count were not re-measured) | `systemctl --user list-timers --all` |
````

### Entry 9: original line 46

````text
| Root (system) timers for this stack | ~~7 (watchdog 90 s, team-liveness, skill-grants, llama-council, NTS cert refresh, Tailscale cert renew, watchdog-tune one-shot)~~ (corrected 2026-10-15: 9. Those 7, plus `corporatetraveldc-agent-leak-scan` (hourly) and `corporatetraveldc-agent-home-snapshot` (daily 03:20Z). The watchdog-tune one-shot fired 2026-10-12 00:00Z.) (corrected 2026-11-22: 13 tracked, `git ls-files 'systemd/*.timer' 'systemd/system/*.timer'`: the 9 above plus `agent-pamphlet-install`, `agent-remote-control-boot`, `wifi-sdio-recovery` and `oob-budget`. Root units are not in `status.json`, so the installed count was not re-measured) | `systemctl list-timers --all` |
````

### Entry 10: original line 47

````text
| Failed user or system units | ~~1: `corporatetraveldc-integrity-sweep` (user), failing on the unsigned staged files of this batch; clears at the next sign~~ ~~(corrected 2026-10-15 00:51Z: 0 user, 0 system. While a batch's edited files are unsigned, the integrity sweep fails until the next sign.)~~ (corrected 2026-11-21 23:54Z: 3 user (`corporatetraveldc-backup`, `corporatetraveldc-backup-freshness`, `corporatetraveldc-brain-capacity-review`) and 1 system (`user@1001.service`, the user manager of `ctdc-agent-anthropic-claude`), per `status.json`) | `systemctl --user --failed; systemctl --failed` |
````

### Entry 11: original line 48

````text
| Feeds reported by `/api/v1/feeds` | ~~20 (12 REST + 8 `push:*` heartbeats)~~ (corrected 2026-11-21 23:55Z: 21 = 12 REST + 9 `push:*` heartbeats; `push:tfdm` is the new one) | `curl -s http://127.0.0.1:8000/api/v1/feeds` |
````

### Entry 12: original line 49

````text
| Web API routes | ~~126 (82 in `src/web/main.py`, 44 in `src/web/routes/*.py`)~~ ~~(corrected 2026-10-15: 128 = 83 in `src/web/main.py` + 45 in `src/web/routes/*.py`)~~ ~~(corrected 2026-10-25: 135 = 81 in `src/web/main.py` + 54 in `src/web/routes/*.py`, AST count 15:41:53Z)~~ (corrected 2026-11-21: 139 = 81 in `src/web/main.py` + 58 in `src/web/routes/*.py`, AST count at `703d8b70`) | AST count of route decorators |
````

### Entry 13: original line 50

````text
| Postgres migrations | ~~73 (`src/common/pg_schema/0001`–`0073`)~~ ~~(corrected 2026-10-15: 75, `0001`–`0075`; `0075_surface_event_log`)~~ ~~(corrected 2026-10-25: 83 `.sql` files, numbered up to `0084_semantic_vessel_drone_geometry`)~~ (corrected 2026-11-21: 88 `.sql` files, numbered up to `0089_swim_census_and_expiry`) | `ls src/common/pg_schema/*.sql \| wc -l` |
````

### Entry 14: original lines 60–64

````text
Container counts move in both directions by design: Quadlet-managed
containers are removed (not just stopped) when their unit stops, so a
thermal LOCKDOWN shrinks `podman ps -a`; and the ~~38~~ ~~39 (corrected 2026-10-15)~~ 37 (corrected 2026-10-25: two retired 2026-10-20) oneshot skill containers
exist only while they run, so overlapping timers push the count above the
long-running baseline.
````

## Status

### Entry 15: original line 203

````text
| Database | **Postgres** (`corporatetraveldc-pgsql`, `postgres:16-alpine`), `DISPATCH_DB_BACKEND=postgres` in `/etc/corporatetraveldc/dispatch.env`. Containers reach it over the unix socket in the shared `corporatetraveldc-pgsql-sock` volume mounted at `/var/run/postgresql`; the host reaches it on `127.0.0.1:5432`. Cutover 2026-09-18 (reference/chat/demo tables 09-19/09-20). ~~73~~ ~~75 (corrected 2026-10-15)~~ ~~83 (corrected 2026-10-25, numbered to `0084`)~~ 88 (corrected 2026-11-22, numbered to `0089`; `0080` is unused) additive migrations in `src/common/pg_schema/`. `REFERENCE_TABLES` in `src/common/db_backend.py` is an empty frozenset; `sqlite` survives only as a rollback backend selector. See `docs/POSTGRES_MIGRATION.md`. |
````

### Entry 16: original line 205

````text
| Public demo (runner-demo) | Up: `https://dispatch-runner.example.com` returns 200, `:8005/healthz` 200. Quadlet sets `DEMO_MODE=true`; `DEMO_SESSION_SECRET` comes from `/etc/corporatetraveldc/demo-secrets.env`. **The data it replays is stale:** demo-api serves `/var/lib/corporatetraveldc-demo-source/demo-source.db`, last written 2026-08-14 14:21Z; the scrub+promote timer `corporatetraveldc-demo-source-refresh.timer` is `disabled` and has never fired. Re-checked 2026-10-15 00:55Z: all still true (`:8005/healthz` ok, public hostname 200, file unchanged since 2026-08-14 14:21:42Z, timer disabled). Added 2026-10-15: runner-demo and demo-api also mount the shared Postgres socket and load `dispatch.env`, because the demo password-gate profiles (`demo_profiles`, migration 0057) moved to Postgres on 2026-09-20. Playback data still comes only from `demo-source.db`. (2026-10-20: superseded. Since relay 2 (2026-10-19), playback and profiles come from the demo Postgres instance `corporatetraveldc-pgsql-demo`, and the refresh timer runs nightly at 03:17 UTC. `demo-source.db` is destroyed, and the sqlite rollback and both sovereign mounts are removed: `docs/DEMO_DATA_ISOLATION_PLAN_2026-08-13.md` addendum 2026-10-20.) (2026-11-21 23:59Z, status.json: runner-demo, demo-api and `corporatetraveldc-pgsql-demo` running (pgsql-demo healthy); the refresh timer is enabled, last fired 2026-11-21 03:17Z, next 2026-11-22 03:17Z.) |
````

### Entry 17: original line 206

````text
| Demo portals (added 2026-10-15) | `corporatetraveldc-demo-portal-client` (:8088) and `-personal` (:8087): `nginx:alpine` containers serving `/home/corporatetraveldc/demos` read-only, bound to 127.0.0.1 and the tailnet IP. ~~Installed and active since 2026-10-12.~~ (corrected 2026-10-25 15:39:55Z: both units are `inactive (dead)` and nothing listens on :8087/:8088; `corporatetraveldc-demo-portals-boot.service` is enabled but also inactive. Whether they should be running is an operator decision; see the sweep register.) (Re-checked 2026-11-22 00:00Z: still true; neither portal container is running, nothing listens on :8087/:8088, and the boot unit is enabled but inactive.) They have no tunnel ingress (none in `cloudflared/config.yml`). Swapped with `scripts/demo-portal.sh`. Their Quadlet headers cite `docs/DEMO_PORTALS.md`, which does not exist; `docs/CLIENT_DEMO_PATTERN.md` describes the pattern. | <!-- not-a-claim -->
````

### Entry 18: original line 208

````text
| Backup uplink (added 2026-10-15) | ~~The cellular backup uplink has been down since 2026-10-06, so failover has no working second path.~~ (superseded 2026-10-25 15:45:56Z: `enu1` is connected (NetworkManager profile `Verizon Hotspot`, metric 600) and the failover watchdog reports `primary=wld0:ok backup=enu1:ok`, `failed_over=0`; the second path is back.) (2026-11-18: the hotspot profile was renamed `Hockey Puck` and its metric is now 1100. On 2026-11-22 00:00Z both uplinks are up, `jorransgateway 1` on `wld0` at metric 600 and `Hockey Puck` on `enu1` at metric 1100, so `wld0` carries the default route.) (Added 2026-11-22: route tiers in `scripts/net-failover-watchdog.sh` are 600 `jorransgateway 1`, 1100 `Hockey Puck` and 1600 the puck's own WiFi as last resort, with 100 reserved for a planned Starlink. They apply to IPv4 and IPv6 alike. The watchdog runs as the operator's user service, so `config/polkit/49-ctdc-net-failover.rules` (2026-11-17) lets that account switch NetworkManager connections. It refuses while a lockdown marker or the uncommanded-reboot hold is present, and falls back to NetworkManager's default "auth" if a hold cannot be read. `config/network/gai.conf` has no precedence lines, so address selection is IPv6-first.) See `docs/NETWORK_FAILOVER.md`. |
````

### Entry 19: original line 210

````text
| Agent gateway (OAuth 2.1 + remote MCP) | Live at `https://agents.example.com` since 2026-10-05: `/.well-known/oauth-authorization-server` 200, other paths 404. Per-agent connector slugs at `/mcp/{slug}`: `chatgpt`, `claude-code`, `codex`, `cowork` (~~all active 2026-10-08~~ (corrected 2026-10-25 15:45:56Z, `scripts/agent-gateway.sh status`: `chatgpt`, `claude-code` and `cowork` each have one active connection (cowork also one dormant and one revoked); both `codex` connections are revoked (`revoke-tokens`, the account is inert since 2026-10-22)); `POST /mcp/codex` without a token returns 401). (2026-11-22: neither `ctdc-agent-openai-*` account is in `/etc/ctdc-accounts.conf` any more; see "Team accounts". The connector list itself was not re-checked.) Consent is the operator's SSH-signed approval. Every tool call is recorded in the audit chain as `agent.tool.call` (connector, account, tool, outcome, SHA-256 of the arguments, never their content; since 2026-10-08). Global kill: `scripts/agent-gateway.sh kill-all`. Code `src/web/routes/agent_gateway.py`, migrations 0071/0073. See `docs/AGENT_SEGMENTATION.md`. |
````

### Entry 20: original line 215

````text
| Local LLM | One user unit, `corporatetraveldc-llama.service`: `llama-server` bound to `100.x.x.x:8093`, model `qwen3-4b-instruct-2507-q4_0.gguf`, `-np 2 --kv-unified -c 12288`, `-t 2 -tb 2`, `CPUQuota=200%`. Ollama is gone (nothing on :11434). Added 2026-10-15: `corporatetraveldc-claude-weekly-llama-fallback` (Quadlet + timer, 00/06/12/18Z) is meant to use this server to stand in for the weekly Claude dump when that run fails. ~~Its model name has no persona mapping, so it cannot generate as configured (see "Local LLM").~~ (superseded 2026-10-25: the persona `claude-weekly-fallback` exists in `src/common/personas.py:807` and `persona_key_for("corporatetraveldc-pi5-claude-weekly-fallback:latest")` resolves to it; see "Local LLM".) |
````

### Entry 21: original line 216

````text
| ADS-B / ACARS / VDL2 receive | Up: ultrafeeder, acarsrouter, acarshub, dumpvdl2, acars-watcher and ~~four~~ five (corrected 2026-10-25: `corporatetraveldc-opensky` added since 2026-10-20) aggregator feeders all running (re-checked 2026-11-21 23:59Z, status.json). |
````

### Entry 22: original line 217

````text
| Signed-manifest integrity sweep | ~~2026-10-08 12:10Z: **failing** on one unsigned staged file (this batch); clears at the next sign. The sweep now also checks build provenance of running local images: 2 verified, 16 unverified (built one commit before HEAD), 0 failed. Unverified clears when the images are rebuilt from signed HEAD.~~ ~~(corrected 2026-10-15 00:50Z: passing: "signature valid, all 1442 files match"; build provenance 18 verified, 0 unverified, 0 failed. The sweep fails while a batch's edited files are unsigned and clears at the next sign. Known verifier limits are in `SECURITY.md`.)~~ ~~(superseded 2026-10-25 15:36:18Z, `scheduled-integrity-sweep.sh --status` + `sweep.log`: status ok, "signature valid, all 1712 files match"; build provenance 2 verified, 16 unverified, 0 failed, the 16 being the running local images built 08:17–08:21Z from `5f7fca0`, one commit behind the main checkout's HEAD `8017f02`; it clears at the next rollout from signed HEAD. The sweep fails while a batch's edited files are unsigned and clears at the next sign. Known verifier limits are in `SECURITY.md`.)~~ ~~(2026-11-02 16:26Z, pre-pentest docs parity: **failing** live; `MANIFEST.sha256` now has 2,128 lines. Expected to clear when the 2026-11-02 release is signed and the root copies are reinstalled; if it does not, the sweep journal names the file.)~~ (superseded 2026-11-21 23:48Z, status.json: the last `corporatetraveldc-integrity-sweep.service` run (23:47:50Z) ended `success`, and the timer runs it every 15 min; `MANIFEST.sha256` has 2,264 lines. Build-provenance counts were not re-checked. The sweep fails while a batch's edited files are unsigned and clears at the next sign. Known verifier limits are in `SECURITY.md`.) |
````

### Entry 23: original line 218

````text
| Team accounts (`/etc/ctdc-accounts.conf`) | 2026-10-08: agents `ctdc-agent-anthropic-claude` (login mode claude) and `ctdc-agent-openai-codex` (codex); services `ctdc-agent-anthropic-cowork`, `ctdc-agent-openai-chatgpt`, `ctdc-agent-llama`, `ctdc-agent-dispatch` (preloaded). Liveness is an AND of login, signer key, no token revocation and no kill order (`scripts/team-liveness.sh`). See `docs/AGENT_SEGMENTATION.md`, `docs/AGENT_TRUST_MODEL.md`. ~~(2026-11-02: seven accounts; `ctdc-agent-anthropic-claude-ea` (uid 1007, login mode claude, the operator's executive-assistant agent) is the one this row omitted. The two `ctdc-agent-openai-*` accounts are being retired.)~~ (superseded 2026-11-22, live registry: seven lines. Agents in claude login mode: `ctdc-agent-anthropic-claude`, `ctdc-agent-anthropic-claude-ea` (uid 1007, the operator's executive-assistant agent) and `ctdc-agent-xai-grokbot` (preloaded). Services: `ctdc-agent-llama`, `ctdc-agent-dispatch` (preloaded), `ctdc-agent-anthropic-cowork` and `ctdc-agent-xai-grok`. Neither `ctdc-agent-openai-*` account is listed any more.) |
````

## Architecture

### Entry 24: original lines 252–256

````text
(corrected 2026-11-21: 37 Quadlets in `.config/containers/systemd/` are `Type=oneshot` skill
containers. Most start from a timer; the RSS-driven daily watches start from
`scripts/rss-watch-trigger.sh` and `knowledge-graph-compile` from a `.path` unit. The 38th
`verified-exec` Quadlet is the long-running `demo-api`. Ingest is eight containers since
`ingest-tfdm` landed on 2026-11-19, commit e2fb66b0.)
````

## Architecture › Core containers

### Entry 25: original line 275

````text
| `systemd-corporatetraveldc-demo` / `systemd-corporatetraveldc-demo-api` | `localhost/corporatetraveldc-demo:latest` | Recorder (writes `demo_snapshots` in Postgres) / read-only playback API on :8004 over ~~the scrubbed `demo-source.db`~~ the demo Postgres instance (2026-10-20) |
````

## Architecture › Auxiliary containers (same host)

### Entry 26: original lines 287–292

````text
`corporatetraveldc-airnavradar` and (added 2026-10-25; Quadlet tracked since
2026-10-20, active) `corporatetraveldc-opensky`. Disabled pending hardware, as
`*.container.disabled` files that create no unit: `acarsdec`
(`.config/containers/systemd/`), `dumphfdl`, ~~`ais`~~ `ais-catcher`
(corrected 2026-10-25: `systemd/quadlets/`), `ais-watcher`,
`utm-watcher` (`systemd/`). See `docs/SDR_SERVICES.md`.
````

## Architecture › Data feeds

### Entry 27: original line 356

````text
| `notam` | FAA NMS API (`api-nms.aim.faa.gov`); needs `NMS_API_CLIENT_ID` + `NMS_API_CLIENT_SECRET`, else `awaiting_credentials` | 300 s | REST skipped while `push:fns` is fresh (corrected 2026-11-22: REST row about 29 h old, `pull_state` `auth_gated`, one consecutive failure) |
````

### Entry 28: original line 358

````text
| `amtrak` | api.amtraker.com (REST fallback for `push:amtrak`, wired 2026-09-06) | 300 s | ~~REST row 7.4 h old~~ (corrected 2026-11-22: REST row about 18 h old) because push owns the data |
````

### Entry 29: original line 359

````text
| `eurocontrol` | EUROCONTROL NM B2B | 900 s | ~~`awaiting_credentials`~~ (corrected 2026-11-02: live `pull_state` `unconfigured`, credentials not set) |
````

### Entry 30: original line 360

````text
| `jasdat` | JCAB/MLIT | 900 s | ~~`awaiting_credentials`~~ (corrected 2026-11-02: live `pull_state` `unconfigured`, credentials not set) |
````

### Entry 31: original line 361

````text
| `push:fdps` `push:stdds` `push:tfms` `push:tbfm` `push:itws` `push:fns` | FAA SWIM NMS (Solace), ingest containers | push | ~~heartbeats 15–29 s old~~ (corrected 2026-11-22: 3–32 s old) |
````

### Entry 32: original line 363

````text
| `push:nws` | NWWS-OI (XMPP), ingest-core | push | ~~18 s old~~ (corrected 2026-11-22: 21 s old) |
````

### Entry 33: original line 364

````text
| `push:amtrak` | amtrak-tracker container | push | ~~41 s old~~ (corrected 2026-11-22: 245 s old, inside its 660 s window; the writer stamps once per 300 s poll) |
````

## Architecture › SWIM feed liveness and thermal load-shedding

### Entry 34: original lines 422–441

````text
`scripts/thermal-ingest-guard.py` runs every 120 s
(`corporatetraveldc-thermal-ingest-guard.timer`) and stops and restarts
containers under ~~CPU-load or temperature~~ CPU-load (corrected 2026-10-15) pressure. ~~Defaults from `main()`
(only `THERMAL_GUARD_ENABLED`, `THERMAL_GUARD_RESUME_TEMP_C` and
`THERMAL_GUARD_RESUME_DWELL_S` are set in `dispatch.env`, to the default values):~~

| ~~Trip~~ | ~~Condition~~ | ~~What is shed~~ |
|---|---|---|
| ~~Temp tier 1~~ | ~~`temp >= 74.0 °C`~~ | ~~`tfms`, `stdds` (`THERMAL_GUARD_TIER1_FEEDS`)~~ |
| ~~**LOCKDOWN**~~ | ~~`temp >= 79.0 °C` or `load1 >= 40.0`~~ | ~~everything except `web`: all six SWIM feeds, `ingest-core`, `poller`, `pusher`, `runner` (`LOCKDOWN_USER_UNITS`, fixed in code)~~ |
| ~~Informational only~~ | ~~temp 70–74 °C, load1 15–40, or any count of load-attributed LLM fallbacks~~ | ~~nothing~~ |
| ~~Restore~~ | ~~`temp < 65 °C` and `load1 < 20` (default `RESUME_LOAD` = half of the 40 trip, since 2026-10-03), held 300 s~~ | ~~tier 1 restores its two feeds; LOCKDOWN restores the stack in boot order~~ |
| ~~Dormancy~~ | ~~within 600 s of any restore, observe only (temp ≥ 79 °C still trips)~~ | ~~—~~ |

(corrected 2026-10-15: the table above was right until 2026-10-13. Since the
operator's 2026-10-13 directive the guard is a **load guard only**: the 74 °C
tier-1 and 79 °C LOCKDOWN temperature trips are retired, along with the 65 °C
resume bar. In `main()`, `temp1_trip` and `temp2_trip` are hard-coded `False`,
and `THERMAL_GUARD_TIER1_TEMP_C`, `TIER2_TEMP_C` and `RESUME_TEMP_C` in
`dispatch.env` are ignored. Rationale: `docs/GUARDRAILS_JUSTIFICATION.md` §4.)
````

### Entry 35: original line 445

````text
| **LOCKDOWN** | `load1 >= 40.0` (`THERMAL_GUARD_LOAD_LOCKDOWN`) | ~~everything except `web`: all six SWIM feeds~~ (corrected 2026-11-22: the six feeds in `ALL_SWIM_FEEDS` -- `fdps`, `stdds`, `tfms`, `tbfm`, `itws`, `notam`), `ingest-core`, `poller`, `pusher`, `runner` (`LOCKDOWN_USER_UNITS`, fixed in code). `web` is deliberately kept. Containers not on those lists keep running, among them `ingest-tfdm` (added 2026-11-19; the guard was last changed 2026-10-14), `amtrak-tracker` and `runner-demo` |
````

## API

### Entry 36: original line 505

````text
| Public demo | `https://dispatch-runner.example.com` | Password-gated (`DEMO_MODE=true`); ~~replays the 2026-08-14 snapshot~~ (superseded 2026-10-25: replays a 14-day loop (`LOOP_DAYS = 14`, ~~`src/demo/demo_api.py:46`~~ `src/demo/demo_api.py:78`, corrected 2026-10-25) from the demo Postgres instance refreshed nightly since 2026-10-19, see "Demo mode") |
````

## API › Route inventory

### Entry 37: original lines 510–521

````text
~~135 route decorators (AST count 2026-10-25 15:41:53Z)~~ (corrected 2026-11-22, AST count
at `703d8b70`: 139 route decorators: 81 in `src/web/main.py` (60 GET, 17 POST, 3 DELETE,
1 PATCH) plus `routes/watchlist.py` 11, `agent_gateway.py` 10, `sectors.py` 8,
`console.py` 7, `swim_positions.py` 5, `withheld.py` 4 (added 2026-11-02),
`assignments.py` 3, `fids.py` 3, `webhooks.py` 3, `airspace.py` 2, `data_usage.py` 1,
`remember.py` 1. Gating by dependency: 18 `require_tier` (17 Tier 1, 1 Tier 2),
38 `require_admin(<action>)` dependencies
(`grep -rhoE 'Depends\(require_admin\("[a-z._-]+"\)\)' src/web | wc -l`;
`/admin/push-alert` and its alias `/admin/push-test-alert` share one handler)); the rest are anonymous
at the dependency layer, and several of those carry their own credential
check in the handler (board key/signature, signed approvals, OAuth bearer,
console session, webhook secret, withheld-category device pass).
````

## API › Tier 0 — anonymous (selection)

### Entry 38: original line 528

````text
| GET | `/api/v1/feeds` | Feed freshness + error state (~~20~~ 21 feeds, live count 2026-11-22 00:04:48Z) |
````

## API › Tier 1 — `cert` bearer token

### Entry 39: original lines 558–562

````text
`/api/v1/knowledge-graph/{html,meta}`, `/api/v1/vault/file`, starting a
watchlist session (`POST /api/v1/watchlist`; ~~`GET` and `DELETE /api/v1/watchlist/{session_id}`~~
corrected 2026-11-22: the session `GET`/`DELETE` handlers in `main.py` were removed as dead
duplicates on 2026-10-15; `GET /api/v1/watchlist` is the router's entry list below and
`DELETE /api/v1/watchlist/{entry_id}` is admin, `watchlist.entry.remove`),
````

## API › Admin — `admin` bearer token

### Entry 40: original lines 583–588

````text
sanitize posts, watchlist entry mutations
(~~`POST /api/v1/watchlist/{flights,trains,vessels}[/batch]`~~ (corrected 2026-10-15:
`POST /api/v1/watchlist/{flights,trains,vessels}` and `POST /api/v1/watchlist/{flights,trains}/batch`;
there is no vessels batch route, `src/web/routes/watchlist.py`),
`POST /api/v1/watchlist/permanent/batch`, `DELETE /api/v1/watchlist/{entry_id}`,
`DELETE /api/v1/watchlist/batch`) and `POST /api/v1/remember`.
````

## API › Runner API (port 8001, tailnet-only)

### Entry 41: original lines 607–612

````text
~~30 route decorators in `src/runner/main.py` (32 method+path pairs; the
dispatch proxy takes GET/POST/DELETE).~~ ~~(corrected 2026-10-25, AST 15:41:53Z:
33 route decorators (24 GET, 5 POST, 2 DELETE, 1 PUT, 1 `api_route`), 35
method+path pairs; the dispatch proxy takes GET/POST/DELETE.)~~ (corrected 2026-11-21:
37 route decorators (26 GET, 7 POST, 2 DELETE, 1 PUT, 1 `api_route`), 39
method+path pairs; the dispatch proxy takes GET/POST/DELETE.) Sensitive surfaces (admin proxy,
````

## API › Auth model

### Entry 42: original lines 676–677

````text
**One `/admin/*` route has no bearer dependency:**
`GET /admin/approval-requests/{request_id}/resolve` (`src/web/main.py:2889`; was ~~`:2804`~~, ~~`:2821`~~, ~~`:2823`~~, ~~`:2878`~~ (corrected 2026-11-21), line corrected 2026-10-15, 2026-10-25 and 2026-11-02).
````

## Watchlist system

### Entry 43: original lines 736–766

````text
`vessel-alerts` and `drone-alerts`. (Added 2026-10-20: a permanent entry whose
record sets `alert_topic`, the bellwether set, pushes to `bellwether-alerts`
only, with no `dispatch` copy; `src/shared/watch_topic.py`.) ~~Flight position and identity resolution is local: own
ADS-B, ingested FDPS, local FAA/OpenSky registry tables, FIDS, schedule
inference; FlightAware AeroAPI code is dormant without a key.~~ (corrected
2026-10-15: flight position and identity resolution is local first: own
ADS-B, ingested SWIM, local FAA/OpenSky registry tables, schedule inference.
FIDS is gate and baggage enrichment only, never an identity, tail or OOOI
source. Tail sources are SWIM only: FDPS, the TDES departure clearance
linked by GUFI to its FDPS plan (DCA/IAD/BWI), TBFM and TFMS. Since
2026-10-09 01:06Z (2026-10-08 21:06 ET) this deployment also has the **airplanes.live fallback**
(`src/common/airplanes_live.py`, `AIRPLANES_LIVE_FALLBACK=1` in
`dispatch.env`; the code default is off). It is asked only after every local
source has no contact, for position and identity only, with no OOOI
authority. An identifier on the FAA LADD list is never sent to it. Requests
are at least 1.1 s apart, with a 30 s cache. (Added 2026-10-23: after a 429,
403, 5xx or connection error it stops all calls from that process for the
Retry-After time or a backoff that starts at 60 s and doubles up to 900 s.)
FlightAware AeroAPI code is
dormant without a key; whether a key is configured was not checked in this
pass. (Added 2026-10-18: AeroAPI is also the gate and baggage fallback for a
watched flight when no SWIM or ACARS value exists, at most twice per leg and
under a daily cap, `AEROAPI_GATE_DAILY_CAP` default 50; never baggage at
DCA/IAD/BWI and no AeroAPI time or status is stored; `src/common/gate_aeroapi.py`.
The key, `FLIGHTAWARE_API_KEY`, lives in `dispatch-secrets.env`; whether it is
set was not checked.) Since 2026-10-14 watch matching goes by flight instance (the leg), not
by flight number: `docs/FLIGHT_INSTANCE_RESOLVER.md`.) ~~(The runner's
GLOBE map view still embeds `globe.airplanes.live` in the browser.)~~
(2026-10-24: the GLOBE iframe mode is gone; the runner draws one Leaflet
aircraft map from the local receiver plus stored SWIM positions, with hex-only
`?icao=<hex>` links out. `docs/MAP.md`.)
````

### Entry 44: original lines 771–774

````text
(Added 2026-10-15: the FIDS entry only ranks a tie. The authority gate denies
every FIDS OOOI claim, "enrichment-only, never OOOI-authoritative",
~~`src/common/db.py:6167`~~ ~~`src/common/db.py:7051`~~ `src/common/db.py:7185`
(line corrected 2026-11-21; `_OOOI_SOURCE_PRIORITY` at `:7063`).)
````

## ntfy topics (core set)

### Entry 45: original line 804

````text
| `tfr-alert` / `hot-alerts` | ~~VIP/POTUS TFR, Marine One/AF1, severe-ops events~~ (corrected 2026-10-15: each VIP TFR (FAA type VIP, any region) alerts once on both topics, with Pushover co-fire. The alert carries its own FAA text and active window (UTC + ET; the window comes from the FAA detail record, next relay) and fires again only if the FAA changes the TFR. A DC-area VIP set also gets route impact's ground-route hot alert, once per set. CRANE sightings are no longer labelled Marine One. Severe-ops events also use this tier. See `docs/ALERT_REFERENCE.md`.) | 5 |
````

### Entry 46: original line 816

````text
| `approval-gate` | Approval notices (sudo gate, token gate, council, connector link): deny link only; allowing is a signed `scripts/approve.sh` | 3–5 (corrected 2026-11-22: sudo gate, token gate and console sign-in 4, sudo requests that auto-qualify as high-risk 5; council and connector-link requests 3) |
````

## Demo mode

### Entry 47: original lines 868–877

````text
- `corporatetraveldc-demo-api` (:8004) serves only ~~the scrubbed file
  `/var/lib/corporatetraveldc-demo-source/demo-source.db` (read-only mount)~~
  the scrubbed rows in the demo Postgres instance (2026-10-20: the file is
  destroyed and its mount removed),
  populated only by the host-side `scripts/scrub-demo-source.py` scrub+promote
  pass. ~~That pass's timer, `corporatetraveldc-demo-source-refresh.timer`, is
  disabled and has never run, so the file is still the 2026-08-14 14:21Z
  promotion (1.8 GB).~~ (The timer has run nightly at 03:17 UTC since relay 3,
  2026-10-19; last run 2026-11-21 03:17Z, result success.) `/healthz` reports
  `loop_days: 14`.
````

### Entry 48: original lines 881–889

````text
  signals sanitized server-side. (Added 2026-10-15: the session profiles live
  in Postgres (`demo_profiles`, migration 0057, since 2026-09-20)~~, so both
  runner-demo and demo-api mount the `corporatetraveldc-pgsql-sock` volume
  and load `dispatch.env`~~. (corrected 2026-11-21: since relay 2,
  2026-10-19, `demo_profiles` is on the demo Postgres instance; both
  containers mount only `corporatetraveldc-pgsql-demo-sock` and load
  `demo-secrets.env` and `demo.env`, not `dispatch.env`.) Neither mounts the
  live state tree `/var/lib/corporatetraveldc`; runner-demo mounts the
  demo-only `/var/lib/corporatetraveldc-demo` in its place.)
````

### Entry 49: original lines 890–899

````text
- Demo portals (added 2026-10-15): `corporatetraveldc-demo-portal-client`
  (:8088) and `-personal` (:8087) serve static client-preview sites from
  `/home/corporatetraveldc/demos`, on 127.0.0.1 and the tailnet only, with no
  tunnel ingress. ~~They are active since 2026-10-12.~~ (corrected 2026-10-25
  15:39:55Z: both units `inactive (dead)`, nothing listening on :8087/:8088;
  see Status. Still true 2026-11-22 00:04Z: no portal container is running
  and `corporatetraveldc-demo-portals-boot.service`, which starts them after
  the boot rollout, is `inactive (dead)`.) See
  `docs/CLIENT_DEMO_PATTERN.md`; the `docs/DEMO_PORTALS.md` their Quadlets <!-- not-a-claim -->
  cite does not exist.
````

## Installation › First-time setup

### Entry 50: original lines 991–994

````text
# Start the core stack (production boots through
# corporatetraveldc-stack-boot-stagger, which brings up the 35 units in
# scripts/stack-boot-ctl.sh ORDER one at a time; was 32, then 34 (2026-10-25),
# corrected 2026-11-21: ingest-tfdm added 2026-11-19)
````

## Installation › After any code change

### Entry 51: original lines 1048–1056

````text
(corrected 2026-10-15: the `systemctl --user restart <units>` line describes
a locals-only restart, which the standing rule forbids. Every rollout is a
**full load**: `bash scripts/serialized-rollout.sh` rebuilds the local images,
pulls every external image (an update check) and restarts every container
one at a time, externals included (`RESTART_EXTERNALS=1` is the default). It
takes the heavy-job turn (`full-load rollout`, up to 2 h wait) and gates each
restart on load. Batches are staged in the guard worktree while main stays
frozen; see `docs/GUARDRAILS_PROVEN.md`, "Updates without downtime or drift"
(2026-10-14).)
````

### Entry 52: original lines 1080–1081

````text
Image updates: `scripts/stack-refresh.sh --weekly` (timer
`corporatetraveldc-weekly-external-image-update.timer`, Sun 04:15 America/New_York: 09:15Z under EST, 08:15Z under EDT; corrected 2026-11-21, was "08:15Z" only) pulls
````

### Entry 53: original lines 1087–1088

````text
any finding. Rollback is `podman tag <svc>:previous <svc>:latest` + restart;
`corporatetraveldc-podman-prune.timer` (07:15 America/New_York: 12:15Z under EST, 11:15Z under EDT; corrected 2026-11-21, was "11:15Z" only) prunes dangling images only.
````

### Entry 54: original lines 1094–1098

````text
range ring and MLAT position is wrong. The frontend reads coordinates only
from the runner: `/api/v1/frontend-config`, and on the map `/api/map/scope`'s
receiver centre first (real centre for trusted callers only; corrected
2026-11-21, `src/runner/frontend/src/components/aircraftMap/AircraftMap.jsx`)
(see `docs/GPS_COORDINATE_CONFIGURATION.md`).
````

## Installation › Signed-manifest coverage

### Entry 55: original line 1104

````text
| ~~39~~ ~~40 (corrected 2026-10-15)~~ 38 (corrected 2026-10-25: `grep -l verified-exec ~/.config/containers/systemd/*.container \| wc -l` = 38 at 15:39:53Z) Quadlets whose `Exec=` goes through `scripts/verified-exec.sh` (the 37 oneshot skills and demo-api) | at every start |
````

## Development › Schema

### Entry 56: original lines 1160–1163

````text
Postgres DDL is the ~~73~~ ~~75 (corrected 2026-10-15)~~ ~~83 (corrected 2026-10-25, numbered to `0084`)~~ 88 (corrected 2026-11-21: numbered to `0089`; there is no `0080`) numbered files in `src/common/pg_schema/`, applied by
`scripts/pg-migrate.sh`. The legacy SQLite schema constants in
`src/common/db.py` (`SCHEMA` … `SCHEMA_V40`, ~~`SCHEMA_V43`~~ `SCHEMA_V45` (corrected 2026-10-25)) and
`src/common/db_swim.py` (`SCHEMA_SWIM_V41` … ~~`V46`~~ `V47` (corrected 2026-10-25)) remain for the rollback
````

## Local LLM — llama.cpp

### Entry 57: original lines 1171–1174

````text
- One host process, `corporatetraveldc-llama.service` (user unit, tracked at
  `.config/systemd/user/`): `llama-server -m qwen3-4b-instruct-2507-q4_0.gguf
  --host 100.x.x.x --port 8093 -np 2 --kv-unified -c 12288 -fa on
  -ctk q8_0 -ctv q8_0 -t 2 -tb 2 ~~--cache-ram 0~~ --cache-ram 1024 --cache-reuse 256 (corrected 2026-10-25: changed 2026-10-19, `.config/systemd/user/corporatetraveldc-llama.service:78`) --no-webui`,
````

### Entry 58: original line 1180

````text
- `corporatetraveldc-llama-restart.timer` restarts it daily at 19:45 America/New_York, ~~23:45Z~~ (corrected 2026-11-21: 00:45Z while ET is on standard time, 23:45Z in daylight time; the last fire was 2026-11-21 00:45Z).
````

### Entry 59: original lines 1189–1191

````text
- Per-skill behaviour is the ~~22~~ 23 (corrected 2026-10-25: `claude-weekly-fallback` added) personas in `src/common/personas.py`
  (`PERSONAS`); a skill's `corporatetraveldc-pi5-<task>:latest` model string
  maps to a persona via `persona_key_for()`. The ~~21~~ 23 (corrected 2026-10-25, `git ls-files | grep -cE '^corporatetraveldc\.'`)
````

### Entry 60: original lines 1215–1221

````text
**Scheduled cloud job (added 2026-10-15).** The one job that calls a cloud
model is outside `llm.py`. `corporatetraveldc-second-brain-weekly-dump`
(timer enabled, ~~Sun 02:00Z~~ Sun 02:00 America/New_York, ~~= 06:00Z in EDT (corrected 2026-10-25)~~ 07:00Z while ET is on standard time, 06:00Z in daylight time (corrected 2026-11-21; next fire 2026-11-22 07:00Z)) runs `claude --model opus -p …`
(~~`scripts/second-brain-weekly-dump.sh:139`~~ `scripts/second-brain-weekly-dump.sh:93`, corrected 2026-11-21) ~~over `CLAUDE.md` and Claude's
memory files~~ (corrected 2026-11-21: since 2026-11-01 it reads only the operator session's Claude memory files, with `/srv/ctdc-context/PLATFORM_CONTEXT.md` and the two canonical vault rules notes as instructions; it never resets `CLAUDE.md`, the shared context or `AGENTS.md`, and it resets the memory files only after a confirmed, retrievable vault write; since 2026-11-18 an empty memory directory counts as success with no model run) and writes the result to the vault. On a failed or unconfirmed
run it writes a failure marker
(`common.claude_weekly_fallback.mark_failure`, ~~`scripts/second-brain-weekly-dump.sh:162`~~ `scripts/second-brain-weekly-dump.sh:139`, corrected 2026-11-21).
````

### Entry 61: original lines 1222–1234

````text
`corporatetraveldc-claude-weekly-llama-fallback` (Quadlet + timer, 00/06/12/18Z)
then tries a load-gated partial stand-in on the local llama-server. ~~**Known
defect:** its default model name,
`corporatetraveldc-pi5-claude-weekly-fallback:latest`, has no persona in
`src/common/personas.py`, and neither the Quadlet nor `dispatch.env` sets
`OLLAMA_CLAUDE_FALLBACK_MODEL` or `OLLAMA_MODEL`. `llm.py` therefore answers
502 "no persona mapping", so the backstop cannot generate as configured.~~
(superseded 2026-10-25: `PERSONAS` now has a `claude-weekly-fallback` entry
(`src/common/personas.py:807`) and `persona_key_for()` maps the default model
name `corporatetraveldc-pi5-claude-weekly-fallback:latest` to it; the Quadlet
and `dispatch.env` still set neither `OLLAMA_CLAUDE_FALLBACK_MODEL` nor
`OLLAMA_MODEL`, so the code default applies and resolves. Whether a run has
generated successfully was not checked in this pass.)
````

## FAA SWIM / NMS credentials

### Entry 62: original lines 1280–1287

````text
or agreement that allows it. Independently of the agreement, LADD/PIA
blocking has a federal-law basis (FAA Reauthorization Act of 2024, sec. 803):
~~any Tier-0 caller of those routes is LADD-filtered~~ (corrected 2026-11-21:
since the operator's 2026-11-02 decision tier no longer decides it.
`/positions` and `/last-leg` show a LADD-listed aircraft only to a caller
holding an aircraft pass or the master token; every other caller, Tier 1
included, has it withheld, and `/last-leg` answers a withheld leg exactly as
a miss), and a filter failure is a 503, never an unfiltered list.
````

## Key paths

### Entry 63: original line 1297

````text
| ~~`/var/lib/corporatetraveldc/corporatetraveldc.db`~~ | ~~Legacy SQLite path (`DISPATCH_DB`). **Present**: a 69,632-byte file last modified 2026-10-05 23:21Z (recreated by a CLI smoke test that used the wrong env-var name; see Findings in `CHANGES-core.md`). Not the live store.~~ (2026-10-20: destroyed by the 2026-10-19 relay. `DISPATCH_DB` has no default and is removed from `config/dispatch.env`, so nothing can re-create the file: `docs/POSTGRES_MIGRATION.md` §3.7.) |
````

### Entry 64: original line 1300

````text
| `/etc/corporatetraveldc/svc/<svc>.env` | Per-service scoped secrets (web, poller, pusher, ingest, runner, demo, demo-archive, amtrak-tracker, acars-watcher, execstandard-verifier); ~~53~~ ~~54 (corrected 2026-10-15)~~ ~~52 (corrected 2026-10-25)~~ 53 (corrected 2026-11-22: top-level tracked Quadlets; 52 plus `ingest-tfdm`. The two files in `retired-20261020/` are not counted) Quadlets read one |
````

## Synthetic aircraft identifiers

### Entry 65: original lines 1370–1382

````text
  Later the same day (review 07) a history scan found more: real hex codes
  in five tests and fixtures, two foreign registrations in tests, and the
  same identifiers in the git history of the public mirror and of two other
  public repositories. ~~All were replaced and the public histories rewritten~~
  (corrected 2026-10-15: all were replaced in the current trees, but the
  public-history rewrite was finished on 2026-10-15 01:44Z / 21:44 ET 10-14 for
  the public mirror (6 identifiers, `scripts/leak-guard/ladd_history_scan.py` +
  git-filter-repo; 472 commits kept, tip tree unchanged, a fresh-clone rescan
  finds 0). The archived public repository `corporatetravel-dispatch-mcp`
  still carries 2 identifiers in its history: open operator item) (unverified
  2026-10-25: the public-mirror and archived-repo histories are off-box and
  were not re-scanned in this pass)
  (`docs/security-reviews/2026-10-08-07-agents-and-adversarial-receipt.md`).
````
