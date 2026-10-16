# Corporate Travel Dispatch Intelligence (CTDI)

Re-verified against code (the tree committed with this text, on top of `92231b1`) and live state on 2026-10-08 12:15Z / 08:15 ET. Previous full verification: HEAD `2c3f81b`, 2026-10-06 18:25Z.
Front-door refresh 2026-10-15 00:50–01:00Z / 2026-10-14 20:50–21:00 ET: counts, status, thermal guard,
watchlist identity, CPS, demo, rollout and the documentation index re-checked against the code at main
`1491bf5a` plus the batch staged for the next relay, and against the live system. Corrections are dated
"(corrected 2026-10-15 …)" in place.

Multi-region real-time travel intelligence platform. Monitors commercial
aviation (six FAA SWIM push feeds plus REST fallbacks), rail, weather and
airspace restrictions, and pushes an alert when something operationally
relevant changes. Runs on one Raspberry Pi 5 as rootless Podman containers
managed by systemd Quadlets under one deployment user, alongside
timer-driven skill containers, a local SDR receive stack and one host-level
llama.cpp (`llama-server`) inference unit. ~~All LLM inference is local; no
cloud LLM is called from this deployment.~~ (corrected 2026-10-15: the
platform's own runtime inference is local, and the `src/common/llm.py` cloud
fallback is closed (see "Local LLM" below), but this deployment does call a
cloud model on a schedule. `corporatetraveldc-second-brain-weekly-dump.timer`
(enabled; Sun 02:00Z) runs `claude --model opus` over `CLAUDE.md` and Claude's
memory files (`scripts/second-brain-weekly-dump.sh:139`). When that run fails,
a local Llama backstop takes over
(`corporatetraveldc-claude-weekly-llama-fallback.timer`, 00/06/12/18Z; it cannot
generate as configured, see "Local LLM"). The operator's agents (Claude Code and Codex on the box, ChatGPT and Cowork
through the agent gateway) run on vendor-hosted models.)

Counts at the verification time above (re-run the commands; they move):

| Measure | Value | Command |
|---|---|---|
| Containers running | ~~35 long-running (36 at measurement: one oneshot skill mid-run)~~ (corrected 2026-10-15 00:51Z: 38 = 37 long-running, now including the two demo portals, + 1 oneshot skill mid-run) | `podman ps --format '{{.Names}}' \| wc -l` |
| `.container` Quadlets tracked in this repo | ~~76 (38 long-running, 38 `Type=oneshot` skills/jobs)~~ (corrected 2026-10-15: 77 = 38 long-running + 39 `Type=oneshot` skills/jobs) | `ls .config/containers/systemd/*.container \| wc -l` |
| Long-running Quadlets not installed live | ~~2: `corporatetraveldc-demo-portal-{client,personal}` (tracked, never installed). The `corporatetraveldc-client-demo@.container` template is now installed but has no instance~~ (corrected 2026-10-15: 0. Both demo portals have been installed and active since 2026-10-12 (see Status). The `corporatetraveldc-client-demo@.container` template is installed with no instance. The diff now shows only the repo-side `amtrak-tracker.container.disabled` and `corporatetraveldc-acarsdec.container.disabled` (these create no unit) and, live-only, two `corporatetraveldc-ultrafeeder.container.bak-*` files and `nextcloud-net.network`.) | `diff <(ls .config/containers/systemd) <(ls ~/.config/containers/systemd)` |
| Active user timers | ~~64 (59 `corporatetraveldc-*`)~~ (corrected 2026-10-15: 72 listed, 67 of them `corporatetraveldc-*`; 75 `.timer` files tracked) | `systemctl --user list-timers --all` |
| Root (system) timers for this stack | ~~7 (watchdog 90 s, team-liveness, skill-grants, llama-council, NTS cert refresh, Tailscale cert renew, watchdog-tune one-shot)~~ (corrected 2026-10-15: 9. Those 7, plus `corporatetraveldc-agent-leak-scan` (hourly) and `corporatetraveldc-agent-home-snapshot` (daily 03:20Z). The watchdog-tune one-shot fired 2026-10-12 00:00Z.) | `systemctl list-timers --all` |
| Failed user or system units | ~~1: `corporatetraveldc-integrity-sweep` (user), failing on the unsigned staged files of this batch; clears at the next sign~~ (corrected 2026-10-15 00:51Z: 0 user, 0 system. While a batch's edited files are unsigned, the integrity sweep fails until the next sign.) | `systemctl --user --failed; systemctl --failed` |
| Feeds reported by `/api/v1/feeds` | 20 (12 REST + 8 `push:*` heartbeats) | `curl -s http://127.0.0.1:8000/api/v1/feeds` |
| Web API routes | ~~126 (82 in `src/web/main.py`, 44 in `src/web/routes/*.py`)~~ (corrected 2026-10-15: 128 = 83 in `src/web/main.py` + 45 in `src/web/routes/*.py`) | AST count of route decorators |
| Postgres migrations | ~~73 (`src/common/pg_schema/0001`–`0073`)~~ (corrected 2026-10-15: 75, `0001`–`0075`; `0075_surface_event_log`) | `ls src/common/pg_schema/*.sql \| wc -l` |

`GET /healthz` at the verification time returned
`{"status":"ok","reason":null,"snapshot_age_seconds":1,"audit_count_24h":24,"token_count_active":6,"cps":{"score":"GREEN","label":"GO"}}`
(2026-10-15 00:51Z: the same, except `audit_count_24h` 25).
It does not list feeds; `/api/v1/feeds` does.

Container counts move in both directions by design: Quadlet-managed
containers are removed (not just stopped) when their unit stops, so a
thermal LOCKDOWN shrinks `podman ps -a`; and the ~~38~~ 39 (corrected 2026-10-15) oneshot skill containers
exist only while they run, so overlapping timers push the count above the
long-running baseline.

> **Origin note:** CTDI was built for Washington, DC metro operations
> (executive chauffeur + CERT/ARES/Skywarn). The DC configuration is the
> reference implementation, not a constraint — see
> **[docs/REGIONALIZATION.md](docs/REGIONALIZATION.md)**.

> **Repository note:** the system user, container prefix and filesystem paths
> use `corporatetraveldc`, the original deployment name. New deployments can
> substitute any username; only env config and Quadlet paths need to change.

- **[Platform Compatibility Reference (PDF)](docs/platform-compatibility.pdf)**
- **[Design Principles](docs/DESIGN-PRINCIPLES.md)** — local-first, offline-capable, vendor-neutral.
- **[Regionalization Guide](docs/REGIONALIZATION.md)** — deploying outside DC.
- **[Data Sources & Access Guide](docs/DATA_SOURCES.md)** — signup portals and policy links for every feed.
- **[Internal Infra Map](docs/INFRA_MAP.md)** — private service/host/domain map (private repo only).
- **[Single-Edge-Unit Assumptions](docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md)** — every resource guardrail here is tuned for one Pi 5 under shared-resource contention.
- **[Backups](docs/BACKUP.md)** — encrypted Borg backups, nightly, synced to an off-box target; freshness check and weekly restore test (added 2026-10-15).
- **[Retired code index](docs/RETIRED.md)** — everything taken out of service, by folder (added 2026-10-15).

### Documentation index (added 2026-10-15)

Living documents. Each describes the platform as it is now and keeps changed
statements struck through with the date. Files marked *(private)* are dropped
from the public mirror.

- **Architecture and infra:** [INFRA_MAP](docs/INFRA_MAP.md) *(private)*,
  [REFERENCE_INFRA](docs/REFERENCE_INFRA.md) (the public counterpart),
  [SINGLE_EDGE_UNIT_ASSUMPTIONS](docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md),
  [HARDWARE_GUIDANCE](docs/HARDWARE_GUIDANCE.md),
  [NETWORK_FAILOVER](docs/NETWORK_FAILOVER.md),
  [PI5-BOOT-CONFIG](docs/PI5-BOOT-CONFIG.md),
  [BOOT_STORM_TIMER_REQUIRES](docs/BOOT_STORM_TIMER_REQUIRES.md),
  [NETWORK_STORAGE_CONSTRAINTS](docs/NETWORK_STORAGE_CONSTRAINTS.md),
  [POSTGRES_MIGRATION](docs/POSTGRES_MIGRATION.md),
  [deployment-profiles](docs/deployment-profiles/README.md),
  [REGIONALIZATION](docs/REGIONALIZATION.md).
- **Data and feeds:** [DATA_SOURCES](docs/DATA_SOURCES.md),
  [ingest layer](src/ingest/README.md), [SDR_SERVICES](docs/SDR_SERVICES.md)
  ([setup notes](docs/SDR_SERVICES_README.md)), [CIFP_DATA](docs/CIFP_DATA.md),
  [DCA_IAD_FIDS](docs/DCA_IAD_FIDS.md),
  [GPS_COORDINATE_CONFIGURATION](docs/GPS_COORDINATE_CONFIGURATION.md),
  [FLIGHT_INSTANCE_RESOLVER](docs/FLIGHT_INSTANCE_RESOLVER.md),
  [watchlist](src/shared/watchlist_README.md),
  [AIRLINE_TIME_FIDELITY_SPEC](docs/research/AIRLINE_TIME_FIDELITY_SPEC.md).
- **Alerts:** [ALERT_REFERENCE](docs/ALERT_REFERENCE.md),
  [ALERT_ARCHITECTURE](docs/ALERT_ARCHITECTURE.md).
- **Operations:** [BACKUP](docs/BACKUP.md), [RETIRED](docs/RETIRED.md),
  [OPERATOR_CONSOLE](docs/OPERATOR_CONSOLE.md),
  [HEADLESS_ACCESS](docs/HEADLESS_ACCESS.md) *(private;
  public: [example](docs/HEADLESS_ACCESS.example.md))*,
  [TAILNET_MIGRATION_INVENTORY](docs/TAILNET_MIGRATION_INVENTORY.md) *(private;
  public: [example](docs/TAILNET_MIGRATION_INVENTORY.example.md))*,
  [scheduled-task skills](docs/tasks/scheduled/README.md).
- **Runner, demo and integration:** [dispatch-runner-design](docs/dispatch-runner-design.md),
  [DISPATCH_PANEL_PARITY](docs/DISPATCH_PANEL_PARITY.md),
  [CLIENT_DEMO_PATTERN](docs/CLIENT_DEMO_PATTERN.md),
  [INTEGRATION_SPEC](docs/INTEGRATION_SPEC.md),
  [auth-token-proxy-pattern](docs/auth-token-proxy-pattern.md).
- **Agents and governance:** [AGENT_SEGMENTATION](docs/AGENT_SEGMENTATION.md),
  [AGENT_TRUST_MODEL](docs/AGENT_TRUST_MODEL.md),
  [BOARD_SIGNING](docs/BOARD_SIGNING.md),
  [TEAM_SEGMENTATION_SHOWCASE](docs/TEAM_SEGMENTATION_SHOWCASE.md),
  [SOVEREIGN_SKILL_SUPPLY_CHAIN](docs/SOVEREIGN_SKILL_SUPPLY_CHAIN.md),
  [AGENTS.md](AGENTS.md), [skills/](skills/).
- **Security and compliance:** [SECURITY.md](SECURITY.md),
  [COMPLIANCE_SECURITY](docs/COMPLIANCE_SECURITY.md),
  [GUARDRAILS_PROVEN](docs/GUARDRAILS_PROVEN.md),
  [GUARDRAILS_JUSTIFICATION](docs/GUARDRAILS_JUSTIFICATION.md),
  [REPRODUCIBLE_BUILDS](docs/REPRODUCIBLE_BUILDS.md),
  [SECRETS_NAMING_AND_ISOLATION](docs/SECRETS_NAMING_AND_ISOLATION.md),
  [CREDENTIALS_REGISTER](docs/CREDENTIALS_REGISTER.md) *(private)*,
  [SUDO_JUSTIFICATION_PROPOSAL](docs/SUDO_JUSTIFICATION_PROPOSAL.md),
  [LADD_CUI_HANDLING](docs/LADD_CUI_HANDLING.md),
  [HONEYPOT_FAIL2BAN](docs/HONEYPOT_FAIL2BAN.md),
  [HARDWARE_KEYS_DESIGN](docs/HARDWARE_KEYS_DESIGN.md),
  [GPG_KEYS_PUBLISHED](docs/GPG_KEYS_PUBLISHED.md),
  [ISO_42001_ALIGNMENT](docs/ISO_42001_ALIGNMENT.md),
  [regulated-operator-setup](docs/regulated-operator-setup.md),
  [security-reviews](docs/security-reviews/README.md).
- **Models and second brain:** [DEDICATED_MODELS_PLAN](docs/DEDICATED_MODELS_PLAN.md),
  [SECOND_BRAIN_STATUS](docs/SECOND_BRAIN_STATUS.md) *(private; public:
  [example](docs/SECOND_BRAIN_STATUS.example.md))*,
  [SECOND_BRAIN_GRAPH_AUTOMATION_PROMPT](docs/SECOND_BRAIN_GRAPH_AUTOMATION_PROMPT.md),
  [CAUSAL_REASONING_ROADMAP](docs/CAUSAL_REASONING_ROADMAP.md),
  [lmstudio-dispatch-prompts](docs/lmstudio-dispatch-prompts.md).
- **Business (internal):** [executive_summary](docs/executive_summary.md),
  [COST_STRUCTURE](docs/COST_STRUCTURE.md),
  [investor-materials](docs/investor-materials/).

**History (frozen dated records; never rewritten):** the parity audit
[DOCS_PARITY_AUDIT_2026-10-12](docs/DOCS_PARITY_AUDIT_2026-10-12.md), the
ntfy audit [NTFY_TOPIC_AUDIT_2026-10-13](docs/NTFY_TOPIC_AUDIT_2026-10-13.md),
the time-fidelity checkpoint
[AIRLINE_TIME_FIDELITY_CHECKPOINT_2026-10-13](docs/research/AIRLINE_TIME_FIDELITY_CHECKPOINT_2026-10-13.md),
[OOOI_SOURCE_AUTHORITY_2026-09-28](docs/OOOI_SOURCE_AUTHORITY_2026-09-28.md),
[CODESHARE_AND_FBO_2026-09-28](docs/CODESHARE_AND_FBO_2026-09-28.md),
[REST_FALLBACK_AUDIT_2026-09-06](docs/REST_FALLBACK_AUDIT_2026-09-06.md),
[MODEL_EVALUATION_2026-09-21](docs/MODEL_EVALUATION_2026-09-21.md),
[CODEBASE_REFERENCE_2026-10-06](docs/CODEBASE_REFERENCE_2026-10-06.md),
[COGS_BUILD_VS_RENT_2026-09-28](docs/COGS_BUILD_VS_RENT_2026-09-28.md), the
`docs/LIVE_STATE_CHECK_*` series, `docs/docs-refresh-2026-10-06/` and
`docs/security-reviews/`.

Public releases are GPG signed. The two commit-signing keys ship at the repo
root as `<fingerprint>.gpg`:

```
ABD3976FCC006E0F3FE559177286B3118BA4EFB2 — original default key
419A864CC29A09513039B6E03033FB4D01903159 — default since 2026-07-07
```

Further public keys (agent manifest signing, Pi agent signing, break-glass)
ship under `security/`. `SECURITY.md` at the repo root is the authority.

---

## Status

| Component | State (2026-10-06 18:25Z unless a row says otherwise) |
|---|---|
| Database | **Postgres** (`corporatetraveldc-pgsql`, `postgres:16-alpine`), `DISPATCH_DB_BACKEND=postgres` in `/etc/corporatetraveldc/dispatch.env`. Containers reach it over the unix socket in the shared `corporatetraveldc-pgsql-sock` volume mounted at `/var/run/postgresql`; the host reaches it on `127.0.0.1:5432`. Cutover 2026-09-18 (reference/chat/demo tables 09-19/09-20). ~~73~~ 75 (corrected 2026-10-15) additive migrations in `src/common/pg_schema/`. `REFERENCE_TABLES` in `src/common/db_backend.py` is an empty frozenset; `sqlite` survives only as a rollback backend selector. See `docs/POSTGRES_MIGRATION.md`. |
| Ops dashboard (runner) | Tailnet-only: `http://100.x.x.x:8001` or `https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net` (nginx 443 → :8001). `ops.example.com` was retired 2026-08-02 and is hard-404'd by `_RETIRED_HOSTNAMES` in `src/runner/main.py`. |
| Public demo (runner-demo) | Up: `https://dispatch-runner.example.com` returns 200, `:8005/healthz` 200. Quadlet sets `DEMO_MODE=true`; `DEMO_SESSION_SECRET` comes from `/etc/corporatetraveldc/demo-secrets.env`. **The data it replays is stale:** demo-api serves `/var/lib/corporatetraveldc-demo-source/demo-source.db`, last written 2026-08-14 14:21Z; the scrub+promote timer `corporatetraveldc-demo-source-refresh.timer` is `disabled` and has never fired. Re-checked 2026-10-15 00:55Z: all still true (`:8005/healthz` ok, public hostname 200, file unchanged since 2026-08-14 14:21:42Z, timer disabled). Added 2026-10-15: runner-demo and demo-api also mount the shared Postgres socket and load `dispatch.env`, because the demo password-gate profiles (`demo_profiles`, migration 0057) moved to Postgres on 2026-09-20. Playback data still comes only from `demo-source.db`. |
| Demo portals (added 2026-10-15) | `corporatetraveldc-demo-portal-client` (:8088) and `-personal` (:8087): `nginx:alpine` containers serving `/home/corporatetraveldc/demos` read-only, bound to 127.0.0.1 and the tailnet IP. Installed and active since 2026-10-12. They have no tunnel ingress (none in `cloudflared/config.yml`). Swapped with `scripts/demo-portal.sh`. Their Quadlet headers cite `docs/DEMO_PORTALS.md`, which does not exist; `docs/CLIENT_DEMO_PATTERN.md` describes the pattern. |
| Backups (added 2026-10-15) | `corporatetraveldc-backup.timer` (daily 03:40Z): an encrypted Borg archive, synced to the off-box Proton Drive target (live since 2026-10-12). LADD table data stays on the box. `backup-freshness` runs every 6 h and `backup-restore-test` Sun 05:30Z; root `agent-home-snapshot` runs 03:20Z. See `docs/BACKUP.md`. |
| Backup uplink (added 2026-10-15) | The cellular backup uplink has been down since 2026-10-06, so failover has no working second path. See `docs/NETWORK_FAILOVER.md`. |
| Web API | Public: `https://dispatch.example.com` (Cloudflare Access; nginx stamps `X-CTDI-Public: 1`, which pins every request to Tier 0 regardless of token). Path-scoped Access bypasses reach the app without login for at least `/robots.txt`, `/api/v1/board` and `/webhooks/*` (probed: 200, 200, 405-for-GET). Tailnet: `http://100.x.x.x:8000`. |
| Agent gateway (OAuth 2.1 + remote MCP) | Live at `https://agents.example.com` since 2026-10-05: `/.well-known/oauth-authorization-server` 200, other paths 404. Per-agent connector slugs at `/mcp/{slug}`: `chatgpt`, `claude-code`, `codex`, `cowork` (all active 2026-10-08; `POST /mcp/codex` without a token returns 401). Consent is the operator's SSH-signed approval. Every tool call is recorded in the audit chain as `agent.tool.call` (connector, account, tool, outcome, SHA-256 of the arguments, never their content; since 2026-10-08). Global kill: `scripts/agent-gateway.sh kill-all`. Code `src/web/routes/agent_gateway.py`, migrations 0071/0073. See `docs/AGENT_SEGMENTATION.md`. |
| Old MCP bridge (mcpo) | Retired 2026-08-18; nothing listens on 8082/8083. `mcp.example.com` returns 404 at the tunnel edge (ingress `http_status:404`). The nginx vhost `mcp.example.com.conf` is still present in the repo and live (local request returns 502). |
| Operator console | `/console` on the tailnet name only (nginx tailnet vhost → web :8000); the public dispatch vhost 404s it. Sign-in is an SSH-signed approval (kind `console-login`). See `docs/OPERATOR_CONSOLE.md`. |
| Executive Standard members edition | `members.executivestandard.example.com` (gated by `auth_request` → `corporatetraveldc-execstandard-verifier` on 127.0.0.1:8787) and `invite.executivestandard.example.com` (invite host; returns 200). Logic in `src/common/es_invites.py`, migration 0072. Their nginx vhosts live in the `executivestandard-website` repo. |
| FAA SWIM push feeds | All six (FDPS, STDDS, TFMS, TBFM, ITWS, AIM/FNS) connected: every `push:*` heartbeat was 15–29 s old at verification (2026-10-15 00:55Z: 5–31 s). Load-shed by `scripts/thermal-ingest-guard.py` under pressure (see below), so "connected" is not "always running". |
| Local LLM | One user unit, `corporatetraveldc-llama.service`: `llama-server` bound to `100.x.x.x:8093`, model `qwen3-4b-instruct-2507-q4_0.gguf`, `-np 2 --kv-unified -c 12288`, `-t 2 -tb 2`, `CPUQuota=200%`. Ollama is gone (nothing on :11434). Added 2026-10-15: `corporatetraveldc-claude-weekly-llama-fallback` (Quadlet + timer, 00/06/12/18Z) is meant to use this server to stand in for the weekly Claude dump when that run fails. Its model name has no persona mapping, so it cannot generate as configured (see "Local LLM"). |
| ADS-B / ACARS / VDL2 receive | Up: ultrafeeder, acarsrouter, acarshub, dumpvdl2, acars-watcher and four aggregator feeders all running. |
| Signed-manifest integrity sweep | ~~2026-10-08 12:10Z: **failing** on one unsigned staged file (this batch); clears at the next sign. The sweep now also checks build provenance of running local images: 2 verified, 16 unverified (built one commit before HEAD), 0 failed. Unverified clears when the images are rebuilt from signed HEAD.~~ (corrected 2026-10-15 00:50Z: passing: "signature valid, all 1442 files match"; build provenance 18 verified, 0 unverified, 0 failed. The sweep fails while a batch's edited files are unsigned and clears at the next sign. Known verifier limits are in `SECURITY.md`.) |
| Team accounts (`/etc/ctdc-accounts.conf`) | 2026-10-08: agents `ctdc-agent-anthropic-claude` (login mode claude) and `ctdc-agent-openai-codex` (codex); services `ctdc-agent-anthropic-cowork`, `ctdc-agent-openai-chatgpt`, `ctdc-agent-llama`, `ctdc-agent-dispatch` (preloaded). Liveness is an AND of login, signer key, no token revocation and no kill order (`scripts/team-liveness.sh`). See `docs/AGENT_SEGMENTATION.md`, `docs/AGENT_TRUST_MODEL.md`. |
| Build integrity | Every local image: hash-locked Python (`requirements*.in` → `requirements*.txt`), base images by digest, apt from a dated snapshot, `--platform` pinned to the host, SBOM + provenance receipt per build. Levels 1–3 of `docs/REPRODUCIBLE_BUILDS.md`; bit-for-bit (Level 4) not claimed. |

---

## Architecture

web, poller, pusher, the seven ingest containers, runner (chat history only),
demo recorder/API and the execstandard verifier share one Postgres database
(`corporatetraveldc-pgsql`). The backend is chosen by `DISPATCH_DB_BACKEND`;
`postgres` is live. The runner otherwise owns its own JSON state
(`user_rss_feeds.json`, UI config).

```
┌────────────────────────────────────────────────────────────────────┐
│                    deployment user (corporatetraveldc)             │
│                                                                    │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌──────────────┐    │
│  │   web     │  │  poller   │  │  pusher   │  │ ingest ×7    │    │
│  │ FastAPI   │  │ fetchers +│  │  ntfy     │  │ SWIM ×6 +    │    │
│  │ :8000     │  │ skills    │  │  sender   │  │ core (NWWS,  │    │
│  │           │  │           │  │           │  │ local RF)    │    │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └──────┬───────┘    │
│        └──────────────┴──────┬───────┴───────────────┘            │
│                   Postgres (corporatetraveldc-pgsql)               │
│                                                                    │
│  runner (:8001, tailnet-only ops dashboard, proxies web :8000)     │
│  runner-demo (:8005) → demo-api (:8004) → frozen demo-source.db    │
│  amtrak-tracker (Amtrak push) · SDR stack · ntfy · Nextcloud ·     │
│  Open WebUI · 38 timer-driven oneshot skill containers             │
│                                                                    │
│  host: llama-server :8093 · nginx 80/443 · cloudflared (user unit) │
│        tailscaled · Pi-hole + Unbound · chronyd (NTS :4460)        │
└────────────────────────────────────────────────────────────────────┘
```

### Core containers

| Container | Image | Role |
|---|---|---|
| `systemd-corporatetraveldc-web` | `localhost/corporatetraveldc-web:latest` | FastAPI API, published on 127.0.0.1:8000 and 100.x.x.x:8000; single uvicorn worker |
| `systemd-corporatetraveldc-poller` | `localhost/corporatetraveldc-poller:latest` | Async scheduler: 12 REST fetchers (`FETCH_SCHEDULE`), 10 in-process skills (`SKILL_SCHEDULE`), watchlist sweeps |
| `systemd-corporatetraveldc-pusher` | `localhost/corporatetraveldc-pusher:latest` | ntfy sender; polls for unnotified events every 30 s |
| `systemd-corporatetraveldc-ingest-{core,fdps,stdds,tfms,tbfm,itws,notam}` | `localhost/corporatetraveldc-ingest:latest` (one image, 7 Quadlets) | One container per SWIM feed plus `core` (NWWS-OI + local airspace). `notam` runs the AIM/FNS SWIM feed. `AMTRAK_ENABLED=false` in every ingest Quadlet since 2026-09-24, so `ingest-core` no longer writes Amtrak. See `src/ingest/README.md`, `scripts/ingest-feed-ctl.sh`. |
| `corporatetraveldc-pgsql` | `docker.io/library/postgres:16-alpine` | The live database |
| `systemd-amtrak-tracker` | `localhost/corporatetraveldc-amtrak-tracker:latest` | Amtrak push writer (api.amtraker.com), stamps `push:amtrak` |
| `systemd-corporatetraveldc-runner` | `localhost/corporatetraveldc-runner:latest` | Ops dashboard SPA + API, port 8001, tailnet-only |
| `systemd-corporatetraveldc-runner-demo` | same runner image | Demo playback, host port 8005 → container 8001, `DISPATCH_BASE_URL=http://100.x.x.x:8004` |
| `systemd-corporatetraveldc-demo` / `systemd-corporatetraveldc-demo-api` | `localhost/corporatetraveldc-demo:latest` | Recorder (writes `demo_snapshots` in Postgres) / read-only playback API on :8004 over the scrubbed `demo-source.db` |
| `systemd-corporatetraveldc-execstandard-verifier` | poller image | `auth_request` verifier for the members site, 127.0.0.1:8787 |

### Auxiliary containers (same host)

SDR/RF: `corporatetraveldc-ultrafeeder` (ADS-B + tar1090; 100.x.x.x:8080
and :8081, Beast out on 0.0.0.0:30005, SBS on 127.0.0.1:30003),
`corporatetraveldc-acarsrouter` (0.0.0.0:9080 and :15555),
`corporatetraveldc-acarshub` (100.x.x.x:8092), `corporatetraveldc-dumpvdl2`,
`corporatetraveldc-acars-watcher` (UDP 5005), and the feeders
`corporatetraveldc-piaware`, `corporatetraveldc-fr24feed` (:8754),
`corporatetraveldc-planefinder` (127.0.0.1:30053),
`corporatetraveldc-airnavradar`. Disabled pending hardware, as
`*.container.disabled` files that create no unit: `acarsdec`
(`.config/containers/systemd/`), `dumphfdl`, `ais`, `ais-watcher`,
`utm-watcher` (`systemd/`). See `docs/SDR_SERVICES.md`.

Infra/comms: `ntfy` (0.0.0.0:2586), `systemd-corporatetraveldc-protonbridge`
(SMTP relay, 100.x.x.x:1025 → container port 25), `nextcloud-app`
(127.0.0.1:8090) + `nextcloud-db` (Postgres 16), `openwebui` (0.0.0.0:3000),
`rss-bridge` (100.x.x.x:3001), `csexec-contact` (website contact API,
127.0.0.1:8002), and two client-preview nginx containers,
`corporatetraveldc-ccw-demo` (:8085) and `corporatetraveldc-ccw-preview1`
(:8086), both on 127.0.0.1 and the tailnet IP.

### Data feeds

The 20 rows `/api/v1/feeds` returned at verification, with the poller interval
from `FETCH_SCHEDULE` (`src/poller/main.py`):

| Feed | Source | Interval | State at verification |
|---|---|---|---|
| `metar` | aviationweather.gov `/api/data/metar`, 7 stations (KDCA KIAD KBWI KFDK KHEF KJYO KGAI); since 2026-10-09 read from the deployment profile (airports with role `metar`), with that list as the fallback | 300 s | fresh |
| `nws` | api.weather.gov `/alerts/active` | 300 s | REST skipped while `push:nws` is fresh (push-covered) |
| `atcscc_opsplan` | ATCSCC | 3600 s | fresh |
| `runsheet` | local file | 300 s | fresh |
| `tfr` | `tfr.faa.gov/tfrapi/getTfrList` | 300 s | fresh; no push twin exists for TFRs |
| `nas` | `nasstatus.faa.gov/api/airport-status-information` | 300 s | fresh |
| `notam` | FAA NMS API (`api-nms.aim.faa.gov`); needs `NMS_API_CLIENT_ID` + `NMS_API_CLIENT_SECRET`, else `awaiting_credentials` | 300 s | REST skipped while `push:fns` is fresh |
| `dca_fids` / `iad_fids` | MWAA JSON endpoints | 300 s | fresh (600 s staleness threshold) |
| `amtrak` | api.amtraker.com (REST fallback for `push:amtrak`, wired 2026-09-06) | 300 s | REST row 7.4 h old because push owns the data |
| `eurocontrol` | EUROCONTROL NM B2B | 900 s | `awaiting_credentials` |
| `jasdat` | JCAB/MLIT | 900 s | `awaiting_credentials` |
| `push:fdps` `push:stdds` `push:tfms` `push:tbfm` `push:itws` `push:fns` | FAA SWIM NMS (Solace), ingest containers | push | heartbeats 15–29 s old |
| `push:nws` | NWWS-OI (XMPP), ingest-core | push | 18 s old |
| `push:amtrak` | amtrak-tracker container | push | 41 s old |

Only `nws`, `notam` and `amtrak` have a push twin (`REST_FALLBACKS` in
`src/ingest/failover.py`). FDPS, STDDS, TFMS, TBFM and ITWS have no REST
fallback (operator decision 2026-09-06, `docs/REST_FALLBACK_AUDIT_2026-09-06.md`).

### Push/pull failover

SWIM and NWWS ingest stamp `push:<feed>` every 30 s
(`INGEST_HEARTBEAT_INTERVAL_SECS`, default 30). Before a REST poll that has a
push twin, the poller skips the fetch if the heartbeat is younger than
`FALLBACK_MAX_AGE` = 90 s (`src/ingest/failover.py`); Amtrak overrides it to
660 s because its writer stamps once per 300 s poll. When ingest disconnects,
the heartbeat ages out and REST polling resumes.

### SWIM feed liveness and thermal load-shedding

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

| Trip | Condition | What is shed |
|---|---|---|
| **LOCKDOWN** | `load1 >= 40.0` (`THERMAL_GUARD_LOAD_LOCKDOWN`) | everything except `web`: all six SWIM feeds, `ingest-core`, `poller`, `pusher`, `runner` (`LOCKDOWN_USER_UNITS`, fixed in code) |
| Informational only | load1 15–40, CPU above 75 °C, or any count of load-attributed LLM fallbacks | nothing (temperature never sheds) |
| Restore | after a 300 s dwell, held only while `load1 > 30` (`THERMAL_GUARD_HOLD_LOAD`) or the CPU is above 75 °C (`THERMAL_GUARD_HOLD_TEMP_C`). Once the CPU has gone above 75 °C, the temperature hold clears below 74 °C (1.0 °C hysteresis). | the stack, in boot order |
| Dormancy | within 600 s of any restore, observe only, unless the CPU is at or above 79 °C (then the pass is evaluated; temperature still sheds nothing) | — |

The rollout's own hold-before-restart gate (`scripts/lib/load_gate.sh`, floor 15) is separate.

The guard never stops or starts the llama unit. A shed unit exits 0, so it
does not show in failure greps; restarting it by hand gets it shed again on
the next pass. The authoritative state:

```bash
cat /var/lib/corporatetraveldc/thermal_ingest_guard_state.json   # tier 0 at verification (also 2026-10-15 00:55Z)
journalctl --user -u corporatetraveldc-thermal-ingest-guard --since "24 hours ago"
```

"Load-attributed LLM fallback" events are written by `_record_load_fallback()`
in `src/common/llm.py` to `/var/lib/corporatetraveldc/llm_load_fallback_events.jsonl`
for a busy slot (`OllamaBusyError`, a legacy class name) or a generation
timeout, never for a connection error.

---

## API

| Endpoint | URL | Notes |
|---|---|---|
| Ops dashboard (runner) | `http://100.x.x.x:8001` / `https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net` | Tailnet only |
| API (public) | `https://dispatch.example.com` | Cloudflare Access; always Tier 0 |
| API (tailnet) | `http://100.x.x.x:8000` | Bearer-token tier resolution |
| Public demo | `https://dispatch-runner.example.com` | Password-gated (`DEMO_MODE=true`); replays the 2026-08-14 snapshot |
| Agent gateway | `https://agents.example.com` | OAuth 2.1 + remote MCP, per-agent slugs |

### Route inventory

~~126 route decorators (re-counted 2026-10-08): 82 in `src/web/main.py` (61 GET, 16 POST, 4 DELETE,
1 PATCH)~~ (corrected 2026-10-15: 128 route decorators: 83 in `src/web/main.py` (61 GET, 17 POST, 4 DELETE,
1 PATCH)) plus `routes/agent_gateway.py` 10, `watchlist.py` 10, `sectors.py` 8,
`console.py` ~~6~~ 7, `fids.py` 3, `webhooks.py` 3, `airspace.py` 2,
`data_usage.py` 1, `remember.py` 1. Gating by dependency: 15 `require_tier`
(14 Tier 1, 1 Tier 2), 37 `require_admin(<action>)`
dependencies on 37 handlers (`/admin/push-alert` and its alias
`/admin/push-test-alert` share one handler); the rest are anonymous
at the dependency layer, and several of those carry their own credential
check in the handler (board key/signature, signed approvals, OAuth bearer,
console session, webhook secret).

### Tier 0 — anonymous (selection)

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Health + snapshot age + active token count + CPS |
| GET | `/api/v1/feeds` | Feed freshness + error state (20 feeds) |
| GET | `/api/v1/cps` | Critical Predictability State |
| GET | `/api/v1/tfr` | Active TFRs (87 at verification) |
| GET | `/api/v1/weather` | METAR snapshot (`{metars:[…]}`) |
| GET | `/api/v1/alerts` | Active NWS alerts |
| GET | `/api/v1/wx/discussion[/{awips_id}]` | WPC forecast discussions |
| GET | `/api/v1/airmets` | AIRMET/SIGMET polygons |
| GET | `/api/v1/notams` | Active NOTAMs |
| GET | `/api/v1/amtrak` | Amtrak DC-area status |
| GET | `/api/v1/opsplan` | ATCSCC daily ops plan |
| GET | `/api/v1/brief` · `/brief/history` · `/brief/weekly` · `/brief/{ref}` | Brief texts (`/api/v1/brief` returns `text/plain`) |
| GET | `/api/v1/route` | Ground route impact narrative |
| GET | `/api/v1/events` | SSE event stream |
| GET | `/api/v1/train-config` · `/api/v1/wx-config` | Operator rail / meteorology config |
| GET | `/api/v1/flightplan/{callsign}` | FDPS flight plan |
| GET | `/api/v1/fids/{airport}` · `/{airport}/arrivals` · `/{airport}/{flight}` | DCA/IAD FIDS |
| GET | `/api/v1/adsb` | Local-receiver ADS-B snapshot |
| GET | `/api/v1/aircraft/{identifier}` · `/api/v1/aircraft-registry/status` | FAA/OpenSky registry lookup |
| GET | `/api/v1/airspace[/{feature_id}]` | Static DC airspace features |
| GET | `/api/v1/demo/readiness` | Demo archive seed status |
| GET | `/api/v1/data-usage` · `/api/v1/feeds/usage` · `/api/v1/bandwidth-priority` | Data usage / bandwidth priority |
| GET | `/api/v1/board*` | Coordination board reads; posting needs `X-Board-Key` or an SSH board signature (`docs/BOARD_SIGNING.md`) |
| GET | `/api/v1/sectors*` | Sector/family alert topic state (mutations are admin) |
| GET | `/api/v1/vault/research` · `/research/list` | Research vault reads (arena filter applies) |
| GET/POST | `/api/v1/approvals*`, `/api/v1/council*`, `POST /api/v1/workspace/contribute` | Signed approvals, council/arena, workspace contributions; each verifies an SSH signature in the handler (`src/common/governance.py`, migration 0069) |

### Tier 1 — `cert` bearer token

`/api/v1/runsheet`, `/api/v1/opsplan/range`, `/api/v1/radio`,
`/api/v1/tfr-enriched`, `/api/v1/osint/feed`, `GET /api/v1/osint/scopes`,
`/api/v1/knowledge-graph/{html,meta}`, `/api/v1/vault/file`, watchlist
sessions (`GET/POST /api/v1/watchlist`, `DELETE /api/v1/watchlist/{session_id}`),
and the watchlist entry reads in `routes/watchlist.py` (`GET /api/v1/watchlist`
entries, `/history`).

### Tier 2 — `shares` bearer token

`GET /api/v1/cui/status` (audit-logged).

### Admin — `admin` bearer token

`/admin/{healthz,feeds,audit,tokens,version,triggers,watchdog/status}`,
`POST /admin/refresh-feed/{feed_name}`, `POST /admin/force-recompute-cps`,
`POST /admin/force-opsplan-snapshot`, `POST /admin/force-osint-scrape`,
`POST /admin/push-alert` (alias `/admin/push-test-alert`),
`GET/POST/DELETE /admin/vip`, `POST/DELETE /admin/bandwidth-priority`,
`/admin/approval-requests` (create/list/get), OSINT scope mutations
(`POST/PATCH/DELETE /api/v1/osint/scopes`), sector silence/throttle/enable/
sanitize posts, watchlist entry mutations
(~~`POST /api/v1/watchlist/{flights,trains,vessels}[/batch]`~~ (corrected 2026-10-15:
`POST /api/v1/watchlist/{flights,trains,vessels}` and `POST /api/v1/watchlist/{flights,trains}/batch`;
there is no vessels batch route, `src/web/routes/watchlist.py`),
`POST /api/v1/watchlist/permanent/batch`, `DELETE /api/v1/watchlist/{entry_id}`,
`DELETE /api/v1/watchlist/batch`) and `POST /api/v1/remember`.
`require_admin(action)` audits every allowed and denied call, and a token may
carry an `allowed_actions` scope list (migration 0070) outside which it is
denied.

### Inbound webhooks — `X-Webhook-Secret`

Each returns 503 until its `*_WEBHOOK_SECRET` (`LIMOANYWHERE_`, `RINGCENTRAL_`,
`THREECX_`) is configured, 401 on a wrong secret. See `src/web/routes/webhooks.py`
and `docs/INTEGRATION_SPEC.md`.

| Method | Path | Source |
|---|---|---|
| POST | `/webhooks/limoanywhere/reservations` | LimoAnywhere (auto-adds the extracted flight or train to the watchlist) |
| POST | `/webhooks/ringcentral/events` | RingCentral (echoes `Validation-Token`) |
| POST | `/webhooks/3cx/events` | 3CX |

### Runner API (port 8001, tailnet-only)

30 route decorators in `src/runner/main.py` (32 method+path pairs; the
dispatch proxy takes GET/POST/DELETE). Sensitive surfaces (admin proxy,
non-GET `/api/v1/*` proxy, chat history, `PUT /api/v1/config`) are gated by
`_is_trusted()` (Tailscale CGNAT 100.64.0.0/10, RFC1918, loopback;
`CF-Connecting-IP` used exclusively when present). In demo mode an untrusted
origin also needs the `ctdc_demo_session` cookie.

Routes: `/healthz`, `/api/whoami`, `/api/demo/{login,status,webhook-log}`,
`/api/adsb/{local,live}` (both local receiver only), `/api/{vdl2,acars,hfdl}/messages`,
`/api/ais/vessels`, `/api/utm/drones`, `POST /api/ask` + `GET/DELETE /api/chat/history`
(Dispatch Drawer chat via llama-server), `/api/dispatch/{path}` (proxy to
:8000 with Tier-1 token injection for an allowlist, see
`docs/auth-token-proxy-pattern.md`), `/api/stream` (SSE),
`/api/ntfy/stream`, `GET/PUT /api/v1/config`, `/api/v1/frontend-config`,
the RSS engine (`/api/rss`, `/api/rss/categories` GET/POST, `/api/rss/custom`,
`/api/rss/resolve-source`, `/api/rss/user-feeds` GET/POST,
`DELETE /api/rss/user-feeds/{feed_id}`), and the SPA catch-all.

RSS catalog: `src/shared/rss_catalog.py`, shared with the second-brain RSS
poller. User feeds persist in `/var/lib/corporatetraveldc/user_rss_feeds.json`,
custom categories in `user_rss_categories.json`. Built-in catalog
(`_RSS_CATALOG`, static count): 11 categories, 32 feeds — `corporate_intel`,
`marketing_intel`, `travel_trends`, `dc_area`, `aviation`,
`advanced_air_mobility`, `gig_economy`, `concierge_luxury_travel`,
`trains_yachts`, `executive_protection`, `osint_cybersecurity_video`.

### Auth model

Tokens are created with `ctdc-token` (`src/ctdc_token/cli.py`). Format
`ctdc_<user>_<32 random chars>`; only the SHA-256 hash is stored.

```
Tier 0 → anonymous, and ANY request carrying X-CTDI-Public: 1 (stamped by
         the public nginx vhost) regardless of token
Tier 1 → bearer token tier=cert
Tier 2 → bearer token tier=shares (audit-logged)
Admin  → bearer token tier=admin, optionally scoped by allowed_actions
```

Network origin grants no tier; the old `Tailscale-User-Login`/XFF trust was
removed as spoofable (`src/auth/auth.py`).

**One `/admin/*` route has no bearer dependency:**
`GET /admin/approval-requests/{request_id}/resolve` (`src/web/main.py:2821`; was ~~`:2804`~~, line corrected 2026-10-15).
It requires the per-action key `k` carried only in the operator's push
(wrong or missing key = 403), and since 2026-10-04 it can only **deny**:
`action=allow` always returns 403. Allowing takes a human SSH signature over
the exact request (`POST /api/v1/approvals/{id}/resolve`, `scripts/approve.sh`).

---

## Watchlist system

Two tiers share one pipeline (`src/shared/watchlist.py`,
`src/shared/watchlist_README.md`):

**Permanent** — JSON files in `/opt/corporatetraveldc/watchlists/`
(`permanent_flights.json`, `permanent_trains.json`, `permanent_vessels.json`,
`permanent_drones.json`), re-read by `WatchlistFileWatcher` on a 60 s mtime poll.

**Transient** — added via `POST /api/v1/watchlist/{flights,trains,vessels}`
(admin token). Expire at `auto_remove_at`; when omitted, 6 h after scheduled
arrival for flights and vessels, 3 h for trains, else 24 h after the add.
The poller sweeps expiries every 60 s, flights every 120 s, trains and vessels
every 300 s (vessels need `AIS_AISHUB_ID`).

Four entry types: flight (callsign; marketed codeshare numbers resolve to the
operating flight via `db.resolve_operating_callsign`), train (Amtrak number),
vessel (MMSI) and drone (separate `uas_phase` columns, not OOOI). Events fire
dual ntfy pushes (domain topic + concise `dispatch`) with forward-only
content-hash dedup. ~~Flight position and identity resolution is local: own
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
are at least 1.1 s apart, with a 30 s cache. FlightAware AeroAPI code is
dormant without a key; whether a key is configured was not checked in this
pass. Since 2026-10-14 watch matching goes by flight instance (the leg), not
by flight number: `docs/FLIGHT_INSTANCE_RESOLVER.md`.) (The runner's
GLOBE map view still embeds `globe.airplanes.live` in the browser.)

OOOI phases `pre_departure → out → off → on → in` never revert. Same-phase
ties resolve by `_OOOI_SOURCE_PRIORITY` in `src/common/db.py`: ACARS (4) >
SMES (3) > TFMS / TFMS-airline (2) > TBFM (1) > ADS-B (0) > FIDS (−1).
(Added 2026-10-15: the FIDS entry only ranks a tie. The authority gate denies
every FIDS OOOI claim, "enrichment-only, never OOOI-authoritative",
`src/common/db.py:6167`.)
Separate authority rules (2026-10-05): ADS-B never asserts ON or IN, and
airline-posted TFMS times are denied for OUT/OFF at an SMES origin and ON/IN
at an SMES destination (KDCA/KIAD/KBWI).

---

## ntfy topics (core set)

| Topic | Content | Priority |
|---|---|---|
| `tfr-alert` / `hot-alerts` | ~~VIP/POTUS TFR, Marine One/AF1, severe-ops events~~ (corrected 2026-10-15: each VIP TFR (FAA type VIP, any region) alerts once on both topics, with Pushover co-fire. The alert carries its own FAA text and active window (UTC + ET; the window comes from the FAA detail record, next relay) and fires again only if the FAA changes the TFR. A DC-area VIP set also gets route impact's ground-route hot alert, once per set. CRANE sightings are no longer labelled Marine One. Severe-ops events also use this tier. See `docs/ALERT_REFERENCE.md`.) | 5 |
| `flight-alerts` / `train-alerts` / `vessel-alerts` / `drone-alerts` (added 2026-10-16: drone events used to fall through to `train-alerts`) | Watchlist events per domain | 2–5 |
| `dispatch` | Concise bottom line for all events | mirrors source |
| `dispatch-debriefs` / `dispatch-ops` | Full debrief tables / weekly aggregate | 2–3 |
| `cps` | CPS score changes | 3–5 |
| `wx-alerts` | NWS + ITWS hazardous weather | 3–4 |
| `nas-alerts` | NAS program/restriction/NOTAM alerts | 2–5 |
| `<family>-alerts` + `<family>-<zone>` (zones zny/zdc/zid/zob/zatl/zhu/zla/zse) | Escalating TFMS/TBFM/FDPS/ITWS/AIM-FNS sector alerts, per-topic throttled | 2–4 |
| `ops-brief` / `ep` / `ep-advance` | Hourly briefs | 2–4 |
| `ops-health` | Freshness audit, watchdogs, thermal guard | 2–5 |
| `osint-alerts` | OSINT scope hits | 2–3 |
| `reservations` / `calls` | Inbound webhook events | 3 |
| `approval-gate` | Approval notices (sudo gate, token gate, council): deny link only; allowing is a signed `scripts/approve.sh` | 4 |

Full catalog: `docs/ALERT_REFERENCE.md`; rationale: `docs/ALERT_ARCHITECTURE.md`.

---

## CPS — Critical Predictability State

Part 135.609-informed go/no-go score from ceiling, visibility, wind,
precipitation (METAR), airspace (TFRs + static restricted areas) and GDP (NAS
programs). Output `GREEN/GO`, `YELLOW/MARGINAL`, `RED/NO-GO`. Computed by
`src/poller/skills/cps_recompute.py` every 3600 s from the poller's
`SKILL_SCHEDULE`, and on demand via `POST /admin/force-recompute-cps`.

Added 2026-10-15: the inputs also include SWIM.

- **STDDS RVR** (`stdds_rvr`, from the APDS `RVRDataUpdateMessage`) is used
  in place of METAR visibility when a recent touchdown reading exists
  (≤ 900 s). The skill takes the worst touchdown reading, preferring the
  runways in the ITWS active runway configuration, and converts it with the
  14 CFR 91.175(h) RVR/visibility table transcribed in the file
  (`_RVR_TO_VIS_SM`). A measuring sensor converts to at most 1¼ SM, so a
  measuring RVR reading always scores a visibility violation. A 6000+
  reading or an offline sensor defers to METAR. The source is recorded in
  the score narrative.
- **ITWS** wind shear, microburst and gust-front products degrade the wind
  check.

**91.175(h) sign-off status: not recorded.** The code comment
(`cps_recompute.py`, the RVR table block) says the operator must check the
seven table pairs against the CFR "before the sign-off that lets this
influence live scores". No doc records that sign-off. The code has no runtime
gate, so the substitution is already live in scoring. Operator item: sign off,
or gate it.

---

## Demo mode

- `corporatetraveldc-demo` (recorder) polls the live API every
  `DEMO_RECORDER_INTERVAL` (default 300 s) and writes compressed snapshots to
  the Postgres `demo_snapshots` table (`src/demo/db.py`, migration 0057) —
  not to `demo.db` any more (that file was last written 2026-09-21).
  Defaults `DEMO_RECORDER_RETENTION=364`, `DEMO_RECORDER_SEED_TARGET=14` are
  `os.environ.get` defaults in `src/demo/recorder.py:45-48`.
- `corporatetraveldc-demo-api` (:8004) serves only the scrubbed file
  `/var/lib/corporatetraveldc-demo-source/demo-source.db` (read-only mount),
  populated only by the host-side `scripts/scrub-demo-source.py` scrub+promote
  pass. That pass's timer, `corporatetraveldc-demo-source-refresh.timer`, is
  disabled and has never run, so the file is still the 2026-08-14 14:21Z
  promotion (1.8 GB). `/healthz` reports `loop_days: 14`.
- `corporatetraveldc-runner-demo` (:8005) reads demo-api. With
  `DEMO_MODE=true` it is password-gated (`POST /api/demo/login`, HMAC-signed
  `ctdc_demo_session` cookie, `src/demo/profiles.py`, 8 h default) with
  signals sanitized server-side. (Added 2026-10-15: the session profiles live
  in Postgres (`demo_profiles`, migration 0057, since 2026-09-20), so both
  runner-demo and demo-api mount the `corporatetraveldc-pgsql-sock` volume
  and load `dispatch.env`. Neither mounts the live state tree
  `/var/lib/corporatetraveldc`.)
- Demo portals (added 2026-10-15): `corporatetraveldc-demo-portal-client`
  (:8088) and `-personal` (:8087) serve static client-preview sites from
  `/home/corporatetraveldc/demos`, on 127.0.0.1 and the tailnet only, with no
  tunnel ingress. They are active since 2026-10-12. See
  `docs/CLIENT_DEMO_PATTERN.md`; the `docs/DEMO_PORTALS.md` their Quadlets
  cite does not exist.
- Public hostname `https://dispatch-runner.example.com`; no
  Cloudflare Access policy fronts it. Its nginx vhost exists only on the host
  (not tracked in any repo).

`GET /api/v1/demo/readiness` reports per-tier (2w/8w/12w/24w/36w/52w) archive
readiness.

---

## Supported platforms

> Detail in **[docs/platform-compatibility.pdf](docs/platform-compatibility.pdf)**.
> [UNVERIFIED: only the Linux aarch64 reference deployment was inspected in
> this pass; the other rows restate the install scripts' targets.]

| Platform | Server stack | Install script |
|---|---|---|
| Linux x86_64 / ARM64 (Pi 5 reference) | Full | `install/install.sh` |
| macOS | Full | `install/install.sh` |
| Windows x64 | via WSL2 | `install/install-windows.ps1` |
| Android ARM64 (Termux) | bare Python | `install/install-android.sh` |
| iOS / iPadOS | web client only | — |

The Solace SWIM client library is Linux-only; other platforms run the
REST-fallback feed set.

---

## Installation

### Prerequisites

- Linux host (reference: Fedora 44 aarch64 on a Pi 5, SELinux enforcing,
  kernel 6.18, 16 GB RAM, 4 cores)
- Rootless Podman with a systemd user session (linger enabled)
- llama.cpp `llama-server` on the host (reference binary path
  `/usr/local/lib/ollama/llama-server`, a leftover install location) and a
  GGUF under `/var/lib/corporatetraveldc/models/`

### First-time setup

```bash
git clone <this-repo> /opt/corporatetraveldc/private/ctdi-dispatch-internal
cd /opt/corporatetraveldc/private/ctdi-dispatch-internal

# Non-secret config and the master secrets file
cp config/dispatch.env.example /etc/corporatetraveldc/dispatch.env
cp dispatch-secrets.env.template /etc/corporatetraveldc/dispatch-secrets.env
chmod 0600 /etc/corporatetraveldc/dispatch-secrets.env
# populate credentials and set ULTRAFEEDER_LAT / ULTRAFEEDER_LON (see below)

# Per-service scoped secrets: first-party containers do NOT read
# dispatch-secrets.env. Generate /etc/corporatetraveldc/svc/<svc>.env from it
# with the reviewed allowlists in scripts/service-env/ (generate.py --write).

# Sign the tree, then build (images bake MANIFEST.sha256 -- order is
# sign -> build -> restart)
scripts/sign-manifest.sh
bash build-images.sh

# Model server (one unit)
cp .config/systemd/user/corporatetraveldc-llama.service ~/.config/systemd/user/
cp .config/systemd/user/corporatetraveldc-llama-restart.{service,timer} ~/.config/systemd/user/

# Quadlets
cp .config/containers/systemd/*.container ~/.config/containers/systemd/
systemctl --user daemon-reload

# Database schema
scripts/pg-migrate.sh

# Start the core stack (production boots through
# corporatetraveldc-stack-boot-stagger, which brings up the 32 units in
# scripts/stack-boot-ctl.sh ORDER one at a time)
systemctl --user start corporatetraveldc-pgsql corporatetraveldc-web corporatetraveldc-poller corporatetraveldc-pusher

curl http://127.0.0.1:8000/healthz

PYTHONPATH=src python3 src/ctdc_token/cli.py create \
  --user operator --tier admin --label admin-phone
```

Root-executed scripts (team liveness, watchdog, cert renew, fail2ban actions;
added 2026-10-15 from `SCRIPTS=` in the installer: skill grants, stall
monitor and watchdog tune, NTS cert refresh, lockdown / restore-network, the
signed-sudo executor `approved-exec.py`, the agent-home snapshot and the
transcript leak scan)
run from root-owned installed copies under `/usr/local/libexec/ctdc/`,
installed by `scripts/install-root-copies.sh` after a manifest check, never
from the checkout.

### After any code change

```bash
scripts/sign-manifest.sh            # operator
bash build-images.sh [target]       # tags the outgoing image :previous
systemctl --user restart <units>    # or scripts/serialized-rollout.sh
```

(corrected 2026-10-15: the `systemctl --user restart <units>` line describes
a locals-only restart, which the standing rule forbids. Every rollout is a
**full load**: `bash scripts/serialized-rollout.sh` rebuilds the local images,
pulls every external image (an update check) and restarts every container
one at a time, externals included (`RESTART_EXTERNALS=1` is the default). It
takes the heavy-job turn (`full-load rollout`, up to 2 h wait) and gates each
restart on load. Batches are staged in the guard worktree while main stays
frozen; see `docs/GUARDRAILS_PROVEN.md`, "Updates without downtime or drift"
(2026-10-14).)

Never edit tracked files while a build, rollout or stack refresh is running.

Every `podman build` passes `--platform linux/<host arch>` and is followed by
`scripts/build/provenance.py record`, which refuses an image built for another
architecture. `scripts/build/build-policy.py` enforces both statically. Python
dependencies change only by editing a `*.in` intent file and re-locking with
`scripts/build/lock-python.py`. Detail and limits: `docs/REPRODUCIBLE_BUILDS.md`.

Image updates: `scripts/stack-refresh.sh --weekly` (timer
`corporatetraveldc-weekly-external-image-update.timer`, Sun 04:15 ET / 08:15Z) pulls
every external image, rebuilds every local image from signed HEAD, gates each
image, and restarts every container one at a time. A tripwire variant
(`corporatetraveldc-stack-refresh-tripwire.timer`, hourly with a random
delay) fires on a randomly drawn instant 36 h to 7 d after the last run,
audits "is what is running what was signed?" first, and holds everything on
any finding. Rollback is `podman tag <svc>:previous <svc>:latest` + restart;
`corporatetraveldc-podman-prune.timer` (07:15 ET / 11:15Z) prunes dangling images only.

**If you run a real ADS-B receiver, set your antenna's real coordinates**
(`ULTRAFEEDER_LAT`/`ULTRAFEEDER_LON`, plus the UltraFeeder Quadlet's
`READSB_LAT`/`READSB_LON`/`TAR1090_DEFAULTCENTERLAT`/`TAR1090_DEFAULTCENTERLON`).
Unset, the platform falls back to a DC placeholder and every distance,
range ring and MLAT position is wrong. The frontend reads coordinates only
from the runner's `/api/v1/frontend-config` (see
`docs/GPS_COORDINATE_CONFIGURATION.md`).

### Signed-manifest coverage

| Surface | Checked |
|---|---|
| ~~39~~ 40 (corrected 2026-10-15) Quadlets whose `Exec=` goes through `scripts/verified-exec.sh` (the 39 oneshot skills and demo-api) | at every start |
| `src/common/llm.py` (calls `scripts/verify-manifest.sh` before inference) | at inference |
| `corporatetraveldc-integrity-sweep.timer` (every 15 min) | whole tree |
| web, poller, pusher, ingest, runner, amtrak-tracker, acars-watcher, verifier | **not at start** (their `CMD` launches the app directly); the image build gate and the 15-min sweep cover them |

---

## Development

All Python runs from the repo root with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python3 src/poller/skills/cps_recompute.py --force
PYTHONPATH=src python3 src/poller/fetchers/metar.py
PYTHONPATH=src python3 src/ctdc_token/cli.py list

# Postgres from the host
psql -h 127.0.0.1 -p 5432 -U dispatch corporatetraveldc

# Tests (973 `def test_` functions under tests/ on 2026-10-08; 1,222 in 146
# test_*.py files on 2026-10-15; parametrised cases add more). Keep every
# tempfile and TMPDIR inside tmp_path (the /tmp headroom guard, 2026-10-14).
PYTHONPATH=src python -m pytest tests/ -x --tb=short
```

### Skill runtime rules

**SR-1** (`src/common/sr1_log.py`): call `log_usage()` in a `finally` block.
Logged to `/var/lib/corporatetraveldc/api-usage.csv`.

**SR-2** (`src/common/sr2_gate.py`): `check_gate()` before expensive work,
`commit_gate()` only after it succeeded. Hash content-bearing fields only.
On skip, `sys.exit(0)`. Support `--force`.

### Schema

Postgres DDL is the ~~73~~ 75 (corrected 2026-10-15) numbered files in `src/common/pg_schema/`, applied by
`scripts/pg-migrate.sh`. The legacy SQLite schema constants in
`src/common/db.py` (`SCHEMA` … `SCHEMA_V40`, `SCHEMA_V43`) and
`src/common/db_swim.py` (`SCHEMA_SWIM_V41` … `V46`) remain for the rollback
backend; `init_db_all()` does not apply the `db_swim` versions. Additive only:
never drop or rename a column.

---

## Local LLM — llama.cpp

- One host process, `corporatetraveldc-llama.service` (user unit, tracked at
  `.config/systemd/user/`): `llama-server -m qwen3-4b-instruct-2507-q4_0.gguf
  --host 100.x.x.x --port 8093 -np 2 --kv-unified -c 12288 -fa on
  -ctk q8_0 -ctv q8_0 -t 2 -tb 2 --cache-ram 0 --no-webui`,
  `Slice=production.slice`, `CPUQuota=200%`, `MemoryLow=5120M`,
  `MemoryHigh=7168M`, `MemoryMax=8448M`, `MemorySwapMax=0`. The unit file sets
  `CPUWeight=9000`; a `systemctl set-property` drop-in under
  `~/.config/systemd/user.control/` overrides it to **10000** live (plus
  `IOWeight=10000`).
- `corporatetraveldc-llama-restart.timer` restarts it daily at 19:45 ET / 23:45Z.
- Clients find it through `LLAMA_BASE_URL` (`http://100.x.x.x:8093` in
  `dispatch.env`); `OLLAMA_BASE_URL` is read only as a deprecated fallback
  (renamed 2026-10-05). `src/common/llama_pool.py` resolves one host/port
  for every tier (`HOT_PORT = CHAT_PORT = LLAMA_PORT`) and serialises
  long-running generations with a box-wide lock.
- Models on disk: `qwen3-4b-instruct-2507-q4_0.gguf` (loaded, chosen
  2026-09-21, `docs/MODEL_EVALUATION_2026-09-21.md`) and
  `phi3-mini-q4_0.gguf` (kept, not loaded).
- Per-skill behaviour is the 22 personas in `src/common/personas.py`
  (`PERSONAS`); a skill's `corporatetraveldc-pi5-<task>:latest` model string
  maps to a persona via `persona_key_for()`. The 21
  `corporatetraveldc.<persona>` Modelfiles at the repo root are kept as
  manifest-covered source text; `build-models.sh` only diffs their SYSTEM
  blocks against `personas.py`.
- Legacy names kept deliberately: `ollama_model=`, `OllamaBusyError`
  (`src/common/ollama_lock.py`), `OLLAMA_TIMEOUT` (`dispatch.env` sets 240;
  code default 900), `OLLAMA_PREFLIGHT_COOL_TARGET_C` (`dispatch.env` 70.0;
  code default 62.0).

Guards: `generate()` returns `None` on any failure and the skill renders its
deterministic template; `sanitize_llm_response`; a pre-flight load gate that
waits (bounded) while load1 is above target before batch inference;
`corporatetraveldc-brief-fallback-monitor.timer` (hourly at :50) alerts when
briefs degrade to fallback (since 2026-10-14: only when a brief fell back on
its latest run, with a short narrative giving each brief's fallbacks over the
last 6 runs and whether the trend is improving, worsening or steady).

**Cloud fallback is closed.** `src/common/llm.py` would call Anthropic only
with `ANTHROPIC_API_KEY` set **and** `ANTHROPIC_FALLBACK_ENABLED` true; the
module default is `false` (since 2026-08-26) and `dispatch.env` sets `false`.
Brief skills also pass `allow_anthropic=False`. The `anthropic` SDK is still
listed in `requirements.txt` (intent in `requirements.in`; `requirements.txt`
is the generated hash lock since 2026-10-07).

**Scheduled cloud job (added 2026-10-15).** The one job that calls a cloud
model is outside `llm.py`. `corporatetraveldc-second-brain-weekly-dump`
(timer enabled, Sun 02:00Z) runs `claude --model opus -p …`
(`scripts/second-brain-weekly-dump.sh:139`) over `CLAUDE.md` and Claude's
memory files and writes the result to the vault. On a failed or unconfirmed
run it writes a failure marker
(`common.claude_weekly_fallback.mark_failure`).
`corporatetraveldc-claude-weekly-llama-fallback` (Quadlet + timer, 00/06/12/18Z)
then tries a load-gated partial stand-in on the local llama-server. **Known
defect:** its default model name,
`corporatetraveldc-pi5-claude-weekly-fallback:latest`, has no persona in
`src/common/personas.py`, and neither the Quadlet nor `dispatch.env` sets
`OLLAMA_CLAUDE_FALLBACK_MODEL` or `OLLAMA_MODEL`. `llm.py` therefore answers
502 "no persona mapping", so the backstop cannot generate as configured.

---

## FAA SWIM / NMS credentials

Per feed key `<KEY>` ∈ FDPS, STDDS, TFMS, AIM, TBFM, ITWS (AIM serves the
`fns` feed and heartbeat):

```
SWIM_NMS_USER_<KEY> / SWIM_NMS_PASS_<KEY> / SWIM_NMS_QUEUE_<KEY>
SWIM_NMS_VPN_<KEY>  (default: <KEY>; this deployment uses AIM_FNS for AIM)
SWIM_NMS_HOST_<KEY> (fallback SWIM_NMS_HOST, default tcps://ems1.swim.faa.gov:55443)
```

The ingest containers read these from `/etc/corporatetraveldc/svc/ingest.env`
(generated from `dispatch-secrets.env`). After a credential change,
regenerate the scoped file and restart only the affected feed:

```bash
scripts/ingest-feed-ctl.sh restart <feed>
scripts/ingest-feed-ctl.sh restart all --order=lightest-first --stagger=15
```

Standing convention: a SWIM account in doubt is retired and replaced with a
fresh email and passwords. New-deployment access: `docs/DATA_SOURCES.md`.

---

## Key paths

| Path | Purpose |
|---|---|
| `/opt/corporatetraveldc/private/ctdi-dispatch-internal/` | This repo (`/opt/corporatetraveldc/ctdi-dispatch-internal` is a symlink) |
| `corporatetraveldc-pgsql` (volumes `corporatetraveldc-pgsql-data`, `corporatetraveldc-pgsql-sock`) | Live database |
| `/var/lib/corporatetraveldc/corporatetraveldc.db` | Legacy SQLite path (`DISPATCH_DB`). **Present**: a 69,632-byte file last modified 2026-10-05 23:21Z (recreated by a CLI smoke test that used the wrong env-var name; see Findings in `CHANGES-core.md`). Not the live store. |
| `/etc/corporatetraveldc/dispatch.env` | Non-secret config |
| `/etc/corporatetraveldc/dispatch-secrets.env` | Master secrets file (0600); source for the scoped files |
| `/etc/corporatetraveldc/svc/<svc>.env` | Per-service scoped secrets (web, poller, pusher, ingest, runner, demo, amtrak-tracker, acars-watcher, execstandard-verifier); ~~53~~ 54 (corrected 2026-10-15) Quadlets read one |
| `/usr/local/libexec/ctdc/` | Root-owned installed copies of root-run scripts |
| `/var/lib/corporatetraveldc/api-usage.csv` | SR-1 usage log |
| `/var/lib/corporatetraveldc/skill-state/` | SR-2 gate state |
| `/run/corporatetraveldc/triggers/` | Admin trigger files: the path inside web and poller, both of which mount the podman volume `corporatetraveldc-triggers` at `/run/corporatetraveldc`. Host-side writers (the failover kickover guardrail, since 2026-10-14) write into that volume's mountpoint, not into a host `/run` directory (added 2026-10-15) |
| `scripts/backup/`, `/etc/corporatetraveldc/backup/` | Backup scripts; passphrase file (operator-only) and settings. See `docs/BACKUP.md` (added 2026-10-15) |
| `/opt/corporatetraveldc/watchlists/` | Permanent watchlist JSON |
| `/var/lib/corporatetraveldc/models/` | GGUF models |
| `.config/containers/systemd/` (repo) → `~/.config/containers/systemd/` (live) | Quadlets (copied, not symlinked) |
| `/opt/corporatetraveldc/private/executivestandard-website/articles/` | Canonical Executive Standard articles |
| `scripts/stack-refresh.sh`, `scripts/serialized-rollout.sh` | Weekly/tripwire refresh; one-at-a-time rollout |
| `scripts/cf-dns-record.sh` | Cloudflare DNS upsert/delete with the local management token (no dashboard changes) |
| `scripts/gui-window.sh` | On-demand headless maintenance desktop over SSH tunnel |
| `systemd/retired-20261003/`, `nginx/conf.d/retired-20261003/` | Config retired 2026-10-03. Every retired folder is indexed in `docs/RETIRED.md` (added 2026-10-15) |

---

## CUI handling

**CRITICAL**: this repository never contains, and must never be modified to
contain, SHARES, HEARS, HEART or any FOUO/CUI radio frequencies — in code,
configs, exports or documents. The infrastructure ships with empty
placeholder files; the operator populates them on the deployment host. The
audit log never leaves the host unredacted; rows are archived and signed by
`audit-log-archive` (daily, maintenance window) rather than deleted.

## Synthetic aircraft identifiers

Documentation uses reserved values instead of real ones: IP addresses from
RFC 5737 (`192.0.2.0/24` and its siblings), domains under RFC 2606 / RFC 6761
(`example.com`). This repository does the same for aircraft. **Any
illustrative, general aviation, or otherwise privacy-respected or
privacy-withheld tail number is replaced by a registration in the synthetic
`Q` registry**: `Q`, then a digit, then digits and/or letters (for example
`Q7K2M`). No state registers aircraft under `Q`, so a `Q` registration
can never identify a real aircraft. These values are purely for
instructional and evidentiary purposes: tests, fixtures, examples,
screenshots and review records.

- **The default everywhere.** Tests, fixtures (including captured FAA and
  ADS-B messages adapted as fixtures), code comments, documentation and demo
  samples use `Q` registrations. A real aircraft's ICAO hex code is replaced
  too, by a random value that the FAA and OpenSky registry tables show as
  unassigned when it was generated.
- **US N-number formatting.** Where the N-number format itself is the
  subject (for example the leading-`N` handling), an impossible form is used:
  no US registration begins `N0`, so `N01AB` illustrates the format without
  naming an aircraft.
- **Parsers accept the synthetic registry.** The FDPS GA-tail classifier,
  the TDLS registration extractor and the runner map's search recognise `Q`
  registrations alongside `N` ones, so synthetic fixtures exercise the same
  code paths as real general-aviation traffic. Live traffic never carries a
  `Q` registration.
- **Enforced.** `tests/scripts/test_synthetic_identifiers.py` fails on any
  US-registration-shaped identifier in tracked text. The public-mirror leak
  gate (`scripts/scrub-public-tree.py`) refuses to publish one, and reports
  only a count, never the value, since it may be on the FAA LADD privacy list.
  Operational files that must name real aircraft (the live permanent
  watchlist) stay private; the public mirror carries a synthetic
  `.example.json` instead.
- **Why.** On 2026-10-08 a sweep found 46 real registrations, 9 of them on
  the LADD list, in fixtures, comments, tests and a skill doc, several of
  them looking like placeholders. It also found a LADD-listed aircraft in the
  published permanent watchlist. Details: `docs/security-reviews/2026-10-08-06-build-arch-and-ladd.md`.
  Later the same day (review 07) a history scan found more: real hex codes
  in five tests and fixtures, two foreign registrations in tests, and the
  same identifiers in the git history of the public mirror and of two other
  public repositories. ~~All were replaced and the public histories rewritten~~
  (corrected 2026-10-15: all were replaced in the current trees, but the
  public-history rewrite was finished on 2026-10-15 01:44Z / 21:44 ET 10-14 for
  the public mirror (6 identifiers, `scripts/leak-guard/ladd_history_scan.py` +
  git-filter-repo; 472 commits kept, tip tree unchanged, a fresh-clone rescan
  finds 0). The archived public repository `corporatetravel-dispatch-mcp`
  still carries 2 identifiers in its history: open operator item)
  (`docs/security-reviews/2026-10-08-07-agents-and-adversarial-receipt.md`).

---

## Reservation system integration

Credential-gated inbound webhooks (`/webhooks/limoanywhere/reservations`,
`/webhooks/ringcentral/events`, `/webhooks/3cx/events`), or the watchlist API
directly:

```
POST /api/v1/watchlist/flights          (admin bearer token)
{"identifier": "UAL2341", "origin": "KORD", "destination": "KDCA",
 "auto_remove_at": "2026-07-01T22:00:00Z", "notes": "pickup"}
```

Trains: `POST /api/v1/watchlist/trains` with the bare train number. Full
contract: `docs/INTEGRATION_SPEC.md`. Permanent entries: edit the JSON files
in `/opt/corporatetraveldc/watchlists/`.

---

## License

**Business Source License 1.1** (source-available, not OSI open source).
Licensor [operator LLC], LLC; Licensed Work "CTDI, version 2026.08";
Change Date 2030-08-24; Change License GPL v3 or later. Full text and the
Additional Use Grant: [`LICENSE`](LICENSE). Summary, not a substitute:

- Free for non-production use.
- Free for personal self-hosted production use, or as an internal relay
  inside an organisation, provided it never serves a fee-based product or
  service to a third-party client (see the grant's carve-outs).
- Reselling, hosting, white-labeling, embedding in another commercial
  platform, or use in any fee-based client service needs a commercial license.

> The Additional Use Grant language is a working draft under legal review.
> Confirm current terms with [operator LLC], LLC before relying on it.

---

---

## Superseded (kept for the record)

### Replaced by the 2026-10-08 re-verification

~~Verified against HEAD 2c3f81b and live state on 2026-10-06 18:25Z / 14:25 ET.~~

| ~~Measure~~ | ~~Value (2026-10-06)~~ |
|---|---|
| ~~Long-running Quadlets not running~~ | ~~3: the `corporatetraveldc-client-demo@.container` template and the two `corporatetraveldc-demo-portal-{client,personal}` Quadlets (tracked, never installed live)~~ |
| ~~Active user timers~~ | ~~63 (59 `corporatetraveldc-*`)~~ |
| ~~Failed user or system units~~ | ~~0~~ |
| ~~Signed-manifest integrity sweep~~ | ~~Passing: the 18:20Z run reported "signature valid, all 1246 files match". The 17:35Z and 18:05Z runs failed on an unsigned working-tree edit to `scripts/stack-refresh.sh`, cleared by signed commit 2c3f81b at 18:10Z.~~ |

~~38 `require_admin(<action>)` (37 handlers; `/admin/push-alert` and `/admin/push-test-alert` share one)~~ (the source has 37 `require_admin(...)` dependencies)

~~# Tests (877 `def test_` functions under tests/ at HEAD; parametrised cases add more)~~

### Replaced by the 2026-10-06 verification

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-core.md`.


### Corporate Travel Dispatch Intelligence (CTDI)

~~**Documentation snapshot: 2026-09-28** (previously 2026-08-23, reconciled 2026-09-03) — factual claims below were verified against the running system and current source (previous full verification 2026-08-11, partial reconciliation 2026-08-19). The 2026-09-03 pass reconciled this file against `docs/CODEBASE_REFERENCE_DRAFT_2026-09-03.md` (a fresh code-verified audit): the Ollama → llama.cpp cutover (2026-08-27), the runner-demo restore + `DEMO_MODE=true`, the thermal-guard trigger demotion (2026-08-27), and the 2026-09-03 forward-only push-dedup redesign. **The 2026-09-28 pass folds in the two big changes since:** the **Postgres cutover** — the live write path is now Postgres (`DISPATCH_DB_BACKEND=postgres`), cutover 2026-09-18 with the reference, chat and demo tables following 09-19/09-20 (see `docs/POSTGRES_MIGRATION.md`); SQLite is now the rollback-only backend — and the **local model swap** phi3-mini → Qwen3-4B-Instruct-2507 (2026-09-21, commit `fe43973`).~~

~~Multi-region real-time travel intelligence platform. Monitors commercial aviation (FAA SWIM push feeds plus REST fallbacks), rail, weather, and airspace restrictions — delivering push alerts the moment something operationally relevant changes. Runs as rootless Podman containers managed by systemd Quadlets under a single deployment user, alongside timer-driven skill containers, a local SDR receive stack, and host-local llama.cpp (`llama-server`) LLM inference — the Ollama daemon was retired 2026-08-27, though `OLLAMA_*` env-var/parameter names survive as compatibility vocabulary (see the Local LLM section). Container/unit counts drift as feeds and skills are added, so this README does not pin them — check the live picture instead:~~

*Superseded block:*
```text superseded
systemctl --user list-units 'corporatetraveldc-*' --all --no-legend | wc -l
podman ps -a --format '{{.Names}}' | wc -l
ls .config/containers/systemd/*.container | wc -l
```

~~(As of 2026-08-23 14:0x EDT those returned 122 loaded units, 39 containers and 63 `.container` Quadlets in the repo — 64 are installed live, the extra being `corporatetraveldc-ccw-demo.container`, a client-preview service that is running but deliberately not tracked here; see `docs/INFRA_MAP.md`. Re-run the commands rather than trusting any number written here. The container count is especially volatile in *both* directions, for two independent reasons: Quadlet-managed containers are *removed*, not just stopped, when their unit stops, so a thermal LOCKDOWN or tier-1 shed makes even `podman ps -a` shrink (the same command read 30 during a shed earlier the same morning); and the timer-triggered **skill** containers are short-lived oneshots that exist only while they run, so a moment when several `*-daily-watch` / brief / digest timers overlap pushes the count *above* the long-running baseline — 34 with the long-running stack up and no skills firing, 39 with five skill containers mid-run. Neither direction is a fault.)~~

> ~~**Origin note:** CTDI was originally built for Washington, DC metro operations (executive chauffeur + CERT/ARES/Skywarn). The DC configuration is the reference implementation, not a constraint — see **[docs/REGIONALIZATION.md](docs/REGIONALIZATION.md)** for deploying elsewhere.~~

> ~~**Repository note:** The system user, container prefix, and filesystem paths use `corporatetraveldc` — the original deployment name, preserved for backward compatibility. New deployments can substitute any username; only env config and Quadlet paths need to reflect it.~~

~~📄 **[Platform Compatibility Reference (PDF)](docs/platform-compatibility.pdf)** — what works (and what doesn't) on Linux, macOS, Windows, Android, and iOS. 📐 **[Design Principles](docs/DESIGN-PRINCIPLES.md)** — local-first, offline-capable, vendor-neutral. Read before contributing. 🌍 **[Regionalization Guide](docs/REGIONALIZATION.md)** — deploying outside DC. 📡 **[Data Sources & Access Guide](docs/DATA_SOURCES.md)** — signup portals, email templates, and policy links for every integrated feed. 🗺️ **[Internal Infra Map](docs/INFRA_MAP.md)** — full private service/host/domain map (private repo only). ⚠️ **[Single-Edge-Unit Assumptions](docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md)** — every resource guardrail in this stack is tuned for **one Raspberry Pi 5 under shared-resource contention**. Read before de-consolidating or reusing values.~~

~~All public releases are GPG signed:~~

*Superseded block:*
```text superseded
ABD3976FCC006E0F3FE559177286B3118BA4EFB2 — Corporate Travel DC 'the operator' (original default key)
419A864CC29A09513039B6E03033FB4D01903159 — Rotated key, new default as of July 7, 2026
```

~~Active keys ship their pubkeys in-repo, named by full fingerprint.~~

**~~Status (2026-09-28)~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › Status (2026-09-28)

| ~~Component~~ | ~~State~~ |
|---|---|
| ~~Database backend~~ | ~~**Postgres — live since the 2026-09-18 cutover.** `DISPATCH_DB_BACKEND=postgres` is set in `/etc/corporatetraveldc/dispatch.env`; the write path, the 11 reference tables, the second-brain vault index, the runner's `chat_messages`, and the demo snapshot/profile tables all live in `corporatetraveldc-pgsql` (unix socket `/var/run/postgresql`). Schema is versioned as **65 additive migrations** in `src/common/pg_schema/` (`0001`–`0065`). **SQLite is now rollback-only** — `REFERENCE_TABLES` in `src/common/db_backend.py` is an empty frozenset, so the old `corporatetraveldc.db` WAL file holds nothing live. See `docs/POSTGRES_MIGRATION.md`.~~ |
| ~~Ops dashboard (runner app)~~ | ~~**Tailnet-only.** `http://100.x.x.x:8001` or `https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net` (nginx → :8001). The former public `ops.example.com` hostname was **retired 2026-08-02** and is hard-404'd by hostname in `src/runner/main.py` (`_RETIRED_HOSTNAMES`).~~ |
| ~~Public demo (runner, demo-playback)~~ | ~~**UP — restored 2026-08-24, and `DEMO_MODE=true` is now set explicitly** (verified live 2026-09-03: unit `active (running)`, `NRestarts=0`, `:8005 /healthz` → `ok`, and the `dispatch-runner.example.com` vhost serves 200). The 2026-08-15→24 crash loop (`sqlite3.OperationalError` from the 2026-08-14 F6 mount change) was fixed by mounting a dedicated `/var/lib/corporatetraveldc-demo` state dir (commit `0a7f643 [SHA unverified 2026-09-28; likely f63ee17]`), and the Quadlet now sets `Environment=DEMO_MODE=true` + `DEMO_SESSION_SECRET`, so the password gate (`demo.profiles` sessions), signal sanitization, and ntfy suppression are armed — closing the "explicit either way" operator directive of 2026-08-20.~~ |
| ~~Web API (browser / programmatic)~~ | ~~`https://dispatch.example.com` (Cloudflare Access gated; nginx stamps `X-CTDI-Public: 1`, which pins the request to Tier 0 regardless of token)~~ |
| ~~Tailscale direct API~~ | ~~`http://100.x.x.x:8000`~~ |
| ~~Public MCP (OpenAPI bridge)~~ | ~~**Retired 2026-08-18.** `mcpo`/`mcpo-public` units are gone (`systemctl --user list-units 'corporatetraveldc-mcpo*' --all` → 0 units); ports 8082/8083 refuse connections; the server checkout was renamed to `/home/corporatetraveldc/mcp/dispatch-mcp.archived-20260817`. The `mcp.example.com` nginx vhost still exists and proxies to the now-gone `:8083`, but the cloudflared ingress for it is `http_status:404`, so the hostname returns **404 at the tunnel edge** (corrected 2026-10-03; it never reaches nginx) — removing the dead vhost is still pending. Restoring the bridge would mean un-archiving the checkout, reinstating the `mcpo`/`mcpo-public` Quadlets, and re-pointing the vhost.~~ |
| ~~FAA SWIM NMS push feeds~~ | ~~✅ All 6 provisioned and credentialed (FDPS/STDDS/TFMS/TBFM/ITWS/FNS) — provisioned 2026-07-20, split into per-feed containers 2026-07-26. **Not continuously running by design:** `scripts/thermal-ingest-guard.py` sheds SWIM ingest containers under CPU-load/thermal pressure (see "SWIM feed liveness and thermal load-shedding").~~ |
| ~~Local LLM (llama.cpp)~~ | ~~**Ollama retired 2026-08-27.** Inference is one host-level `llama-server` (llama.cpp) systemd user unit — `corporatetraveldc-llama.service` (:8093, consolidated from separate hot/chat/report-1 tiers 2026-09-06) — serving one shared GGUF (Qwen3-4B-Instruct-2507 as of 2026-09-21); per-skill personas live in `src/common/personas.py`, not in per-skill Ollama models (see Local LLM section)~~ |
| ~~ADS-B receive (UltraFeeder)~~ | ~~✅ **Restored 2026-08-11** — the ADS-B RTL-SDR dongle had stopped enumerating on USB (~2026-08-10, container crash-looping; `adsb-feed-silence-watchdog` detected and alerted correctly); hardware reseat brought it back midday 2026-08-11 (dongle enumerates, container up, live decode confirmed). All other SDR containers (ACARS/VDL2 chain, feeders) up throughout.~~ |


### Corporate Travel Dispatch Intelligence (CTDI) › Architecture

~~web, poller, pusher, and all 7 ingest containers share one database, and that database is **Postgres**. The live backend is `corporatetraveldc-pgsql`, reached over the unix socket `/var/run/postgresql` from inside the containers (not a TCP host — see `docs/POSTGRES_MIGRATION.md` §3.1). Cutover completed **2026-09-18** (the reference, chat and demo tables followed 09-19/09-20), and the schema is carried as **65 additive migrations** in `src/common/pg_schema/` (`0001`–`0065`). The backend is selected by `DISPATCH_DB_BACKEND` in `/etc/corporatetraveldc/dispatch.env`, which is set to `postgres`; the only other value, `sqlite` (a single WAL file), is now the **rollback-only** path — `REFERENCE_TABLES` in `src/common/db_backend.py` is an empty frozenset, so the old `corporatetraveldc.db` file holds nothing live. See `docs/POSTGRES_MIGRATION.md` for which tables live where and for the cutover/rollback procedure. The runner mostly stays off the shared DB — it owns the ops frontend and its own JSON state — with one exception: its Dispatch Drawer chat history (`chat_messages`) now goes through the same shared `db.conn()` write path as everything else, since 2026-09-20:~~

*Superseded block:*
```text superseded
┌────────────────────────────────────────────────────────────────────┐
│                    deployment user (corporatetraveldc)             │
│                                                                    │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌──────────────┐    │
│  │   web     │  │  poller   │  │  pusher   │  │ ingest ×7    │    │
│  │ FastAPI   │  │ Scheduler │  │  ntfy     │  │ SWIM ×6 +    │    │
│  │ REST API  │  │ + Skills  │  │  sender   │  │ core (NWWS/  │    │
│  │  :8000    │  │           │  │           │  │ Amtrak/RF)   │    │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └──────┬───────┘    │
│        └──────────────┴──────────────┴───────────────┘            │
│              Postgres shared DB (DISPATCH_DB_BACKEND=postgres)     │
│                                                                    │
│  ┌──────────────────────────────────────────────────┐             │
│  │  runner (:8001) — Tailnet-only ops dashboard     │             │
│  │  FastAPI + React/Vite SPA, screen-reader ready   │             │
│  │  Intel Feed · ADS-B Map · Trains · AIS · Brief   │             │
│  │  proxies dispatch web API at :8000               │             │
│  │  owns user_rss_feeds.json (separate from DB)     │             │
│  └──────────────────────────────────────────────────┘             │
│                                                                    │
│  + runner-demo (:8005, public demo) · demo recorder · demo-api    │
│    (:8004) · SDR stack · ntfy · Nextcloud · Open WebUI · timers   │
└────────────────────────────────────────────────────────────────────┘
```


### Corporate Travel Dispatch Intelligence (CTDI) › Architecture › Core containers

| ~~Container~~ | ~~Image~~ | ~~Role~~ |
|---|---|---|
| ~~`corporatetraveldc-web`~~ | ~~`localhost/corporatetraveldc-web:latest`~~ | ~~FastAPI REST API (port 8000, published on 127.0.0.1 + tailnet IP), tiered auth~~ |
| ~~`corporatetraveldc-poller`~~ | ~~`localhost/corporatetraveldc-poller:latest`~~ | ~~Async scheduler — fetchers + AI skills as subprocesses, watchlist sweeps~~ |
| ~~`corporatetraveldc-pusher`~~ | ~~`localhost/corporatetraveldc-pusher:latest`~~ | ~~ntfy alert dispatcher~~ |
| ~~`corporatetraveldc-ingest-{core,fdps,stdds,tfms,tbfm,itws,notam}`~~ | ~~`localhost/corporatetraveldc-ingest:latest` (one image, 7 Quadlets)~~ | ~~Push ingest, split 2026-07-26 into 7 independent containers — one per SWIM feed plus "core" (NWWS-OI/Amtrak/local airspace) — so any single feed restarts without dropping the rest. See `src/ingest/README.md` and `scripts/ingest-feed-ctl.sh`.~~ |
| ~~`corporatetraveldc-runner`~~ | ~~`localhost/corporatetraveldc-runner:latest`~~ | ~~Ops dashboard SPA + API (port 8001) — Tailnet-only~~ |
| ~~`corporatetraveldc-runner-demo`~~ | ~~same runner image~~ | ~~Demo-playback instance (port 8005 → container 8001), reads the demo API (:8004) instead of live feeds — public vhost at `dispatch-runner.example.com`. **Running since the 2026-08-24 fix, with `Environment=DEMO_MODE=true` + `DEMO_SESSION_SECRET` set in the Quadlet** — password gate and sanitization armed (see Status).~~ |
| ~~`corporatetraveldc-demo` / `corporatetraveldc-demo-api`~~ | ~~`localhost/corporatetraveldc-demo:latest`~~ | ~~Archive recorder / read-only playback API (port 8004) over `demo.db`~~ |


### Corporate Travel Dispatch Intelligence (CTDI) › Architecture › Auxiliary containers (same host)

~~SDR/RF: `ultrafeeder` (ADS-B + tar1090, port 8080 — restored 2026-08-11, see Status), `acarsrouter` (:9080; station ids pass through untouched since 2026-10-03 — `CS-KDCA-VDL` from `dumpvdl2`, `CS-KDCA-ACARS` for the disabled `acarsdec`, `CS-KDCA-ADSB` as ultrafeeder's `MLAT_USER`; airframes.io registered those names), `acarshub` (:9081), `dumpvdl2`, `acars-watcher` (UDP 5005), plus aggregator feeders `piaware`, `fr24feed` (:8754), `planefinder` (:30053), `airnavradar`. Disabled pending hardware: `acarsdec`, `dumphfdl`, `ais`/`ais-catcher`, `ais-watcher` — these exist only as staged `*.container.disabled` files under `systemd/`, `systemd/quadlets/` and (for `acarsdec`) `.config/containers/systemd/`. None is installed in `~/.config/containers/systemd/`, so they have **no systemd unit at all** — `systemctl --user list-unit-files` matching any of those names returns 0, which is expected, not a fault. See `docs/SDR_SERVICES.md`.~~

~~Infra/comms: `ntfy` (:2586), `protonbridge` (SMTP relay, 100.x.x.x:1025 → container port 25 — tailnet-only, see `docs/INFRA_MAP.md` §4), `nextcloud-app` (:8090) + `nextcloud-db` (Postgres 16), `openwebui` (:3000), `rss-bridge` (:3001), `csexec-contact` (website contact API, :8002), `amtrak-tracker`.~~


### Corporate Travel Dispatch Intelligence (CTDI) › Architecture › Data feeds

| ~~Feed~~ | ~~Source~~ | ~~Interval~~ | ~~Status~~ |
|---|---|---|---|
| ~~METAR~~ | ~~AviationWeather.gov ADDS~~ | ~~5 min~~ | ~~✅ Active~~ |
| ~~NWS alerts~~ | ~~api.weather.gov~~ | ~~5 min~~ | ~~✅ Active (REST fallback; push-primary via NWWS-OI)~~ |
| ~~ATCSCC ops plan~~ | ~~ATCSCC~~ | ~~1 hr~~ | ~~✅ Active~~ |
| ~~Runsheet~~ | ~~Local file~~ | ~~5 min~~ | ~~✅ Active~~ |
| ~~TFR~~ | ~~tfr.faa.gov/tfrapi/getTfrList (JSON)~~ | ~~5 min~~ | ~~✅ Active — independent REST poll; **no** push-primary exists for TFRs~~ |
| ~~NAS programs~~ | ~~nasstatus.faa.gov/api/airport-status-information~~ | ~~5 min~~ | ~~✅ Active~~ |
| ~~NOTAMs (REST)~~ | ~~FAA NOTAM API~~ | ~~5 min~~ | ~~⚠️ Needs `FAA_NOTAM_API_KEY` + `FAA_NOTAM_API_SECRET` (`awaiting_credentials`). Live NOTAM data already flows via the SWIM FNS push feed regardless.~~ |
| ~~DCA / IAD FIDS~~ | ~~MWAA JSON endpoints~~ | ~~5 min~~ | ~~✅ Active (600 s staleness threshold — see `docs/DCA_IAD_FIDS.md`)~~ |
| ~~Amtrak~~ | ~~Push-primary is the **`amtrak-tracker` container** (`src/amtrak_tracker/`, api.amtraker.com, port 8898, stamps `push:amtrak`); `src/ingest/amtrak.py` is a *second* implementation of the same capability inside `ingest-core` (gated `AMTRAK_ENABLED`, same heartbeat key — the failover contract keeps them from double-writing, but which is authoritative is only discoverable from quadlet enablement state). **Still no REST fallback**: `poller/fetchers/amtrak.py` exists but has no `FETCH_SCHEDULE` entry (re-verified 2026-09-03)~~ | ~~Push~~ | ~~✅ Active (see CLAUDE.md "Known bad")~~ |
| ~~FDPS (flight plan + track, FIXM 3.0)~~ | ~~FAA SWIM NMS~~ | ~~Push~~ | ~~✅ Live (LOCKDOWN-only shed †)~~ |
| ~~STDDS (surface + terminal tracks)~~ | ~~FAA SWIM NMS~~ | ~~Push~~ | ~~✅ Live — carries no TFR data (temp tier-1 shed candidate †)~~ |
| ~~TFMS (GDP/GS/AFP/restrictions/per-flight TMI)~~ | ~~FAA SWIM NMS~~ | ~~Push~~ | ~~✅ Live (temp tier-1 shed candidate †)~~ |
| ~~AIM/FNS (digital NOTAMs)~~ | ~~FAA SWIM NMS~~ | ~~Push~~ | ~~✅ Live (LOCKDOWN-only shed † — **no longer "never shed"**, see the 2026-08-23 redesign)~~ |
| ~~TBFM (arrival sequencing)~~ | ~~FAA SWIM NMS~~ | ~~Push~~ | ~~✅ Live (LOCKDOWN-only shed †)~~ |
| ~~ITWS (terminal weather)~~ | ~~FAA SWIM NMS~~ | ~~Push~~ | ~~✅ Live (LOCKDOWN-only shed †)~~ |
| ~~NWWS-OI (NWS push)~~ | ~~NWWS-OI XMPP MUC~~ | ~~Push~~ | ~~✅ Live~~ |
| ~~EUROCONTROL NM B2B~~ | ~~EUROCONTROL~~ | ~~15 min~~ | ~~⚠️ Needs credentials — code ships ready~~ |
| ~~JASDAT (Japan)~~ | ~~JCAB/MLIT~~ | ~~15 min~~ | ~~⚠️ Needs credentials — code ships ready~~ |

**~~† SWIM feed liveness and thermal load-shedding~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › Architecture › † SWIM feed liveness and thermal load-shedding

~~"Live" above means **provisioned, credentialed, and eligible to run** — not "running continuously". All six `SWIM_NMS_{HOST,USER,PASS,QUEUE}_{FDPS,STDDS, TFMS,AIM,TBFM,ITWS}` credential sets are present in `/etc/corporatetraveldc/dispatch-secrets.env`, but `scripts/thermal-ingest-guard.py` (2-minute timer) **stops SWIM ingest containers — and, under LOCKDOWN, the entire dispatch stack except `web` — under CPU-load / thermal pressure, and starts them again when the box recovers**. This is designed behaviour, not a fault:~~

~~**Redesigned 2026-08-23 by operator directive — the two-tier load ladder this README used to describe (tier 1 `load1 >= 10`, tier 2 `load1 >= 14`, resume `load1 < 6.0`) is gone.** Every real trip on record had been load-driven, never temperature-driven (peak temp across the guard's whole journal history was ~71 °C, under the 74 °C line, with an independent auto-ramping PWM fan regulating underneath), and the old 6.0 resume bar sat *inside* normal load noise — so load and temperature are no longer symmetric. Temperature keeps its original two-stage trigger as a backstop; load collapses to a single informational/LOCKDOWN split:~~

| ~~Trip~~ | ~~Condition~~ | ~~What's shed~~ |
|---|---|---|
| ~~Temp tier 1 (mild)~~ | ~~`temp >= 74.0 °C`~~ | ~~`tfms`, `stdds` only~~ |
| ~~**LOCKDOWN**~~ | ~~`temp >= 79.0 °C` **or** `load1 >= 40.0`~~ | ~~**the entire stack except `web`**, immediately, no partial stage: all 6 SWIM feeds (`fdps,stdds,tfms,tbfm,itws,notam`), `ingest-core`, `poller`, `pusher`, `runner`~~ |
| ~~Informational only, no shed~~ | ~~`temp` 70–74 °C, or `load1` 15–40~~ | ~~—~~ |
| ~~Restore~~ | ~~`temp < 65 °C` **and** `load1 < 15.0`, held 300 s~~ | ~~tier 1 restores `tfms,stdds`; LOCKDOWN restores the whole stack~~ |

~~Note the two consequences a reader of the old table would get wrong: the `notam` container (which runs the AIM/**FNS** feed, a real 6th SWIM feed — not a NOTAM-only afterthought) is now shed under LOCKDOWN, where it previously never was; and LOCKDOWN stops `poller`/`pusher`/`runner` too, so a stopped core container is no longer automatically a fault either. `web` is the only thing guaranteed to survive.~~

~~**Two 2026-08-27 changes to the 2026-08-23 model (verified against the guard script 2026-09-03):** (1) the third LOCKDOWN trigger — "≥ 2 load-attributed brief fallbacks in 300 s" — was **demoted to informational-only** after one night produced ~15 fallback-attributed LOCKDOWN trips with real load1 of only 4–9; the count is still computed and logged every cycle but no longer trips LOCKDOWN or blocks restore. (2) With the same-day Ollama → llama.cpp cutover, the guard **no longer stops or starts any LLM service**: `ollama.service` is gone, and the `corporatetraveldc-llama-hot/chat/report-*` units are deliberately excluded from LOCKDOWN scope so the hot alert path survives exactly the events LOCKDOWN responds to.~~

~~"Load-attributed brief fallback" is a signal from `src/common/llm.py` (`_record_load_fallback()` → `/var/lib/corporatetraveldc/llm_load_fallback_events.jsonl`), logged only for `OllamaBusyError` (slot busy — the exception class name is kept from the Ollama era) or a generate-call `httpx.TimeoutException` — deliberately *not* for `httpx.ConnectError`, so a deliberately-stopped LLM server can never look like contention. Informational-only since 2026-08-27.~~

~~A real LOCKDOWN fired and fully restored on 2026-08-23; verify current state from the guard's own journal and state file rather than from this table.~~

~~**Consequences for anyone reading unit state:**~~

- ~~Finding `corporatetraveldc-ingest-<feed>.service` `inactive (dead)` with `Result=success` is **expected** and is not something to "fix" by restarting it — the guard will start it again on its own, and a manual start just gets shed again on the next 2-minute pass. Under LOCKDOWN the same is true of `poller`, `pusher`, and `runner` (the `corporatetraveldc-llama-*` units are never touched by the guard).~~
- ~~These sheds are **silent to `systemctl list-units` failure greps**, because the units exit 0.~~
- ~~The authoritative check is the guard's own state, not `systemctl`:~~

*Superseded block:*
```text superseded
cat /var/lib/corporatetraveldc/thermal_ingest_guard_state.json
journalctl --user -u corporatetraveldc-thermal-ingest-guard --since "24 hours ago"
```

~~Thresholds are overridable via `THERMAL_GUARD_*` in `dispatch.env` (defaults listed in the script header).~~


### Corporate Travel Dispatch Intelligence (CTDI) › Architecture › Push/pull failover

~~Each connected push feed stamps a `push:<feed>` heartbeat into `feed_state` every 30 seconds. Before each REST poll that has a push-primary, the poller checks whether the heartbeat is fresher than 90 seconds (`FALLBACK_MAX_AGE`); if so the REST fetch is skipped. When ingest disconnects, the heartbeat ages out and REST polling resumes automatically.~~


### Corporate Travel Dispatch Intelligence (CTDI) › API

~~**Base URLs:**~~

| ~~Endpoint~~ | ~~URL~~ | ~~Notes~~ |
|---|---|---|
| ~~Ops dashboard (runner)~~ | ~~`http://100.x.x.x:8001` / `https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net`~~ | ~~Tailnet only; no public hostname~~ |
| ~~API~~ | ~~`https://dispatch.example.com`~~ | ~~CF Access gated; served as Tier 0 (nginx sets `X-CTDI-Public: 1`)~~ |
| ~~API (tailnet)~~ | ~~`http://100.x.x.x:8000`~~ | ~~Full tier resolution via bearer token~~ |
| ~~Public demo~~ | ~~`https://dispatch-runner.example.com`~~ | ~~Live (restored 2026-08-24); password-gated (`DEMO_MODE=true` set in the Quadlet — see Status)~~ |
| <del>~~Public MCP bridge~~</del> | <del>~~`https://mcp.example.com`~~</del> | ~~**Retired 2026-08-18** — mcpo/mcpo-public units removed, ports 8082/8083 refuse connections, server checkout archived at `/home/corporatetraveldc/mcp/dispatch-mcp.archived-20260817`. The nginx vhost still exists and proxies to the dead `:8083`, but the tunnel ingress is `http_status:404`, so the hostname returns **404 at the edge** (corrected 2026-10-03); vhost removal is still pending.~~ |

**~~Tier 0 — Anonymous (selection)~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › API › Tier 0 — Anonymous (selection)

~~`src/web/main.py` declares **57** `@app.get()` routes across all tiers (re-count with `grep -c '^@app\.get(' src/web/main.py` — this grows; an earlier revision said "~50"). Of those, 37 are anonymous, 10 carry a `require_tier` dependency (T1/T2) and 10 carry `require_admin` (re-derived 2026-08-23 by AST-classifying every `@app.get` decorator + signature, not by grepping mention counts); the `src/web/routes/*.py` modules add further routes on top. The table below is a selection of the Tier-0 subset, not an exhaustive list.~~

| ~~Method~~ | ~~Path~~ | ~~Description~~ |
|---|---|---|
| ~~GET~~ | ~~`/healthz`~~ | ~~Service health + snapshot age~~ |
| ~~GET~~ | ~~`/api/v1/feeds`~~ | ~~Feed freshness + error state~~ |
| ~~GET~~ | ~~`/api/v1/cps`~~ | ~~Critical Predictability State (go/no-go)~~ |
| ~~GET~~ | ~~`/api/v1/tfr`~~ | ~~Active TFRs (no enrichment)~~ |
| ~~GET~~ | ~~`/api/v1/weather`~~ | ~~METAR snapshot~~ |
| ~~GET~~ | ~~`/api/v1/alerts`~~ | ~~Active NWS hazardous weather alerts~~ |
| ~~GET~~ | ~~`/api/v1/wx/discussion[/{awips_id}]`~~ | ~~WPC forecast discussions~~ |
| ~~GET~~ | ~~`/api/v1/airmets`~~ | ~~AIRMET/SIGMET hazard polygons~~ |
| ~~GET~~ | ~~`/api/v1/notams`~~ | ~~Active NOTAMs for DC-area airports~~ |
| ~~GET~~ | ~~`/api/v1/amtrak`~~ | ~~Amtrak DC-area status~~ |
| ~~GET~~ | ~~`/api/v1/opsplan`~~ | ~~ATCSCC daily ops plan~~ |
| ~~GET~~ | ~~`/api/v1/brief` · `/brief/history` · `/brief/weekly` · `/brief/{ref}`~~ | ~~Brief texts + history~~ |
| ~~GET~~ | ~~`/api/v1/route`~~ | ~~Ground route impact narrative~~ |
| ~~GET~~ | ~~`/api/v1/events`~~ | ~~Live SSE event stream~~ |
| ~~GET~~ | ~~`/api/v1/train-config` · `/api/v1/wx-config`~~ | ~~Operator rail / meteorology config~~ |
| ~~GET~~ | ~~`/api/v1/flightplan/{callsign}`~~ | ~~FDPS-confirmed flight plan~~ |
| ~~GET~~ | ~~`/api/v1/fids/{airport}` · `/{airport}/arrivals` · `/{airport}/{flight}`~~ | ~~DCA/IAD FIDS~~ |
| ~~GET~~ | ~~`/api/v1/adsb`~~ | ~~**Local-receiver-only** ADS-B snapshot (since 2026-08-27 — no third-party proxy; bounded by the box's own receiver range)~~ |
| ~~GET~~ | ~~`/api/v1/aircraft/{identifier}` · `/api/v1/aircraft-registry/status`~~ | ~~FAA/OpenSky registry lookup~~ |
| ~~GET~~ | ~~`/api/v1/airspace[/{feature_id}]`~~ | ~~Static DC airspace features~~ |
| ~~GET~~ | ~~`/api/v1/demo/readiness`~~ | ~~Demo archive seed status~~ |
| ~~GET/POST/PATCH/DELETE~~ | ~~`/api/v1/osint/*`~~ | ~~OSINT feed + scopes~~ |
| ~~GET~~ | ~~`/api/v1/board*`~~ | ~~Coordination board (read; posts need `X-Board-Key`)~~ |
| ~~GET~~ | ~~`/api/v1/sectors*`~~ | ~~Sector/family alert topic state~~ |


### Corporate Travel Dispatch Intelligence (CTDI) › API › Tier 1 — `cert` bearer token

| ~~Method~~ | ~~Path~~ | ~~Description~~ |
|---|---|---|
| ~~GET~~ | ~~`/api/v1/tfr-enriched`~~ | ~~TFRs with AI enrichment text~~ |
| ~~GET~~ | ~~`/api/v1/radio`~~ | ~~Radio reference~~ |
| ~~GET~~ | ~~`/api/v1/runsheet`~~ | ~~Daily runsheet + watchlist sessions~~ |
| ~~GET~~ | ~~`/api/v1/opsplan/range`~~ | ~~Ops plan date range~~ |
| ~~GET/POST/DELETE~~ | ~~`/api/v1/watchlist` (sessions)~~ | ~~Watchlist session management~~ |
| ~~GET~~ | ~~`/api/v1/watchlist` + `/history` (entries)~~ | ~~Watchlist entries + event history~~ |


### Corporate Travel Dispatch Intelligence (CTDI) › API › Tier 2 — `shares` bearer token

| ~~Method~~ | ~~Path~~ | ~~Description~~ |
|---|---|---|
| ~~GET~~ | ~~`/api/v1/cui/status`~~ | ~~CUI status — audit-logged~~ |


### Corporate Travel Dispatch Intelligence (CTDI) › API › Admin — `admin` bearer token

~~`/admin/healthz`, `/admin/feeds`, `/admin/audit`, `/admin/tokens`, `/admin/version`, `/admin/triggers`, `POST /admin/refresh-feed/{feed}`, `POST /admin/force-recompute-cps`, `POST /admin/force-opsplan-snapshot`, `POST /admin/force-osint-scrape`, `POST /admin/push-alert` (legacy alias `/admin/push-test-alert`), `GET/POST/DELETE /admin/vip`, `GET/POST/DELETE /admin/bandwidth-priority`, `/admin/approval-requests*`, `/admin/watchdog/status`, plus admin-gated watchlist entry mutations (`POST /api/v1/watchlist/{flights,trains,vessels}[,/batch]`, `POST /api/v1/watchlist/permanent/batch`, `DELETE /api/v1/watchlist/{id}`) and `POST /api/v1/remember` (second-brain capture).~~

**~~Inbound webhooks — shared-secret header (`X-Webhook-Secret`)~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › API › Inbound webhooks — shared-secret header (`X-Webhook-Secret`)

~~Credential-gated: each returns 503 until its `*_WEBHOOK_SECRET` is set in `dispatch-secrets.env`. See `src/web/routes/webhooks.py`.~~

| ~~Method~~ | ~~Path~~ | ~~Source~~ |
|---|---|---|
| ~~POST~~ | ~~`/webhooks/limoanywhere/reservations`~~ | ~~LimoAnywhere Customer API~~ |
| ~~POST~~ | ~~`/webhooks/ringcentral/events`~~ | ~~RingCentral (handles Validation-Token handshake)~~ |
| ~~POST~~ | ~~`/webhooks/3cx/events`~~ | ~~3CX Call Control API~~ |

**~~Runner API (port 8001, Tailnet-only)~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › API › Runner API (port 8001, Tailnet-only)

~~The runner serves its React/Vite SPA plus its own API. Sensitive surfaces (admin proxy, non-GET API proxy) are gated by `_is_trusted()` (Tailscale CGNAT 100.64.0.0/10 + RFC1918 + loopback; `CF-Connecting-IP` honored exclusively when present). In demo mode, untrusted origins additionally need the password-gated session cookie.~~

~~Routes: `/healthz`, `/api/whoami`, `/api/demo/{login,status,webhook-log}`, `/api/adsb/{local,live}`, `/api/{vdl2,acars,hfdl}/messages`, `/api/ais/vessels`, `/api/ask` + `/api/chat/history` (Dispatch Drawer chat — llama.cpp chat tier since 2026-08-27), `/api/dispatch/{path}` (transparent proxy → :8000, with Tier-1 token injection for an allowlist of paths — see `docs/auth-token-proxy-pattern.md`), `/api/stream` (SSE), `/api/ntfy/stream`, `/api/v1/config` (GET/PUT), `/api/v1/frontend-config`, and the RSS engine: `/api/rss`, `/api/rss/categories` (GET/POST), `/api/rss/custom`, `/api/rss/resolve-source`, `/api/rss/user-feeds` (GET/POST/DELETE).~~

~~**RSS catalog** (`src/shared/rss_catalog.py`, shared with the second-brain RSS poller): **11 built-in categories, 32 built-in feeds** (re-verified live 2026-08-23; the catalog grows, so re-count with `PYTHONPATH=src python3 -c "import shared.rss_catalog as r; print(len(r._RSS_CATALOG), sum(len(v) for v in r._RSS_CATALOG.values()), len(r.all_feed_urls()))"` — which also reports the whole pool including operator-added feeds, **34** unique URLs as of 2026-08-23) — `corporate_intel`, `marketing_intel`, `travel_trends`, `dc_area`, `aviation`, `advanced_air_mobility`, `gig_economy`, `concierge_luxury_travel`, `trains_yachts`, `executive_protection`, `osint_cybersecurity_video` — plus `__custom__` for user-defined feeds. User feeds persist in `/var/lib/corporatetraveldc/user_rss_feeds.json` (custom categories in `user_rss_categories.json`). `?limit=N` default 200 max 500; each feed capped at 100 items pre-merge; dates normalized to ISO 8601; `<enclosure type="audio/*|video/*">` items get an `audio_url` for the inline podcast player.~~


### Corporate Travel Dispatch Intelligence (CTDI) › API › Auth model

~~Tokens are created with **`ctdc-token`** (`src/ctdc_token/cli.py`). Format: `ctdc_<user>_<32-char-random>`. Only the SHA-256 hash is stored; plaintext is shown once at creation.~~

*Superseded block:*
```text superseded
Tier 0 → anonymous (all /api/v1/* data endpoints), and ANY request carrying
         X-CTDI-Public: 1 (stamped by the public nginx vhosts) regardless of token
Tier 1 → bearer token tier=cert
Tier 2 → bearer token tier=shares (audit-logged; CUI-adjacent)
Admin  → bearer token tier=admin (all /admin/* endpoints except one
         deliberate exception, below)
```

~~**One `/admin/*` endpoint is unauthenticated by design.** `GET /admin/approval-requests/{request_id}/resolve` (`src/web/main.py:2601`) carries no auth dependency — Cloudflare strips `Authorization` through the tunnel, so a token-gated resolve link would be untappable from a phone off the tailnet. Security rests on the UUID4 request id plus single-use enforcement in `resolve_approval_request()`. Verified live 2026-08-23: an unauthenticated request to that path returns an app-level `404` (reaches the handler), while `/admin/healthz` returns `403`. See `docs/COMPLIANCE_SECURITY.md`.~~

~~**Network origin no longer grants any tier.** The old `Tailscale-User-Login`-header / source-IP tier grant was removed (it was confirmed spoofable via XFF against the live container); a real bearer token is required for T1+ on every path. See `src/auth/auth.py`.~~


### Corporate Travel Dispatch Intelligence (CTDI) › Watchlist system

~~Two tiers share one monitoring/alert pipeline — full detail in `src/shared/watchlist_README.md`:~~

~~**Permanent** — **JSON** files in `/opt/corporatetraveldc/watchlists/` (`permanent_flights.json`, `permanent_trains.json`, `permanent_vessels.json`, `permanent_drones.json`). Hot-reloaded by `WatchlistFileWatcher` within ~65 s, no restart.~~

~~**Transient** — added via REST (`POST /api/v1/watchlist/{flights,trains,vessels}`, admin token). Auto-expire via `auto_remove_at`, swept every 60 s.~~

~~Four entry types: flight (callsign), train (Amtrak number), **vessel (MMSI — AISHub sweep every 300 s, requires `AIS_AISHUB_ID`)**, and drone (Remote-ID/UAS — separate `uas_phase` columns, not the OOOI machine, since multi-sortie UAS legitimately alternate launched/landed). Events fire dual ntfy pushes (domain topic + concise `dispatch`) with **forward-only, content-hash dedup** (redesigned 2026-09-03 after the UAL1369 re-page incident: an unchanged event stays suppressed indefinitely; only a genuine content change re-fires). Flight monitoring is **local-only since 2026-08-27**: local UltraFeeder ADS-B → FDPS push cache → FIDS → schedule inference (identity resolution likewise local: own ADS-B → ingested FDPS → local FAA/OpenSky registry tables). airplanes.live is no longer queried programmatically; FlightAware AeroAPI code survives but is dormant without `FLIGHTAWARE_AEROAPI_KEY`. OOOI phase state machine: `pre_departure → out → off → on → in`, phases never revert; same-phase confirmations resolve by source authority (ACARS > SMES > TFMS > TBFM/ADS-B > FIDS).~~


### Corporate Travel Dispatch Intelligence (CTDI) › ntfy topics (core set)

| ~~Topic~~ | ~~Content~~ | ~~Priority~~ |
|---|---|---|
| ~~`tfr-alert` / `hot-alerts`~~ | ~~VIP/POTUS TFR, Marine One/AF1, severe-ops events~~ | ~~5~~ |
| ~~`flight-alerts` / `train-alerts` / `vessel-alerts`~~ | ~~Watchlist events per domain~~ | ~~2–5~~ |
| ~~`dispatch`~~ | ~~Concise bottom line for all events~~ | ~~mirrors source~~ |
| ~~`dispatch-debriefs` / `dispatch-ops`~~ | ~~Full debrief tables / weekly aggregate~~ | ~~2–3~~ |
| ~~`cps`~~ | ~~CPS score changes~~ | ~~3–5~~ |
| ~~`wx-alerts`~~ | ~~NWS + ITWS hazardous weather~~ | ~~3–4~~ |
| ~~`nas-alerts`~~ | ~~NAS program/restriction/NOTAM alerts~~ | ~~2–5~~ |
| ~~`<family>-alerts` + `<family>-<zone>` (tfms/tbfm/fdps/itws/aim_fns × zny/zdc/zid/zob/zatl/zhu/zla/zse)~~ | ~~Escalating family/sector alerts, per-topic throttled~~ | ~~2–4~~ |
| ~~`ops-brief` / `ep` / `ep-advance`~~ | ~~Hourly briefs~~ | ~~2–4~~ |
| ~~`ops-health`~~ | ~~Freshness audit, watchdogs, thermal guard~~ | ~~2–5~~ |
| ~~`osint-alerts`~~ | ~~OSINT scope hits~~ | ~~2–3~~ |
| ~~`approval-gate`~~ | ~~Approval notices (sudo gate, Cowork token gate, council requests): Deny only -- allowing is a signed `scripts/approve.sh` (2026-10-04)~~ | ~~4~~ |

~~Full catalog + trigger/dedup logic: `docs/ALERT_REFERENCE.md`; design rationale: `docs/ALERT_ARCHITECTURE.md`.~~


### Corporate Travel Dispatch Intelligence (CTDI) › CPS — Critical Predictability State

~~Part 135.609-informed go/no-go score. Factors: ceiling, visibility, wind, precipitation (METAR), airspace (TFRs + static restricted areas), GDP (NAS programs). Output `GREEN/GO`, `YELLOW/MARGINAL`, `RED/NO-GO`. Computed by `poller/skills/cps_recompute.py` hourly and on demand via `POST /admin/force-recompute-cps`.~~

**~~Demo Mode & Travel Pattern Intelligence~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › Demo Mode & Travel Pattern Intelligence

~~A built-in archive recorder (`corporatetraveldc-demo.service`) captures rolling snapshots of every live feed into `demo.db` (zlib-compressed, ~52-week retention on <500 MB). The demo-playback stack:~~

- ~~`corporatetraveldc-demo-api.service` — read-only playback API, port 8004, serving **only the sovereign scrubbed DB** (`/var/lib/corporatetraveldc-demo-source/demo-source.db`, `:ro` mount) — it holds no live-DB code path; rows reach that file only via the host-side `scripts/scrub-demo-source.py` scrub+promote pass (two-layer scrub, rows that fail the allowlist post-scan are dropped, never shipped). Playback time is virtualized against a 14-day window anchored at the last promotion.~~
- ~~`corporatetraveldc-runner-demo.service` — second runner instance, port 8005, `DISPATCH_BASE_URL=http://100.x.x.x:8004`. **Restored 2026-08-24** (dedicated `/var/lib/corporatetraveldc-demo` state mount, commit `0a7f643 [SHA unverified 2026-09-28; likely f63ee17]`) and the Quadlet now sets **`DEMO_MODE=true` + `DEMO_SESSION_SECRET`**, so the app-layer protections below are active.~~
- ~~Public hostname: **`https://dispatch-runner.example.com`** — serving 200 (verified 2026-09-03). With `DEMO_MODE=true` the instance is password-gated (`POST /api/demo/login`, HMAC-signed `ctdc_demo_session` cookie via `src/demo/profiles.py` access profiles, 8 h default) with signals sanitized server-side.~~

~~Seed readiness: `GET /api/v1/demo/readiness` reports per-tier (2w/8w/12w/24w/36w/52w) archive readiness. Config (`DEMO_RECORDER_INTERVAL=300`, `DEMO_RECORDER_RETENTION=364`, `DEMO_RECORDER_SEED_TARGET=14`) — these values are correct, but they are **not set in `dispatch.env`**; they exist only as `os.environ.get` defaults in `src/demo/recorder.py:41-43` (alongside `DEMO_RECORDER_API_BASE`, and `DEMO_RECORDER_API_TOKEN` which *is* read from `/etc/corporatetraveldc/dispatch-secrets.env`). To change one, either add it to `dispatch.env` (nothing reads it from there today) or edit the default in `recorder.py`.~~

~~The same archive doubles as a longitudinal dataset (NOTAM construction windows, TFR frequency, GDP seasonality, METAR climatology, Amtrak OTP) for quarterly planning and partnership evidence — collected as a byproduct of normal operation.~~

**~~Supported Platforms~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › Supported Platforms

> ~~Full detail in **[docs/platform-compatibility.pdf](docs/platform-compatibility.pdf)**.~~

| ~~Platform~~ | ~~Server stack~~ | ~~Containers~~ | ~~Local LLM (llama.cpp)~~ | ~~Install script~~ |
|---|---|---|---|---|
| ~~**Linux x86_64 / ARM64** (Pi 5 reference)~~ | ~~✅ Full~~ | ~~Podman ✅~~ | ~~✅~~ | ~~`install/install.sh`~~ |
| ~~**macOS** (Apple Silicon / Intel)~~ | ~~✅ Full~~ | ~~Podman/Docker ✅~~ | ~~✅~~ | ~~`install/install.sh`~~ |
| ~~**Windows x64**~~ | ~~✅ via WSL2~~ | ~~Docker/Podman Desktop~~ | ~~✅ native~~ | ~~`install/install-windows.ps1`~~ |
| ~~**Android ARM64** (Termux)~~ | ~~✅ bare Python~~ | ~~❌~~ | ~~✅~~ | ~~`install/install-android.sh`~~ |
| ~~**iOS / iPadOS**~~ | ~~❌ web client only~~ | ~~❌~~ | ~~❌~~ | ~~browse to deployment URL~~ |

~~The `solace-pubsubplus` SWIM library is Linux-only (prebuilt wheels x86_64; ARM64 builds from source) — SWIM push ingest requires Linux; other platforms run the REST-fallback feed set.~~


### Corporate Travel Dispatch Intelligence (CTDI) › Installation › Prerequisites

- ~~Linux host running Fedora (reference deployment: Fedora 44 aarch64 on a Pi 5, SELinux enforcing), Debian, or Ubuntu~~
- ~~Rootless Podman with a systemd user session (linger enabled)~~
- ~~llama.cpp (`llama-server`) on the host — all inference is local, no cloud LLM key required. The reference deployment runs it as ONE systemd user unit (`.config/systemd/user/corporatetraveldc-llama.service`, consolidated 2026-09-06 from the earlier separate hot/chat/report-1 tiers) over one shared GGUF (Qwen3-4B-Instruct-2507 as of 2026-09-21, previously phi3-mini); Ollama itself was retired 2026-08-27.~~


### Corporate Travel Dispatch Intelligence (CTDI) › Installation › First-time setup

*Superseded block:*
```text superseded
git clone <this-repo> /opt/corporatetraveldc/private/ctdi-dispatch-internal
cd /opt/corporatetraveldc/private/ctdi-dispatch-internal

# Secrets
cp dispatch-secrets.env.template /etc/corporatetraveldc/dispatch-secrets.env
chmod 0600 /etc/corporatetraveldc/dispatch-secrets.env
# populate credentials (SWIM, NTFY_TOKEN, FAA NOTAM key, …)
# AND set ULTRAFEEDER_LAT / ULTRAFEEDER_LON — see the note below

# Build container images
bash build-images.sh

# Verify the Modelfile↔personas.py sync (build-models.sh no longer builds
# anything — since the 2026-08-27 llama.cpp cutover there is no per-skill
# model artifact; it now diffs each corporatetraveldc.<skill> Modelfile's
# SYSTEM block against src/common/personas.py)
bash build-models.sh

# Install the llama.cpp host unit (2026-09-06: the hot/chat/report-1 tiers
# were consolidated into ONE unit, corporatetraveldc-llama.service, serving a
# single Qwen3-4B-Instruct-2507 GGUF on :8093; the `-llama-*` glob does NOT
# match it -- name each explicitly or the model server never installs).
cp .config/systemd/user/corporatetraveldc-llama.service ~/.config/systemd/user/
cp .config/systemd/user/corporatetraveldc-llama-restart.{service,timer} ~/.config/systemd/user/

# Install Quadlets
cp .config/containers/systemd/*.container ~/.config/containers/systemd/
systemctl --user daemon-reload

# Start the core stack (production uses the stagger units:
# corporatetraveldc-stack-boot-stagger + corporatetraveldc-boot-stagger)
systemctl --user start corporatetraveldc-web corporatetraveldc-poller corporatetraveldc-pusher

# Verify
curl http://127.0.0.1:8000/healthz

# Create an admin token
PYTHONPATH=src python3 src/ctdc_token/cli.py create \
  --user operator --tier admin --label admin-phone
```


### Corporate Travel Dispatch Intelligence (CTDI) › Installation › After any code change

*Superseded block:*
```text superseded
bash build-images.sh          # pass a target name to rebuild one image
systemctl --user daemon-reload
systemctl --user restart corporatetraveldc-web corporatetraveldc-poller \
                         corporatetraveldc-pusher corporatetraveldc-runner
```

~~This flow only ever touches our own `localhost/corporatetraveldc-*` images. The external/third-party containers (the SDR feeders, nextcloud, etc.) are deliberately on a separate cadence — a weekly `podman auto-update` check via `corporatetraveldc-weekly-external-image-update.timer` (Sundays 04:15 ET; standing rule 2026-09-03, see `scripts/weekly-external-image-update.sh`'s header for the Nextcloud major-version-jump incident behind its alerting).~~

~~**If you're self-hosting a real ADS-B receiver, hard-code your actual GPS coordinates — don't run on the placeholder.** Set `ULTRAFEEDER_LAT`/ `ULTRAFEEDER_LON` in `dispatch-secrets.env` (see the template's own note) to your antenna's real position, and set the UltraFeeder Quadlet's `READSB_LAT`/`READSB_LON`/`TAR1090_DEFAULTCENTERLAT`/ `TAR1090_DEFAULTCENTERLON` to match. Left unset, the platform still runs — it silently falls back to a generic Washington-DC-area placeholder — but every distance-from-you calculation, the compass summary, the tactical map's range rings, and the ADS-B embed's native "H"/home key and per- aircraft distance columns will all be wrong, and MLAT positioning accuracy depends on the receiver's own site position being correct, not just the display. `runner`'s `/api/v1/frontend-config` endpoint is the one place the frontend reads this from — if you're extending the UI, read from there rather than hardcoding a literal into a `.jsx`/`.js` file (see `docs/GPS_COORDINATE_CONFIGURATION.md` for the incident that made this a documented rule instead of an assumption).~~

~~Note on signed-manifest integrity (scope verified 2026-08-19): the `verified-exec.sh` gate covers the timer-triggered **skill** quadlets, `src/common/llm.py` before every inference, and the 15-minute `corporatetraveldc-integrity-sweep` timer. The long-running core containers (web/poller/pusher/ingest/runner) do **not** run the check at startup — but a stale manifest still blocks every skill run and inference, so re-sign (`scripts/sign-manifest.sh`) after any code change, **before** rebuilding images: web/poller bake `MANIFEST.sha256` into the image, and building against an unsigned tree ships a permanently-mismatched manifest (correct order is **sign → build → restart**).~~


### Corporate Travel Dispatch Intelligence (CTDI) › Development

~~All Python commands run from the repo root with `PYTHONPATH=src`:~~

*Superseded block:*
```text superseded
# Run a skill manually (--force bypasses the SR-2 hash gate)
PYTHONPATH=src python3 src/poller/skills/cps_recompute.py --force
PYTHONPATH=src python3 src/poller/skills/tfr_enrichment.py --force

# Run a fetcher manually
PYTHONPATH=src python3 src/poller/fetchers/metar.py

# Token management
PYTHONPATH=src python3 src/ctdc_token/cli.py list
PYTHONPATH=src python3 src/ctdc_token/cli.py revoke --prefix ctdc_user_

# Inspect the database (Postgres; see docs/POSTGRES_MIGRATION.md §3.1
# for why it's a unix socket, not a TCP host, from inside a container --
# from the bare host, use `psql -h 127.0.0.1 -p 5432 -U dispatch corporatetraveldc`)
psql -h /var/run/postgresql -U dispatch -d corporatetraveldc \
  -c "SELECT * FROM cps_scores ORDER BY computed_at DESC LIMIT 3;"

# Tests
python -m pytest tests/ -x --tb=short
```


### Corporate Travel Dispatch Intelligence (CTDI) › Development › Skill runtime rules

~~**SR-1** (`src/common/sr1_log.py`): call `log_usage()` in a `finally` block — always. Logged to `/var/lib/corporatetraveldc/api-usage.csv`.~~

~~**SR-2** (`src/common/sr2_gate.py`): call `check_gate()` before any expensive computation or LLM call, and `commit_gate()` only *after* the guarded work succeeded (the check/commit split landed 2026-08-25 so a mid-run crash leaves the gate open instead of permanently suppressing retries). Hash only content-bearing fields (never timestamps). If the check says skip, `sys.exit(0)` immediately. Support `--force`. Skills with inherently time-bounded inputs declare an SR-2 exemption in their docstrings instead.~~

**~~Schema migrations~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › Development › Schema migrations

~~The schema authority is versioned additively across **two** modules since 2026-08-30: `src/common/db.py` (`SCHEMA`, `SCHEMA_V2`…`SCHEMA_V40`, plus `SCHEMA_V43`) and `src/common/db_swim.py` (`SCHEMA_SWIM_V41/V42/V44+` — the v41+ SWIM tables were deliberately split into their own module during the 2026-08-30 SWIM audit; the numbering continues across both files). Check the current top with `grep -ohE 'SCHEMA(_SWIM)?_V[0-9]+' src/common/db.py src/common/db_swim.py | sort -u -V | tail -1` — the number moves fast, re-run rather than trusting any figure here. **Trap:** `init_db_all()` does *not* pick up db_swim's versions — any consumer of the v41+ tables must call the `init_db_swim_v4x()` functions explicitly (documented in db_swim's docstring). Never drop or rename columns — only `ALTER TABLE ADD COLUMN`.~~

**~~Local LLM — llama.cpp (Ollama retired 2026-08-27)~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › Local LLM — llama.cpp (Ollama retired 2026-08-27)

~~**All inference is local.** No external LLM API key is required. Since the **2026-08-27 cutover**, inference runs as a raw host-level `llama-server` (llama.cpp) process, CPU-only, bound to the tailnet IP at `100.x.x.x:8093`.~~

~~**2026-09-06 consolidation:** the original three separate tiers (hot/chat/ report-1, each its own resident model copy) were merged into ONE unit, `corporatetraveldc-llama.service` — three resident copies of the same 2.18 GB model drove 4 GB into zram and dropped throughput to ~0.5 tok/s under concurrent load. Now: one model, one process, two request slots (`-np 2 --kv-unified -c 12288`), hard-capped at two CPU cores (`CPUQuota=200%`) — "hot briefs/alerts can only ever preempt another report, never add a third core or server," per the operator directive that drove the consolidation. Slot discipline (one long-runner at a time on slot 1; hot/chat fast-lane on slot 2) lives in `common/llama_pool.py` + `common/llm.py`.~~

~~**2026-09-21 model swap:** phi3-mini → **Qwen3-4B-Instruct-2507** (q4_0), after a live A/B/C bake-off against real production skill runs on this exact box — Qwen3-4B measured faster (5.1 tok/s generation vs phi3-mini's 3.6) despite being the larger model, and was the only candidate that reliably covered every named section in a multi-section synthesis task instead of silently dropping one. See `docs/MODEL_EVALUATION_2026-09-21.md` for the full writeup. phi3-mini and an evaluated-but-not-chosen Qwen2.5-3B are kept on disk as historical record, not loaded.~~

~~Per-skill behavior comes from **`src/common/personas.py`** — a registry of ~23 personas (system preamble + task text + `num_ctx`/`num_predict`/sampling params), extracted verbatim from the old per-skill Ollama Modelfiles. Editing a persona takes effect on the next request: no rebuild, no restart, no smoke test. The `corporatetraveldc.*` Modelfiles at repo root are kept **only** as human-readable canonical source text that the signed-manifest integrity check still verifies; `build-models.sh` (reworked 2026-08-30) no longer builds anything — it diffs each Modelfile's SYSTEM block against `personas.py`.~~

~~**Naming note — deliberate:** `OLLAMA_BASE_URL`, `ollama_model=`, `OllamaBusyError`, and "Ollama unavailable" log lines all survive verbatim so zero call sites changed at cutover, and again survived the 2026-09-21 model swap for the same reason. They now mean llama.cpp, whichever GGUF is currently loaded. Resource governance moved with the 2026-08-27 cutover: the old `ollama.service` + `20-resource-limits.conf` drop-in and the `ollama-governor` SIGSTOP/SIGCONT unit are gone; `corporatetraveldc-llama.service` carries its own `CPUWeight`/`MemoryMax` limits in its unit file, and a daily `corporatetraveldc-llama-restart.timer` (03:00 ET) cycles it for freshness.~~

**~~Per-skill personas (successor to the dedicated per-task models)~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › Local LLM — llama.cpp (Ollama retired 2026-08-27) › Per-skill personas (successor to the dedicated per-task models)

~~Each LLM-calling skill still requests a `corporatetraveldc-pi5-<task>:latest` model string, but since 2026-08-27 that name resolves to a **persona** in `src/common/personas.py` (`persona_key_for()`), not an Ollama model — the ~21 per-skill Modelfiles were each really the same shared GGUF with a different SYSTEM block, so the SYSTEM blocks were extracted into the registry and one shared GGUF is served instead. Personas exist for: `ops-brief`, `ops-brief-trend`, `ep-advance`, `ep-advance-trend`, `ep-advance-venues`, `chat`, `osint-monitor`, `tfr-enrichment`, `route-impact`, `weekly-summary`, `transport-digest`, `disruption-weather-digest`, `dispatch-desk-memo`, `secondbrain-daily`, `secondbrain-weekly`, and the seven `aam-*`/`*-daily-watch` watches (re-derive from `personas.py` — the registry grows).~~

~~**Guards that survived the cutover:** the deterministic-fallback contract (`generate()` returns `None` on any failure → the skill renders its own template), the response guard (`sanitize_llm_response` — repetition-loop / persona-echo / truncation trims, each validated against real 2026-08-17 failure specimens), central prompt sanitization, thermal cool-launch and load pre-flight gates, and `corporatetraveldc-brief-fallback-monitor.timer` (hourly) alerting loudly if briefs degrade to deterministic fallback. **Retired with Ollama:** the candidate/smoke/promote model build, the gemma SWA denylist, `_abandon_ollama_generation()` and the model-swap overhead problem itself (models are now permanently resident per tier).~~

~~Cloud fallback: **closed on this box — zero cloud calls.** `ANTHROPIC_FALLBACK_ENABLED` is **fail-closed by default since 2026-08-26** (module default `false`), *and* `/etc/corporatetraveldc/dispatch.env` sets it `false` explicitly. Brief skills additionally pass `allow_anthropic=False` as a second, narrower opt-out. With the local server unavailable, skills fall back to deterministic templates; `brief-fallback-monitor` (hourly) alerts when that happens. Design history (Ollama era, superseded): `docs/DEDICATED_MODELS_PLAN.md`.~~

**~~Changing a persona~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › Local LLM — llama.cpp (Ollama retired 2026-08-27) › Changing a persona

*Superseded block:*
```text superseded
$EDITOR src/common/personas.py    # takes effect on the next request
bash build-models.sh              # verify Modelfile↔personas.py sync (no build step exists anymore)
scripts/sign-manifest.sh          # both files are manifest-covered
```

~~(Prewarm timers, the candidate/smoke/promote pipeline, and per-skill model rebuilds are all Ollama-era history — models are permanently resident per tier now, and there is no per-skill model artifact to build.)~~


### Corporate Travel Dispatch Intelligence (CTDI) › FAA SWIM / NMS credentials

~~All six feeds are provisioned and credentialed (all `SWIM_NMS_{HOST,USER,PASS,QUEUE}_<KEY>` sets present). Note that provisioned does not mean permanently running: `thermal-ingest-guard` sheds SWIM ingest containers under load — see "† SWIM feed liveness and thermal load-shedding" above before diagnosing a quiet feed as a credential problem. Reference for rotation/re-provisioning (`<KEY>` ∈ FDPS, STDDS, TFMS, AIM, TBFM, ITWS — note AIM credentials serve the `fns` feed/heartbeat):~~

*Superseded block:*
```text superseded
SWIM_NMS_USER_<KEY> / SWIM_NMS_PASS_<KEY> / SWIM_NMS_QUEUE_<KEY>
SWIM_NMS_VPN_<KEY>  / SWIM_NMS_HOST_<KEY>   (fallback SWIM_NMS_HOST,
                                             default tcps://ems1.swim.faa.gov:55443)
```

~~After credential entry, restart just the affected feed:~~

*Superseded block:*
```text superseded
scripts/ingest-feed-ctl.sh restart <feed>          # or:
scripts/ingest-feed-ctl.sh restart all --order=lightest-first --stagger=15
```

~~To request SWIM access for a new deployment see [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).~~


### Corporate Travel Dispatch Intelligence (CTDI) › Key paths

| ~~Path~~ | ~~Purpose~~ |
|---|---|
| ~~`/opt/corporatetraveldc/private/ctdi-dispatch-internal/`~~ | ~~This repo (`/opt/corporatetraveldc/ctdi-dispatch-internal` is a symlink to it)~~ |
| ~~`src/`~~ | ~~All Python source~~ |
| ~~`/var/lib/corporatetraveldc/corporatetraveldc.db`~~ | ~~SQLite database (WAL), path set by `DISPATCH_DB`. Since the 2026-09-18 cutover everything runs on Postgres (`DISPATCH_DB_BACKEND=postgres`) — `REFERENCE_TABLES` in `src/common/db_backend.py` is now an empty frozenset. The physical file no longer exists — it was removed after the cutover and its space reclaimed (confirmed absent 2026-09-28; see `docs/CODEBASE_REFERENCE_2026-09-28.md` §2). `sqlite` remains only as the rollback *backend* selector; there is no untouched on-disk backup at this path — see `docs/POSTGRES_MIGRATION.md`~~ |
| ~~`corporatetraveldc-pgsql` (Postgres, unix socket `/var/run/postgresql`)~~ | ~~Live database since the 2026-09-18 cutover (reference/chat/demo tables followed 09-19/09-20) — the write path (65 migrations, `src/common/pg_schema/`), the 11 reference tables, the second-brain vault index, `dispatch-chat.db`'s `chat_messages`, and `demo.db`/`demo_access.db`'s `demo_snapshots`/`demo_profiles` — see `docs/POSTGRES_MIGRATION.md`~~ |
| ~~`/etc/corporatetraveldc/dispatch.env`~~ | ~~Non-secret platform config~~ |
| ~~`/etc/corporatetraveldc/dispatch-secrets.env`~~ | ~~Credentials (mode 0600)~~ |
| ~~`/var/lib/corporatetraveldc/api-usage.csv`~~ | ~~SR-1 skill usage log~~ |
| ~~`/var/lib/corporatetraveldc/skill-state/`~~ | ~~SR-2 hash gate state~~ |
| ~~`/run/corporatetraveldc/triggers/`~~ | ~~Admin trigger files~~ |
| ~~`/opt/corporatetraveldc/watchlists/`~~ | ~~Permanent watchlist **JSON** files~~ |
| ~~`/var/lib/corporatetraveldc/user_rss_feeds.json`~~ | ~~Runner: user Intel Feed subscriptions~~ |
| ~~`.config/containers/systemd/` (repo) → `~/.config/containers/systemd/` (live)~~ | ~~Quadlet unit files~~ |
| ~~`/opt/corporatetraveldc/private/executivestandard-website/articles/`~~ | ~~Canonical source of every Executive Standard article (one `<slug>.md` each; 2026-10-03). `executive_standard_sync.py` reads it; `gated: true` / "members only" in frontmatter = hashes-only on the public transparency sibling~~ |
| ~~`scripts/cf-dns-record.sh`~~ | ~~Upsert/delete one Cloudflare DNS record with the local management token (same post-incident pattern as `cf-service-token-*.sh`; `--show`/`--dry-run` are read-only). First use 2026-10-03: pointed `executivestandard.example.com` at Substack~~ |
| ~~`scripts/gui-window.sh`~~ | ~~On-demand headless maintenance desktop (`Xvfb` + `i3` + loopback `x11vnc`, reached via `ssh -L` over Tailscale), bounded by an auto-teardown timer; teardown sweeps for leftovers. For GUI-only jobs such as pairing an agent's desktop app~~ |
| ~~`scripts/scheduled-podman-prune.sh` + `corporatetraveldc-podman-prune.timer`~~ | ~~Daily 07:15 ET dangling-image prune (never `-a`, never anything younger than 24 h). `build-images.sh` tags the outgoing image `:previous` before each rebuild — rollback is `podman tag <svc>:previous <svc>:latest` + restart~~ |
| ~~`systemd/retired-20261003/`, `nginx/conf.d/retired-20261003/`~~ | ~~Dead config retired 2026-10-03: duplicate Quadlets that had drifted from `.config/containers/systemd/`, and the public `executivestandard` vhost (hostname handed to Substack)~~ |


### Corporate Travel Dispatch Intelligence (CTDI) › CUI handling

~~**CRITICAL**: This repository never contains, and must never be modified to contain, actual SHARES, HEARS, HEART, or any FOUO/CUI radio frequencies — in code, configs, exports, or documents, even password-protected. The infrastructure ships with empty placeholder files; the operator populates credentialed data from authorized sources on the deployment host. The audit log is append-only, 90-day retention, and never leaves the host.~~

**~~Reservation System Integration~~** *(former heading)*


### Corporate Travel Dispatch Intelligence (CTDI) › Reservation System Integration

~~CTDI can add flights/trains to the watchlist automatically when a reservation is created in livery/booking software, via the credential-gated inbound webhooks (`/webhooks/limoanywhere/reservations`, `/webhooks/ringcentral/events`, `/webhooks/3cx/events`) or by calling the watchlist API directly:~~

*Superseded block:*
```text superseded
POST /api/v1/watchlist/flights          (admin bearer token)
{"identifier": "UAL2341", "origin": "KORD", "destination": "KDCA",
 "auto_remove_at": "2026-07-01T22:00:00Z", "notes": "Smith pickup"}
```

~~For trains use `/api/v1/watchlist/trains` with the train number. Platforms without native webhooks can poll their reservations API on cron and sync the same way. Permanent entries: edit the JSON files in `/opt/corporatetraveldc/watchlists/` (hot-reloaded).~~


### Corporate Travel Dispatch Intelligence (CTDI) › License

~~**Business Source License 1.1** (source-available, not OSI-approved open source). Full text: [`LICENSE`](LICENSE). Summary, not a substitute for the actual terms:~~

- ~~Free for non-production use (evaluation, development, testing) always.~~
- ~~Free for production use as a personal self-hosted deployment, or as an internal relay/middleware layer within an organization of any size, **provided** that use never serves a fee-based product or service rendered to a third-party client or customer (see the Additional Use Grant in `LICENSE` for the exact boundary, including the white-label, hosted-service, and platform-absorption carve-outs).~~
- ~~Any other production use -- reselling as a hosted/managed service, white-labeling, embedding it as a component of another commercial platform, or using it (even invisibly) in the course of any fee-based service to a client -- requires a commercial license from [operator LLC], LLC.~~
- ~~Each release converts automatically to GPL v3-or-later four years after its first public distribution (per-version Change Date; see `LICENSE`), so the platform becomes fully open source over time rather than staying locked up indefinitely.~~

> ~~**Status: the Additional Use Grant language in `LICENSE` is a working draft and is currently under legal review.** It reflects the intended terms but has not yet been confirmed by counsel. Do not treat it as final for a production licensing decision -- contact [operator LLC], LLC directly to confirm current terms before relying on it.~~
