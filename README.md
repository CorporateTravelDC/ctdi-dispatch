# Corporate Travel Dispatch Intelligence (CTDI)

Re-verified against code (the tree committed with this text, on top of `92231b1`) and live state on 2026-10-08 12:15Z / 08:15 ET. Previous full verification: HEAD `2c3f81b`, 2026-10-06 18:25Z.
Front-door refresh 2026-10-15 00:50–01:00Z / 2026-10-14 20:50–21:00 ET: counts, status, thermal guard,
watchlist identity, CPS, demo, rollout and the documentation index re-checked against the code at main
`1491bf5a` plus the batch staged for the next relay, and against the live system. Corrections are dated
"(corrected 2026-10-15 …)" in place.
Re-verified 2026-10-25 15:39–15:50Z / 11:39–11:50 ET against HEAD `5f7fca01` and the live system
(docs-first revalidated sweep, stage 3); corrections are dated "(corrected 2026-10-25 …)" or
"(superseded 2026-10-25 …)" in place.
Opening section re-checked 2026-11-21 23:55Z / 18:55 ET against HEAD `703d8b70` (clean tree), the
operator status snapshot `/srv/ctdc-handoff/status/status.json` (generated 23:54Z) and the public
dispatch GETs; corrections are dated "(corrected 2026-11-21 …)" in place. Live figures that the
status snapshot does not carry (installed Quadlet files, non-`corporatetraveldc-*` and root timers)
were not re-measured.

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

Counts at the verification time above (re-run the commands; they move):

| Measure | Value | Command |
|---|---|---|
| Containers running | ~~35 long-running (36 at measurement: one oneshot skill mid-run)~~ ~~(corrected 2026-10-15 00:51Z: 38 = 37 long-running, now including the two demo portals, + 1 oneshot skill mid-run)~~ ~~(superseded 2026-10-25 15:39:55Z: 39 running; the two demo portals are **not** among them, see Status)~~ (corrected 2026-11-21 23:54Z: 38 running per `status.json`; the two demo portals are still not among them) | `podman ps --format '{{.Names}}' \| wc -l` |
| `.container` Quadlets tracked in this repo | ~~76 (38 long-running, 38 `Type=oneshot` skills/jobs)~~ (corrected 2026-10-15: 77 = 38 long-running + 39 `Type=oneshot` skills/jobs) ~~(2026-10-20: 76 after the cleanup relay retired `corporatetraveldc-ops-brief-deferred` and `corporatetraveldc-nms-v240-check` to `retired-20261020/`, `docs/RETIRED.md`)~~ ~~(corrected 2026-10-25: 77 top-level tracked files and 77 installed live; the 2026-10-20 cleanup retired two to `retired-20261020/` and `corporatetraveldc-opensky.container` was added afterwards. 38 of them run through `verified-exec.sh`: 37 `Type=oneshot` skills/jobs + `demo-api`)~~ (corrected 2026-11-21: 78 top-level tracked: the 2026-10-25 count of 77 plus `corporatetraveldc-ingest-tfdm.container`, added 2026-11-19 in `e2fb66b0`; 38 of them run through `verified-exec.sh`: 37 `Type=oneshot` skills/jobs + `demo-api`. Installed count not re-measured) | `git ls-files ':(glob).config/containers/systemd/*.container' \| wc -l` |
| Long-running Quadlets not installed live | ~~2: `corporatetraveldc-demo-portal-{client,personal}` (tracked, never installed). The `corporatetraveldc-client-demo@.container` template is now installed but has no instance~~ (corrected 2026-10-15: 0. Both demo portals have been installed and active since 2026-10-12 (see Status). The `corporatetraveldc-client-demo@.container` template is installed with no instance. The diff now shows only the repo-side `amtrak-tracker.container.disabled` and `corporatetraveldc-acarsdec.container.disabled` (these create no unit) and, live-only, two `corporatetraveldc-ultrafeeder.container.bak-*` files and `nextcloud-net.network`.) (2026-10-20: `amtrak-tracker.container.disabled` retired; `nextcloud-net.network` now tracked; the two `.bak-*` files removed live by the cleanup relay; the diff shows only `corporatetraveldc-acarsdec.container.disabled` and the `retired-20261020/` folder.) | `diff <(ls .config/containers/systemd) <(ls ~/.config/containers/systemd)` |
| Active user timers | ~~64 (59 `corporatetraveldc-*`)~~ ~~(corrected 2026-10-15: 72 listed, 67 of them `corporatetraveldc-*`; 75 `.timer` files tracked)~~ ~~(superseded 2026-10-25 15:39:55Z: 79 listed, 74 of them `corporatetraveldc-*`; 78 top-level `.timer` files tracked)~~ ~~(corrected 2026-10-26: 80 listed, 75 of them `corporatetraveldc-*`; 78 top-level `.timer` files tracked, 79 installed; `systemctl --user list-timers --all`, `git ls-files`, `ls` 22:36Z)~~ (corrected 2026-11-21 23:54Z: 84 `corporatetraveldc-*` timers active per `status.json`; 90 top-level `.timer` files tracked. The full `list-timers` total and the installed count were not re-measured) | `systemctl --user list-timers --all` |
| Root (system) timers for this stack | ~~7 (watchdog 90 s, team-liveness, skill-grants, llama-council, NTS cert refresh, Tailscale cert renew, watchdog-tune one-shot)~~ (corrected 2026-10-15: 9. Those 7, plus `corporatetraveldc-agent-leak-scan` (hourly) and `corporatetraveldc-agent-home-snapshot` (daily 03:20Z). The watchdog-tune one-shot fired 2026-10-12 00:00Z.) (corrected 2026-11-22: 13 tracked, `git ls-files 'systemd/*.timer' 'systemd/system/*.timer'`: the 9 above plus `agent-pamphlet-install`, `agent-remote-control-boot`, `wifi-sdio-recovery` and `oob-budget`. Root units are not in `status.json`, so the installed count was not re-measured) | `systemctl list-timers --all` |
| Failed user or system units | ~~1: `corporatetraveldc-integrity-sweep` (user), failing on the unsigned staged files of this batch; clears at the next sign~~ ~~(corrected 2026-10-15 00:51Z: 0 user, 0 system. While a batch's edited files are unsigned, the integrity sweep fails until the next sign.)~~ (corrected 2026-11-21 23:54Z: 3 user (`corporatetraveldc-backup`, `corporatetraveldc-backup-freshness`, `corporatetraveldc-brain-capacity-review`) and 1 system (`user@1001.service`, the user manager of `ctdc-agent-anthropic-claude`), per `status.json`) | `systemctl --user --failed; systemctl --failed` |
| Feeds reported by `/api/v1/feeds` | ~~20 (12 REST + 8 `push:*` heartbeats)~~ (corrected 2026-11-21 23:55Z: 21 = 12 REST + 9 `push:*` heartbeats; `push:tfdm` is the new one) | `curl -s http://127.0.0.1:8000/api/v1/feeds` |
| Web API routes | ~~126 (82 in `src/web/main.py`, 44 in `src/web/routes/*.py`)~~ ~~(corrected 2026-10-15: 128 = 83 in `src/web/main.py` + 45 in `src/web/routes/*.py`)~~ ~~(corrected 2026-10-25: 135 = 81 in `src/web/main.py` + 54 in `src/web/routes/*.py`, AST count 15:41:53Z)~~ (corrected 2026-11-21: 139 = 81 in `src/web/main.py` + 58 in `src/web/routes/*.py`, AST count at `703d8b70`) | AST count of route decorators |
| Postgres migrations | ~~73 (`src/common/pg_schema/0001`–`0073`)~~ ~~(corrected 2026-10-15: 75, `0001`–`0075`; `0075_surface_event_log`)~~ ~~(corrected 2026-10-25: 83 `.sql` files, numbered up to `0084_semantic_vessel_drone_geometry`)~~ (corrected 2026-11-21: 88 `.sql` files, numbered up to `0089_swim_census_and_expiry`) | `ls src/common/pg_schema/*.sql \| wc -l` |

`GET /healthz` at the verification time returned
`{"status":"ok","reason":null,"snapshot_age_seconds":1,"audit_count_24h":24,"token_count_active":6,"cps":{"score":"GREEN","label":"GO"}}`
(2026-10-15 00:51Z: the same, except `audit_count_24h` 25). (2026-10-25 15:39:58Z: the
payload also carries an `optional_stale` list; `status` ok, `token_count_active` 6, CPS GREEN/GO.)
(2026-11-21 23:55Z: `status` ok, `optional_stale` empty, `audit_count_24h` 59, `token_count_active` 11,
CPS GREEN/GO.)
It does not list feeds; `/api/v1/feeds` does.

Container counts move in both directions by design: Quadlet-managed
containers are removed (not just stopped) when their unit stops, so a
thermal LOCKDOWN shrinks `podman ps -a`; and the ~~38~~ ~~39 (corrected 2026-10-15)~~ 37 (corrected 2026-10-25: two retired 2026-10-20) oneshot skill containers
exist only while they run, so overlapping timers push the count above the
long-running baseline.

> **Origin note:** CTDI was built for Washington, DC metro operations
> (executive chauffeur + CERT/ARES/Skywarn). The DC configuration is the
> reference implementation, not a constraint — see
> **[docs/REGIONALIZATION.md](docs/REGIONALIZATION.md)**.

> **Repository note:** the system user, container prefix and filesystem paths
> use `corporatetraveldc`, the original deployment name. New deployments can
> substitute any username; only env config and Quadlet paths need to change.

- **Platform Compatibility Reference (PDF) (`docs/platform-compatibility.pdf`, private repository)**
- **[Design Principles](docs/DESIGN-PRINCIPLES.md)** — local-first, offline-capable, vendor-neutral.
- **[Regionalization Guide](docs/REGIONALIZATION.md)** — deploying outside DC.
- **[Data Sources & Access Guide](docs/DATA_SOURCES.md)** — signup portals and policy links for every feed.
- **Internal Infra Map (`docs/INFRA_MAP.md`, private repository)** — private service/host/domain map (private repo only).
- **[Single-Edge-Unit Assumptions](docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md)** — every resource guardrail here is tuned for one Pi 5 under shared-resource contention.
- **[Backups](docs/BACKUP.md)** — encrypted Borg backups, nightly, synced to an off-box target; freshness check and weekly restore test (added 2026-10-15).
- **[Retired code index](docs/RETIRED.md)** — everything taken out of service, by folder (added 2026-10-15).
- **[Glossary](docs/GLOSSARY.md)** — acronyms, platform terms and the logic rules with their enforcing tests, generated from `docs/glossary.yaml` (added 2026-10-19).

### Documentation index (added 2026-10-15)

Living documents. Each describes the platform as it is now and keeps changed
statements struck through with the date. Files marked *(private)* are dropped
from the public mirror.

- **Architecture and infra:** INFRA_MAP (`docs/INFRA_MAP.md`, private repository) *(private)*,
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
  [AIRLINE_TIME_FIDELITY_SPEC](docs/research/AIRLINE_TIME_FIDELITY_SPEC.md),
  [SWIM_CENSUS](docs/SWIM_CENSUS.md) (added 2026-11-22),
  [GATE_BAGGAGE_BACKFILL](docs/GATE_BAGGAGE_BACKFILL.md) (added 2026-11-22).
- **Alerts:** [ALERT_REFERENCE](docs/ALERT_REFERENCE.md),
  [ALERT_ARCHITECTURE](docs/ALERT_ARCHITECTURE.md).
- **Operations:** [BACKUP](docs/BACKUP.md), [RETIRED](docs/RETIRED.md),
  [OPERATOR_CONSOLE](docs/OPERATOR_CONSOLE.md),
  HEADLESS_ACCESS (`docs/HEADLESS_ACCESS.md`, private repository) *(private;
  public: [example](docs/HEADLESS_ACCESS.example.md))*,
  TAILNET_MIGRATION_INVENTORY (`docs/TAILNET_MIGRATION_INVENTORY.md`, private repository) *(private;
  public: [example](docs/TAILNET_MIGRATION_INVENTORY.example.md))*,
  [scheduled-task skills](docs/tasks/scheduled/README.md); added 2026-11-22:
  [PLATFORM_SENTINELS](docs/PLATFORM_SENTINELS.md),
  [BRAIN_CAPACITY_POLICY](docs/BRAIN_CAPACITY_POLICY.md) (heavy-job admission),
  [LOCKDOWN_OOB](docs/LOCKDOWN_OOB.md) ([deployment](docs/LOCKDOWN_OOB_DEPLOYMENT.md)),
  [HANDOFF_MIRROR](docs/HANDOFF_MIRROR.md) ([standing rule](docs/HANDOFF_MIRROR_STANDING_RULE.md)),
  [DEV_RELAY](docs/DEV_RELAY.md).
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
  `AGENTS.md` (private repository), [skills/](skills/); added 2026-11-22:
  [PER_AGENT_API_ACTIONS](docs/PER_AGENT_API_ACTIONS.md),
  [AGENT_PAMPHLET_DELIVERY](docs/AGENT_PAMPHLET_DELIVERY.md),
  [AGENT_NEUTRAL_SKILL_CATALOG](docs/AGENT_NEUTRAL_SKILL_CATALOG.md),
  [SHARED_PLATFORM_CONTEXT](docs/SHARED_PLATFORM_CONTEXT.md).
- **Security and compliance:** [SECURITY.md](SECURITY.md),
  [COMPLIANCE_SECURITY](docs/COMPLIANCE_SECURITY.md),
  [GUARDRAILS_PROVEN](docs/GUARDRAILS_PROVEN.md),
  [GUARDRAILS_JUSTIFICATION](docs/GUARDRAILS_JUSTIFICATION.md),
  [REPRODUCIBLE_BUILDS](docs/REPRODUCIBLE_BUILDS.md),
  [SECRETS_NAMING_AND_ISOLATION](docs/SECRETS_NAMING_AND_ISOLATION.md),
  CREDENTIALS_REGISTER (`docs/CREDENTIALS_REGISTER.md`, private repository) *(private)*,
  [SUDO_JUSTIFICATION_PROPOSAL](docs/SUDO_JUSTIFICATION_PROPOSAL.md),
  [LADD_CUI_HANDLING](docs/LADD_CUI_HANDLING.md),
  [HONEYPOT_FAIL2BAN](docs/HONEYPOT_FAIL2BAN.md),
  [HARDWARE_KEYS_DESIGN](docs/HARDWARE_KEYS_DESIGN.md),
  [GPG_KEYS_PUBLISHED](docs/GPG_KEYS_PUBLISHED.md),
  [ISO_42001_ALIGNMENT](docs/ISO_42001_ALIGNMENT.md),
  [regulated-operator-setup](docs/regulated-operator-setup.md),
  [security-reviews](docs/security-reviews/README.md).
- **Models and second brain:** [DEDICATED_MODELS_PLAN](docs/DEDICATED_MODELS_PLAN.md),
  SECOND_BRAIN_STATUS (`docs/SECOND_BRAIN_STATUS.md`, private repository) *(private; public:
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
[COGS_BUILD_VS_RENT_2026-09-28](docs/COGS_BUILD_VS_RENT_2026-09-28.md),
[FINDINGS_2026-10-06](docs/FINDINGS_2026-10-06.md),
[COGS_AGENT_GOVERNANCE_2026-10-29](docs/COGS_AGENT_GOVERNANCE_2026-10-29.md),
[SECOND_BRAIN_DEMO_ARCHIVE_BOUNDARY_2026-10-29](docs/SECOND_BRAIN_DEMO_ARCHIVE_BOUNDARY_2026-10-29.md),
`docs/benchmarks/`, `docs/tickets/`, the
`docs/LIVE_STATE_CHECK_*` series, `docs/docs-refresh-2026-10-06/` and
`docs/security-reviews/`.

Public releases are GPG signed. Two code-signing keys ship at the repo
root as `<fingerprint>.gpg`:

```
ABD3976FCC006E0F3FE559177286B3118BA4EFB2 — previous key, kept for verifying older commits (also shipped as CorporateTravelDC.gpg)
419A864CC29A09513039B6E03033FB4D01903159 — default since 2026-07-07: signing subkey of primary 3B29752DACA3544CEA60D01A7B81F49CD96C1631; signs commits and MANIFEST.sha256.asc
```

Further public keys ship under `security/`: the operator primary and the
agent manifest-signing delegate (`trusted-signing-key.pub.asc`, two key
blocks), Pi agent rotation signing, and break-glass. `SECURITY.md` at the
repo root is the authority.
Security contact: developer@csexecutiveservices.com (CS Executive Services LLC).

---
## Status

| Component | State (2026-10-06 18:25Z unless a row says otherwise) |
|---|---|
| Database | **Postgres** (`corporatetraveldc-pgsql`, `postgres:16-alpine`), `DISPATCH_DB_BACKEND=postgres` in `/etc/corporatetraveldc/dispatch.env`. Containers reach it over the unix socket in the shared `corporatetraveldc-pgsql-sock` volume mounted at `/var/run/postgresql`; the host reaches it on `127.0.0.1:5432`. Cutover 2026-09-18 (reference/chat/demo tables 09-19/09-20). ~~73~~ ~~75 (corrected 2026-10-15)~~ ~~83 (corrected 2026-10-25, numbered to `0084`)~~ 88 (corrected 2026-11-22, numbered to `0089`; `0080` is unused) additive migrations in `src/common/pg_schema/`. `REFERENCE_TABLES` in `src/common/db_backend.py` is an empty frozenset; `sqlite` survives only as a rollback backend selector. See `docs/POSTGRES_MIGRATION.md`. |
| Ops dashboard (runner) | Tailnet-only: `http://100.x.x.x:8001` or `https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net` (nginx 443 → :8001). `ops.example.com` was retired 2026-08-02 and is hard-404'd by `_RETIRED_HOSTNAMES` in `src/runner/main.py`. |
| Public demo (runner-demo) | Up: `https://dispatch-runner.example.com` returns 200, `:8005/healthz` 200. Quadlet sets `DEMO_MODE=true`; `DEMO_SESSION_SECRET` comes from `/etc/corporatetraveldc/demo-secrets.env`. **The data it replays is stale:** demo-api serves `/var/lib/corporatetraveldc-demo-source/demo-source.db`, last written 2026-08-14 14:21Z; the scrub+promote timer `corporatetraveldc-demo-source-refresh.timer` is `disabled` and has never fired. Re-checked 2026-10-15 00:55Z: all still true (`:8005/healthz` ok, public hostname 200, file unchanged since 2026-08-14 14:21:42Z, timer disabled). Added 2026-10-15: runner-demo and demo-api also mount the shared Postgres socket and load `dispatch.env`, because the demo password-gate profiles (`demo_profiles`, migration 0057) moved to Postgres on 2026-09-20. Playback data still comes only from `demo-source.db`. (2026-10-20: superseded. Since relay 2 (2026-10-19), playback and profiles come from the demo Postgres instance `corporatetraveldc-pgsql-demo`, and the refresh timer runs nightly at 03:17 UTC. `demo-source.db` is destroyed, and the sqlite rollback and both sovereign mounts are removed: `docs/DEMO_DATA_ISOLATION_PLAN_2026-08-13.md` addendum 2026-10-20.) (2026-11-21 23:59Z, status.json: runner-demo, demo-api and `corporatetraveldc-pgsql-demo` running (pgsql-demo healthy); the refresh timer is enabled, last fired 2026-11-21 03:17Z, next 2026-11-22 03:17Z.) |
| Demo portals (added 2026-10-15) | `corporatetraveldc-demo-portal-client` (:8088) and `-personal` (:8087): `nginx:alpine` containers serving `/home/corporatetraveldc/demos` read-only, bound to 127.0.0.1 and the tailnet IP. ~~Installed and active since 2026-10-12.~~ (corrected 2026-10-25 15:39:55Z: both units are `inactive (dead)` and nothing listens on :8087/:8088; `corporatetraveldc-demo-portals-boot.service` is enabled but also inactive. Whether they should be running is an operator decision; see the sweep register.) (Re-checked 2026-11-22 00:00Z: still true; neither portal container is running, nothing listens on :8087/:8088, and the boot unit is enabled but inactive.) They have no tunnel ingress (none in `cloudflared/config.yml`). Swapped with `scripts/demo-portal.sh`. Their Quadlet headers cite `docs/DEMO_PORTALS.md`, which does not exist; `docs/CLIENT_DEMO_PATTERN.md` describes the pattern. | <!-- not-a-claim -->
| Backups (added 2026-10-15) | `corporatetraveldc-backup.timer` (daily 03:40Z, up to 10 min randomized delay): an encrypted Borg archive, synced to the off-box Proton Drive target (live since 2026-10-12). LADD table data stays on the box. `backup-freshness` runs every 6 h (01:15, 07:15, 13:15, 19:15Z) and `backup-restore-test` Sun 05:30Z; root `agent-home-snapshot` runs 03:20Z. See `docs/BACKUP.md`. (2026-11-21 23:59Z, status.json: **failing.** `corporatetraveldc-backup.service` is `failed` (exit code 1, last run 18:57:45Z) and `corporatetraveldc-backup-freshness.service` is `failed` (exit code 1, 19:15Z). The last restore test, 2026-11-15 05:30Z, succeeded.) (Added 2026-11-22: a direct path is tracked beside Borg, written 2026-11-19 after the USB backup stick failed. `scripts/backup/ctdi-backup-direct.sh` (`corporatetraveldc-backup-direct.timer`, 03:40Z) dumps, zstd-compresses and gpg-encrypts each object to an operator public key, uploads it, checks it on the remote and then deletes the staged copy. LADD table data is excluded. `corporatetraveldc-backup-restore-test-direct.timer` (Sun 05:30Z) checks the newest set on the remote. `docs/BACKUP.md` says it "runs now" while `BACKUP_MODE=direct`, but live, on 2026-11-22 00:19Z, both direct timers are installed and **disabled**, and the Borg timer is still the active one.) |
| Backup uplink (added 2026-10-15) | ~~The cellular backup uplink has been down since 2026-10-06, so failover has no working second path.~~ (superseded 2026-10-25 15:45:56Z: `enu1` is connected (NetworkManager profile `Verizon Hotspot`, metric 600) and the failover watchdog reports `primary=wld0:ok backup=enu1:ok`, `failed_over=0`; the second path is back.) (2026-11-18: the hotspot profile was renamed `Hockey Puck` and its metric is now 1100. On 2026-11-22 00:00Z both uplinks are up, `jorransgateway 1` on `wld0` at metric 600 and `Hockey Puck` on `enu1` at metric 1100, so `wld0` carries the default route.) (Added 2026-11-22: route tiers in `scripts/net-failover-watchdog.sh` are 600 `jorransgateway 1`, 1100 `Hockey Puck` and 1600 the puck's own WiFi as last resort, with 100 reserved for a planned Starlink. They apply to IPv4 and IPv6 alike. The watchdog runs as the operator's user service, so `config/polkit/49-ctdc-net-failover.rules` (2026-11-17) lets that account switch NetworkManager connections. It refuses while a lockdown marker or the uncommanded-reboot hold is present, and falls back to NetworkManager's default "auth" if a hold cannot be read. `config/network/gai.conf` has no precedence lines, so address selection is IPv6-first.) See `docs/NETWORK_FAILOVER.md`. |
| Web API | Public: `https://dispatch.example.com` (Cloudflare Access; nginx stamps `X-CTDI-Public: 1`, which pins every request to Tier 0 regardless of token). Path-scoped Access bypasses reach the app without login for at least `/robots.txt`, `/api/v1/board` and `/webhooks/*` (probed: 200, 200, 405-for-GET) (2026-10-25 15:45:56Z: the same codes reproduce on the loopback nginx vhost with the `Host` header; `/robots.txt` is answered by the honeypot snippet in nginx, not the app; the Cloudflare Access policy itself is unverified from the box). Tailnet: `http://100.x.x.x:8000`. (Added 2026-11-22: HTTP/2 is off in nginx since 2026-11-19 because of an nginx HTTP/2 CVE. `nginx/conf.d/00-http2-off.conf` sets `http2 off;` at http level, each tracked server block repeats it, and `tests/scripts/test_http2_off.py` fails on any active `http2 on`. The file says to re-enable it only after nginx 1.31.9 or later is installed.) |
| Agent gateway (OAuth 2.1 + remote MCP) | Live at `https://agents.example.com` since 2026-10-05: `/.well-known/oauth-authorization-server` 200, other paths 404. Per-agent connector slugs at `/mcp/{slug}`: `chatgpt`, `claude-code`, `codex`, `cowork` (~~all active 2026-10-08~~ (corrected 2026-10-25 15:45:56Z, `scripts/agent-gateway.sh status`: `chatgpt`, `claude-code` and `cowork` each have one active connection (cowork also one dormant and one revoked); both `codex` connections are revoked (`revoke-tokens`, the account is inert since 2026-10-22)); `POST /mcp/codex` without a token returns 401). (2026-11-22: neither `ctdc-agent-openai-*` account is in `/etc/ctdc-accounts.conf` any more; see "Team accounts". The connector list itself was not re-checked.) Consent is the operator's SSH-signed approval. Every tool call is recorded in the audit chain as `agent.tool.call` (connector, account, tool, outcome, SHA-256 of the arguments, never their content; since 2026-10-08). Global kill: `scripts/agent-gateway.sh kill-all`. Code `src/web/routes/agent_gateway.py`, migrations 0071/0073. See `docs/AGENT_SEGMENTATION.md`. |
| Old MCP bridge (mcpo) | Retired 2026-08-18; nothing listens on 8082/8083 (re-checked 2026-11-22). `mcp.example.com` returns 404 at the tunnel edge (ingress `http_status:404`). The nginx vhost `mcp.example.com.conf` is still present in the repo and live (local request returns 502). |
| Operator console | `/console` on the tailnet name only (nginx tailnet vhost → web :8000); the public dispatch vhost 404s it. Sign-in is an SSH-signed approval (kind `console-login`). See `docs/OPERATOR_CONSOLE.md`. |
| Executive Standard members edition | `members.executivestandard.example.com` (gated by `auth_request` → `corporatetraveldc-execstandard-verifier` on 127.0.0.1:8787) and `invite.executivestandard.example.com` (invite host; returns 200). Logic in `src/common/es_invites.py`, migration 0072. Their nginx vhosts live in the `executivestandard-website` repo. |
| FAA SWIM push feeds | All six (FDPS, STDDS, TFMS, TBFM, ITWS, AIM/FNS) connected: every `push:*` heartbeat was 15–29 s old at verification (2026-10-15 00:55Z: 5–31 s; 2026-10-25 15:39:58Z: 13–30 s, `push:amtrak` 32–242 s on its 300 s cycle; 2026-11-22 00:00Z, `GET /api/v1/feeds`: 3–30 s, `push:amtrak` 82 s). Added 2026-11-19: a seventh consumer, `corporatetraveldc-ingest-tfdm` (SWIM TFDM), whose `push:tfdm` heartbeat is optional for `/healthz` (14 s old on 2026-11-22 00:00Z). Load-shed by `scripts/thermal-ingest-guard.py` under pressure (see below), so "connected" is not "always running". |
| Local LLM | One user unit, `corporatetraveldc-llama.service`: `llama-server` bound to `100.x.x.x:8093`, model `qwen3-4b-instruct-2507-q4_0.gguf`, `-np 2 --kv-unified -c 12288`, `-t 2 -tb 2`, `CPUQuota=200%`. Ollama is gone (nothing on :11434). Added 2026-10-15: `corporatetraveldc-claude-weekly-llama-fallback` (Quadlet + timer, 00/06/12/18Z) is meant to use this server to stand in for the weekly Claude dump when that run fails. ~~Its model name has no persona mapping, so it cannot generate as configured (see "Local LLM").~~ (superseded 2026-10-25: the persona `claude-weekly-fallback` exists in `src/common/personas.py:807` and `persona_key_for("corporatetraveldc-pi5-claude-weekly-fallback:latest")` resolves to it; see "Local LLM".) |
| ADS-B / ACARS / VDL2 receive | Up: ultrafeeder, acarsrouter, acarshub, dumpvdl2, acars-watcher and ~~four~~ five (corrected 2026-10-25: `corporatetraveldc-opensky` added since 2026-10-20) aggregator feeders all running (re-checked 2026-11-21 23:59Z, status.json). |
| Signed-manifest integrity sweep | ~~2026-10-08 12:10Z: **failing** on one unsigned staged file (this batch); clears at the next sign. The sweep now also checks build provenance of running local images: 2 verified, 16 unverified (built one commit before HEAD), 0 failed. Unverified clears when the images are rebuilt from signed HEAD.~~ ~~(corrected 2026-10-15 00:50Z: passing: "signature valid, all 1442 files match"; build provenance 18 verified, 0 unverified, 0 failed. The sweep fails while a batch's edited files are unsigned and clears at the next sign. Known verifier limits are in `SECURITY.md`.)~~ ~~(superseded 2026-10-25 15:36:18Z, `scheduled-integrity-sweep.sh --status` + `sweep.log`: status ok, "signature valid, all 1712 files match"; build provenance 2 verified, 16 unverified, 0 failed, the 16 being the running local images built 08:17–08:21Z from `5f7fca0`, one commit behind the main checkout's HEAD `8017f02`; it clears at the next rollout from signed HEAD. The sweep fails while a batch's edited files are unsigned and clears at the next sign. Known verifier limits are in `SECURITY.md`.)~~ ~~(2026-11-02 16:26Z, pre-pentest docs parity: **failing** live; `MANIFEST.sha256` now has 2,128 lines. Expected to clear when the 2026-11-02 release is signed and the root copies are reinstalled; if it does not, the sweep journal names the file.)~~ (superseded 2026-11-21 23:48Z, status.json: the last `corporatetraveldc-integrity-sweep.service` run (23:47:50Z) ended `success`, and the timer runs it every 15 min; `MANIFEST.sha256` has 2,264 lines. Build-provenance counts were not re-checked. The sweep fails while a batch's edited files are unsigned and clears at the next sign. Known verifier limits are in `SECURITY.md`.) |
| Team accounts (`/etc/ctdc-accounts.conf`) | 2026-10-08: agents `ctdc-agent-anthropic-claude` (login mode claude) and `ctdc-agent-openai-codex` (codex); services `ctdc-agent-anthropic-cowork`, `ctdc-agent-openai-chatgpt`, `ctdc-agent-llama`, `ctdc-agent-dispatch` (preloaded). Liveness is an AND of login, signer key, no token revocation and no kill order (`scripts/team-liveness.sh`). See `docs/AGENT_SEGMENTATION.md`, `docs/AGENT_TRUST_MODEL.md`. ~~(2026-11-02: seven accounts; `ctdc-agent-anthropic-claude-ea` (uid 1007, login mode claude, the operator's executive-assistant agent) is the one this row omitted. The two `ctdc-agent-openai-*` accounts are being retired.)~~ (superseded 2026-11-22, live registry: seven lines. Agents in claude login mode: `ctdc-agent-anthropic-claude`, `ctdc-agent-anthropic-claude-ea` (uid 1007, the operator's executive-assistant agent) and `ctdc-agent-xai-grokbot` (preloaded). Services: `ctdc-agent-llama`, `ctdc-agent-dispatch` (preloaded), `ctdc-agent-anthropic-cowork` and `ctdc-agent-xai-grok`. Neither `ctdc-agent-openai-*` account is listed any more.) |
| Build integrity | Every local image: hash-locked Python (`requirements*.in` → `requirements*.txt`), base images by digest, apt from a dated snapshot, `--platform` pinned to the host, SBOM + provenance receipt per build. Levels 1–3 of `docs/REPRODUCIBLE_BUILDS.md`; bit-for-bit (Level 4) not claimed. (Added 2026-11-22: `scripts/build/apt-snapshot-guard.py` (`corporatetraveldc-apt-snapshot-guard.timer`, every 6 h at :17) reads the apt snapshot pin's `Valid-Until`. It alerts 48 h before expiry. At 24 h it prepares the next pin, gpgv-checked, as a hash-pinned patch with a ready relay in `/srv/ctdc-handoff/`. It never signs or commits. `scripts/serialized-rollout.sh` calls it with `--check-rollout`, which refuses a pin that expires within 2 h.) |
## Architecture

web, poller, pusher, the eight ingest containers, runner (chat history only),
demo recorder/API and the execstandard verifier share one Postgres database
(`corporatetraveldc-pgsql`). The backend is chosen by `DISPATCH_DB_BACKEND`;
`postgres` is live, and `src/common/db_backend.py` raises `SqliteRetired` for
`sqlite` in production (operator decision 2026-10-19). The runner otherwise owns
its own JSON state (`user_rss_feeds.json`, `runner-layer-config.json` UI config).

```
┌────────────────────────────────────────────────────────────────────┐
│                    deployment user (corporatetraveldc)             │
│                                                                    │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌──────────────┐    │
│  │   web     │  │  poller   │  │  pusher   │  │ ingest ×8    │    │
│  │ FastAPI   │  │ fetchers +│  │  ntfy     │  │ SWIM ×7 +    │    │
│  │ :8000     │  │ skills    │  │  sender   │  │ core (NWWS,  │    │
│  │           │  │           │  │           │  │ local RF)    │    │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └──────┬───────┘    │
│        └──────────────┴──────┬───────┴───────────────┘            │
│                   Postgres (corporatetraveldc-pgsql)               │
│                                                                    │
│  runner (:8001, tailnet-only ops dashboard, proxies web :8000)     │
│  runner-demo (:8005) → demo-api (:8004) → demo Postgres instance   │
│  amtrak-tracker (Amtrak push) · SDR stack · ntfy · Nextcloud ·     │
│  Open WebUI · 37 oneshot skill containers (timer/trigger-started)  │
│                                                                    │
│  host: llama-server :8093 · nginx 80/443 · cloudflared (user unit) │
│        tailscaled · Pi-hole + Unbound · chronyd (NTS :4460)        │
└────────────────────────────────────────────────────────────────────┘
```

(corrected 2026-11-21: 37 Quadlets in `.config/containers/systemd/` are `Type=oneshot` skill
containers. Most start from a timer; the RSS-driven daily watches start from
`scripts/rss-watch-trigger.sh` and `knowledge-graph-compile` from a `.path` unit. The 38th
`verified-exec` Quadlet is the long-running `demo-api`. Ingest is eight containers since
`ingest-tfdm` landed on 2026-11-19, commit e2fb66b0.)

Resource slices (added 2026-11-22; unit files in `.config/systemd/user/`): `production.slice` holds
the production containers and `llama-server`. `production-brain.slice` is the shared pool for the
heavy Second Brain, RSS, graph and research jobs (see "Heavy-job admission"). `agents.slice` holds
AI agent sessions and `humans.slice` human teammate sessions, each kept apart from production.

### Core containers

| Container | Image | Role |
|---|---|---|
| `systemd-corporatetraveldc-web` | `localhost/corporatetraveldc-web:latest` | FastAPI API, published on 127.0.0.1:8000 and 100.x.x.x:8000; single uvicorn worker |
| `systemd-corporatetraveldc-poller` | `localhost/corporatetraveldc-poller:latest` | Async scheduler: 12 REST fetchers (`FETCH_SCHEDULE`), 11 in-process skills (`SKILL_SCHEDULE`; `pia-designator-watch` added 2026-11-02), watchlist sweeps |
| `systemd-corporatetraveldc-pusher` | `localhost/corporatetraveldc-pusher:latest` | ntfy sender; polls for unnotified events every 30 s (`PUSH_INTERVAL`) |
| `systemd-corporatetraveldc-ingest-{core,fdps,stdds,tfms,tbfm,itws,notam,tfdm}` | `localhost/corporatetraveldc-ingest:latest` (one image, 8 Quadlets) | One container per SWIM feed plus `core` (NWWS-OI + local airspace). `notam` runs the AIM/FNS SWIM feed. `tfdm` (added 2026-11-19) keeps the SCDS TFDM Terminal Publication subscription connected; it is opt-in (`SWIM_NMS_TFDM_ENABLED=true` only in its own Quadlet) and stamps `push:tfdm`. `AMTRAK_ENABLED=false` in every ingest Quadlet since 2026-09-24, so `ingest-core` no longer writes Amtrak. See `src/ingest/README.md`, `scripts/ingest-feed-ctl.sh` (which does not yet list `tfdm`). |
| `corporatetraveldc-pgsql` / `corporatetraveldc-pgsql-demo` | `docker.io/library/postgres:16-alpine` | The live database (127.0.0.1:5432) / the separate demo instance (127.0.0.1:5433) |
| `systemd-amtrak-tracker` | `localhost/corporatetraveldc-amtrak-tracker:latest` | Amtrak push writer (api.amtraker.com), stamps `push:amtrak` |
| `systemd-corporatetraveldc-runner` | `localhost/corporatetraveldc-runner:latest` | Ops dashboard SPA + API, port 8001 on 127.0.0.1 and the tailnet IP, tailnet-only |
| `systemd-corporatetraveldc-runner-demo` | same runner image | Demo playback, host port 8005 → container 8001, `DISPATCH_BASE_URL=http://100.x.x.x:8004` |
| `systemd-corporatetraveldc-demo` / `systemd-corporatetraveldc-demo-api` | `localhost/corporatetraveldc-demo:latest` | Recorder (writes `demo_snapshots` in Postgres) / read-only playback API on :8004 over ~~the scrubbed `demo-source.db`~~ the demo Postgres instance (2026-10-20) |
| `systemd-corporatetraveldc-execstandard-verifier` | poller image | `auth_request` verifier for the members site, 127.0.0.1:8787 |

### Auxiliary containers (same host)

SDR/RF: `corporatetraveldc-ultrafeeder` (ADS-B + tar1090; 100.x.x.x:8080
and :8081, Beast out on 0.0.0.0:30005, SBS on 127.0.0.1:30003),
`corporatetraveldc-acarsrouter` (0.0.0.0:9080 and :15555),
`corporatetraveldc-acarshub` (100.x.x.x:8092), `corporatetraveldc-dumpvdl2`,
`corporatetraveldc-acars-watcher` (UDP 5005), and the feeders
`corporatetraveldc-piaware`, `corporatetraveldc-fr24feed` (:8754),
`corporatetraveldc-planefinder` (127.0.0.1:30053),
`corporatetraveldc-airnavradar` and (added 2026-10-25; Quadlet tracked since
2026-10-20, active) `corporatetraveldc-opensky`. Disabled pending hardware, as
`*.container.disabled` files that create no unit: `acarsdec`
(`.config/containers/systemd/`), `dumphfdl`, ~~`ais`~~ `ais-catcher`
(corrected 2026-10-25: `systemd/quadlets/`), `ais-watcher`,
`utm-watcher` (`systemd/`). See `docs/SDR_SERVICES.md`.

Infra/comms: `ntfy` (0.0.0.0:2586), `systemd-corporatetraveldc-protonbridge`
(SMTP relay, 100.x.x.x:1025 → container port 25), `nextcloud-app`
(127.0.0.1:8090) + `nextcloud-db` (Postgres 16), `openwebui` (0.0.0.0:3000),
`rss-bridge` (100.x.x.x:3001), `csexec-contact` (website contact API,
127.0.0.1:8002), and two client-preview nginx containers,
`corporatetraveldc-ccw-demo` (:8085) and `corporatetraveldc-ccw-preview1`
(:8086), both on 127.0.0.1 and the tailnet IP.

Installed, not running (added 2026-11-22): five office-suite skeleton Quadlets in
`.config/containers/systemd/office-skeletons/` (`corporatetraveldc-firefly`, `-paperless`,
`-vikunja`, `-pgsql-office`, `-valkey-office`, networks `office-db` and `office-web`). systemd
generates their units, but on 2026-11-22 00:19Z none was started and no container ran.

### Scheduled jobs (user timers)

(added 2026-11-22) On 2026-11-22 00:19Z, `status.json` listed 84 `corporatetraveldc-*` user timers, all
active. Schedules are from the tracked `.timer` files. The host clock is UTC. Times marked ET are
`America/New_York` in the timer, so their UTC time moves with DST. Several timers add a
`RandomizedDelaySec`.

| Group | Timers, without the `corporatetraveldc-` prefix (schedule) |
|---|---|
| Briefs, watches, digests | `ops-brief` (hourly :05 ET), `ep-advance` (hourly :35 ET), `daily-opsplan` (07:00 ET), `freshness-audit` (06:00 ET), `aam-weekly-watch` (Mon 02:00 ET), `dispatch-desk-memo` (Mon 03:00 ET), `weekly-summary` (Mon 03:45 ET), `entity-tracking-digest` (00/06/12/18:12 ET), `quiet-window-report` (05:30 ET), `brief-fallback-monitor` (hourly :50), `claude-weekly-llama-fallback` (00/06/12/18:00Z), `maintenance-dispatch` (:00/:20/:40 ET; runs the rolling queue below) |
| Aviation data | `tbfm-arrival-enrichment` (every 15 min from :07), `airline-time-fidelity` (00/06/12/18:20Z), `convective-sigmet-archiver` (every 10 min from :04), `codeshare-seed` (04:40 ET), `faa-cifp-pull` / `faa-cifp-parse` (Thu 08:00 / 08:20 ET, `docs/CIFP_DATA.md`), `escalation-baseline` (Mon 09:20Z; rebuilds the time-of-day alert baselines with `scripts/build-escalation-baseline.py`), `pull-path-verify` (06:00 and 18:00Z) |
| Platform sentinels | `ground-stop-tripwire`, `plan-coverage-sentinel`, `artcc-sentinel`, `agent-lapse-sentinel` (see "Platform sentinels") |
| Feed and receiver health | `ingest-restart` (2 min), `ingest-feed-watch` (hourly :05), `swim-session-health` (5 min), `feed-db-integrity-check` (:26 and :56), `adsb-link-watchdog`, `adsb-feed-silence-watchdog`, `acars-feed-silence-watchdog`, `sdr-crashloop-guard`, `runner-health-watchdog` (5 min each), `ntfy-topic-count-watchdog` (every 15 min from :08) |
| Host, load and network | `thermal-ingest-guard` (120 s), `thermal-sample` (5 min), `container-mem-watch` (2 min), `tmp-headroom-guard` (5 min), `governor-watch` (6 h), `net-failover-watchdog` (every minute), `failover-kickover-guardrail` (5 min), `dns-anomaly-watch` (120 s), `dns-restart` (01:35 ET), `data-usage-snapshot` (00:05 ET), `uplink-reminder` (14:07Z), `healthcheck-ping` (5 min; the outside dead-man check), `nextcloud-health` (5 min), `llama-restart` (19:45 ET), `apt-snapshot-guard` (every 6 h at :17) |
| Integrity and supply chain | `integrity-sweep`, `website-integrity-sweep` (15 min each), `stack-refresh-tripwire` (hourly), `weekly-external-image-update` (Sun 04:15 ET), `podman-prune` (07:15 ET), `dependency-audit` (09:20Z), `sdk-attribution` (04:20 ET), `compliance-egress-push` (5 min) |
| Backups | `backup` (03:40Z), `backup-freshness` (01/07/13/19:15Z), `backup-restore-test` (Sun 05:30Z) |
| Second brain | `second-brain-rss` (every 2 h at :10 ET), `second-brain-daily` (23:45 ET), `second-brain-index-scan` (04:00 ET), `second-brain-demo-archiver-daily` (04:15 ET), `second-brain-dump-staleness` (09:20 ET), `second-brain-weekly` (Mon 04:30 ET), `second-brain-weekly-dump` (Sun 02:00 ET), `semantic-compile-daily` (02:52 ET, plus 06/12/18:02 ET), `personal-notes-import` (2 min), `personal-export-watch` (Sat 10:00 ET), `brain-capacity-review` (10:43Z; its service was `failed` on 2026-11-21) |
| Documentation drift | `docs-drift-daily` (Tue–Sun 09:00Z) and `docs-drift-weekly` (Mon 09:00Z), both `scripts/weekly-doc-drift-check.sh`: one repo's live facts and doc claims checked deterministically, with an optional local-model narrative; `claude-md-drift-daily` (09:15Z, `scripts/platform-drift-daily.sh`): drift backstop for the installed shared platform context |
| Agents and coordination | `agent-status-snapshot` (5 min; writes `/srv/ctdc-handoff/status/status.json`), `handoff-mirror` (every 15 min from :08, see "Key paths"), `research-board-mirror` (15 min), `board-sweep` (every 15 min from :06), `agent-timecard` (03:50Z), `agent-pamphlet-weekly-refresh` (daily 05:10 ET, despite the name), `claude-login-expiry` (13:00Z), `cowork-coord-24h` / `cowork-coord-7d` (every 24 h / 7 d after boot, up to 12 h random delay), `demo-source-refresh` (03:17Z) |

Rolling maintenance queue: nine oneshot jobs have no timer of their own. Every 20 min,
`maintenance-dispatch` (`scripts/maintenance-dispatch.sh`) checks whether the box is inside today's
quiet window. If it is, it starts the first job not yet run today, in the order of
`scripts/lib/maintenance-queue.txt`: `executive-protection-daily-watch`, `aam-daily-watch`,
`aviation-daily-watch`, `concierge-travel-daily-watch`, `gig-economy-daily-watch`,
`trains-yachts-daily-watch`, `disruption-weather-digest`, `transport-pattern-digest`,
`ep-advance-venues`.

Two `.path` units start jobs when their input changes, both active on 2026-11-22.
`semantic-compile-daily.path` fires when a Jarvis reconciliation note lands, in addition to its timer.
`knowledge-graph-compile.path` fires after a successful semantic compile.

Installed but disabled: `backup-direct.timer` and `backup-restore-test-direct.timer` (see Status,
Backups), `knowledge-graph-compile.timer` (its `.path` unit starts it instead), `retrofit-links.timer`,
and the template `client-demo-webdev-expiry@.timer`.

### Data feeds

The 21 rows `/api/v1/feeds` returned on 2026-11-21 (`push:tfdm` joined on 2026-11-19), with the
poller interval from `FETCH_SCHEDULE` (`src/poller/main.py`):

| Feed | Source | Interval | State at verification |
|---|---|---|---|
| `metar` | aviationweather.gov `/api/data/metar`, 7 stations (KDCA KIAD KBWI KFDK KHEF KJYO KGAI); since 2026-10-09 read from the deployment profile (airports with role `metar`), with that list as the fallback | 300 s | fresh | <!-- not-a-claim -->
| `nws` | api.weather.gov `/alerts/active` | 300 s | REST skipped while `push:nws` is fresh (push-covered) |
| `atcscc_opsplan` | ATCSCC | 3600 s | fresh |
| `runsheet` | local file | 300 s | fresh |
| `tfr` | `tfr.faa.gov/tfrapi/getTfrList` | 300 s | fresh; no push twin exists for TFRs |
| `nas` | `nasstatus.faa.gov/api/airport-status-information` | 300 s | fresh |
| `notam` | FAA NMS API (`api-nms.aim.faa.gov`); needs `NMS_API_CLIENT_ID` + `NMS_API_CLIENT_SECRET`, else `awaiting_credentials` | 300 s | REST skipped while `push:fns` is fresh (corrected 2026-11-22: REST row about 29 h old, `pull_state` `auth_gated`, one consecutive failure) |
| `dca_fids` / `iad_fids` | MWAA JSON endpoints | 300 s | fresh (600 s staleness threshold) |
| `amtrak` | api.amtraker.com (REST fallback for `push:amtrak`, wired 2026-09-06) | 300 s | ~~REST row 7.4 h old~~ (corrected 2026-11-22: REST row about 18 h old) because push owns the data |
| `eurocontrol` | EUROCONTROL NM B2B | 900 s | ~~`awaiting_credentials`~~ (corrected 2026-11-02: live `pull_state` `unconfigured`, credentials not set) |
| `jasdat` | JCAB/MLIT | 900 s | ~~`awaiting_credentials`~~ (corrected 2026-11-02: live `pull_state` `unconfigured`, credentials not set) |
| `push:fdps` `push:stdds` `push:tfms` `push:tbfm` `push:itws` `push:fns` | FAA SWIM NMS (Solace), ingest containers | push | ~~heartbeats 15–29 s old~~ (corrected 2026-11-22: 3–32 s old) |
| `push:tfdm` | FAA SWIM NMS TFDM Terminal Publication, `ingest-tfdm` container (added 2026-11-19, `e2fb66b0`; opt-in per container with `SWIM_NMS_TFDM_ENABLED`; departure stands and counts only) | push | 27 s old (3600 s staleness threshold) |
| `push:nws` | NWWS-OI (XMPP), ingest-core | push | ~~18 s old~~ (corrected 2026-11-22: 21 s old) |
| `push:amtrak` | amtrak-tracker container | push | ~~41 s old~~ (corrected 2026-11-22: 245 s old, inside its 660 s window; the writer stamps once per 300 s poll) |

Only `nws`, `notam` and `amtrak` have a push twin (`REST_FALLBACKS` in
`src/ingest/failover.py`). FDPS, STDDS, TFMS, TBFM and ITWS have no REST
fallback (operator decision 2026-09-06, `docs/REST_FALLBACK_AUDIT_2026-09-06.md`).
TFDM, added 2026-11-19, has none either.

**SWIM message census** (added 2026-11-22; since 2026-11-19, `src/ingest/census.py`, migration
`0089_swim_census_and_expiry`, `docs/SWIM_CENSUS.md`). Every ingest parser counts the message types it
sees, by hour, feed, type and whether it handled the type. Counts are written to
`swim_message_census` every 300 s. An unhandled type is logged as a warning once per feed, type and
hour. Above `SWIM_CENSUS_DROP_ALERT_PER_HOUR` (default 100) in one hour it raises one ops alert per
day. Capped raw samples have aircraft identifiers redacted and are kept 14 days. Since 2026-11-21,
`tbfm`, `stdds` and `fdps` also count messages and KiB per JMS destination, up to 300 destinations per
feed-hour (`SWIM_CENSUS_DEST_FEEDS`, `SWIM_CENSUS_DEST_KEYS`).

**Flight-row expiry** (`src/common/flight_expiry.py`, same migration). Every 30 min the poller marks
an `active` `flight_events` row `expired` when it has had no update for 3 h and its arrival is more
than 3 h past, for 6 h after it carried a position, or for 24 h in any case.
`db.get_flight_plan_by_callsign` never returns an expired row. A later message for the same GUFI
brings the row back.

### Push/pull failover

SWIM and NWWS ingest stamp `push:<feed>` every 30 s
(`INGEST_HEARTBEAT_INTERVAL_SECS`, default 30). Before a REST poll that has a
push twin, the poller skips the fetch if the heartbeat is younger than
`FALLBACK_MAX_AGE` = 90 s (`src/ingest/failover.py`); Amtrak overrides it to
660 s because its writer stamps once per 300 s poll. When ingest disconnects,
the heartbeat ages out and REST polling resumes.

### Network lockdown and the out-of-band channel

(added 2026-11-22) This is not the thermal LOCKDOWN below. `scripts/lockdown.sh` (root, installed
copy) removes the host-reach opt-ins of `corporatetraveldc-pusher` and `corporatetraveldc-acarshub`.
They can no longer reach ntfy or ultrafeeder on the host. No container or tunnel is stopped.
fail2ban triggers it, and the operator can also run it by hand. `scripts/restore-network.sh` reverses
it. Since 2026-11-18 a lockdown also opens a narrow management channel that only the operator can use
(`scripts/ctdc-oob.sh`, logic `scripts/lib/ctdc_oob.py`, public example config
`config/network/oob.example.json`; the deployment's file is private):

- Tailscale is pinned to the first configured uplink that has carrier and a default route.
- That link is shaped to 2048 kbit/s each way, with tailscaled in cake's priority tin. The aim is a
  usable SSH session down to 256 kbit/s.
- A soft data budget applies on metered links. Root's `corporatetraveldc-oob-budget.timer` checks it
  every minute.
- An sshd drop-in listens on the tailnet only and accepts pinned keys only, never root.
- GPG-signed break-glass commands are taken from `/var/lib/ctdc-oob/inbox/*.cmd.sig`
  (`corporatetraveldc-oob-breakglass.path`).

`corporatetraveldc-boot-guard.service` runs early at every boot. If the reboot was not made through
`scripts/ctdc-reboot.sh`, it writes a hold that makes the failover polkit rule refuse network changes
until the operator clears it. These are system units in `systemd/system/`. `status.json` does not show
them, so whether they are installed was not checked. See `docs/LOCKDOWN_OOB.md` and
`docs/LOCKDOWN_OOB_DEPLOYMENT.md`.

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
| **LOCKDOWN** | `load1 >= 40.0` (`THERMAL_GUARD_LOAD_LOCKDOWN`) | ~~everything except `web`: all six SWIM feeds~~ (corrected 2026-11-22: the six feeds in `ALL_SWIM_FEEDS` -- `fdps`, `stdds`, `tfms`, `tbfm`, `itws`, `notam`), `ingest-core`, `poller`, `pusher`, `runner` (`LOCKDOWN_USER_UNITS`, fixed in code). `web` is deliberately kept. Containers not on those lists keep running, among them `ingest-tfdm` (added 2026-11-19; the guard was last changed 2026-10-14), `amtrak-tracker` and `runner-demo` |
| Informational only | load1 15–40, CPU above 75 °C, or any count of load-attributed LLM fallbacks | nothing (temperature never sheds) |
| Restore | after a 300 s dwell, held only while `load1 > 30` (`THERMAL_GUARD_HOLD_LOAD`) or the CPU is above 75 °C (`THERMAL_GUARD_HOLD_TEMP_C`). Once the CPU has gone above 75 °C, the temperature hold clears below 74 °C (1.0 °C hysteresis). | the stack, in boot order |
| Dormancy | within 600 s of any restore, observe only, unless the CPU is at or above 79 °C (then the pass is evaluated; temperature still sheds nothing) | — |

The rollout's own hold-before-restart gate (`scripts/lib/load_gate.sh`, floor 15) is separate.

The guard never stops or starts the llama unit. A shed unit exits 0, so it
does not show in failure greps; restarting it by hand gets it shed again on
the next pass. The authoritative state:

```bash
cat /var/lib/corporatetraveldc/thermal_ingest_guard_state.json   # tier 0 at verification (also 2026-10-15 00:55Z, 2026-10-25 15:39:55Z after a LOCKDOWN/restore cycle 03:22-03:47Z, and 2026-11-22 00:03Z, last restore 2026-11-20 01:13Z via "external")
journalctl --user -u corporatetraveldc-thermal-ingest-guard --since "24 hours ago"
```

"Load-attributed LLM fallback" events are written by `_record_load_fallback()`
in `src/common/llm.py` to `/var/lib/corporatetraveldc/llm_load_fallback_events.jsonl`
for a busy slot (`OllamaBusyError`, a legacy class name) or a generation
timeout (any `httpx.TimeoutException`, `ConnectTimeout` included), never for a
connection error (`httpx.ConnectError` or another transport failure).

### Heavy-job admission

(added 2026-11-22) `corporatetraveldc-heavy-job-manager.service`, a long-running host user unit,
runs `scripts/heavy-job-policy.py manage`. It is a shared queue that runs at most two heavy oneshot
jobs at a time (`max_parallel` 2 in `config/heavy-job-policy.json`). A job's `ExecCondition` admits
it or queues it durably, so a queued job does not spend its run timeout waiting. Each job gets
2.5 GiB of RAM and starts with no swap. The first swap grant is 1 GiB, later grants are 256 MiB, and
a job gets at most 4 GiB. Total reservations stay within 60% of host swap. The manager never kills
other services. The registry has 26 member units (the industry watches, digests, Second Brain,
semantic and graph jobs, personal-notes import and the Llama backstop). `scripts/render-heavy-job-profiles.py`
renders their `90-heavy-job-policy.conf` drop-ins, which put them in `production-brain.slice`.
`scripts/heavy-lock-status.sh` shows the queue, and the `heavy_jobs` block of `status.json` mirrors
it. On 2026-11-22 00:19Z two jobs were active and two were queued. See `docs/BRAIN_CAPACITY_POLICY.md`.
This is separate from the single "heavy-job turn" that rollouts, stack refreshes and backups take in
`scripts/lib/heavy_lock.sh` (see "After any code change").

### Platform sentinels

(added 2026-11-22; since 2026-11-18) These checks run on the platform itself, so they keep working
when no agent is online. Entry point `scripts/platform-sentinel.py`, shared code
`src/common/platform_sentinels.py`, migration `0088_platform_sentinels`, `docs/PLATFORM_SENTINELS.md`.
Each run is fail-soft: an error is logged and the run exits 0.

| Timer (`corporatetraveldc-*`) | Schedule | Checks |
|---|---|---|
| `ground-stop-tripwire` | every 5 min | wide ground stops; GS/GDP on watched legs |
| `plan-coverage-sentinel` | daily 04:40Z | fleet-wide pre-departure FDPS plan coverage |
| `artcc-sentinel` | daily 05:10Z | first FDPS message from each centre |
| `agent-lapse-sentinel` | every 30 min | silent agent accounts: nudge at 6 h, email at 12 h, escalate at 24 h. The operator dismisses one with `scripts/ctdc-agent-lapse.sh` or from `/console` |

---
## API

| Endpoint | URL | Notes |
|---|---|---|
| Ops dashboard (runner) | `http://100.x.x.x:8001` / `https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net` | Tailnet only |
| API (public) | `https://dispatch.example.com` | Cloudflare Access; always Tier 0 |
| API (tailnet) | `http://100.x.x.x:8000` | Bearer-token tier resolution |
| Public demo | `https://dispatch-runner.example.com` | Password-gated (`DEMO_MODE=true`); ~~replays the 2026-08-14 snapshot~~ (superseded 2026-10-25: replays a 14-day loop (`LOOP_DAYS = 14`, ~~`src/demo/demo_api.py:46`~~ `src/demo/demo_api.py:78`, corrected 2026-10-25) from the demo Postgres instance refreshed nightly since 2026-10-19, see "Demo mode") |
| Agent gateway | `https://agents.example.com` | OAuth 2.1 + remote MCP, per-agent slugs |

### Route inventory

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

### Tier 0 — anonymous (selection)

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Health + snapshot age + active token count + CPS |
| GET | `/api/v1/feeds` | Feed freshness + error state (~~20~~ 21 feeds, live count 2026-11-22 00:04:48Z) |
| GET | `/api/v1/cps` | Critical Predictability State |
| GET | `/api/v1/tfr` | Active TFRs (63 on 2026-11-22 00:04:48Z) |
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
`/api/v1/knowledge-graph/{html,meta}`, `/api/v1/vault/file`, starting a
watchlist session (`POST /api/v1/watchlist`; ~~`GET` and `DELETE /api/v1/watchlist/{session_id}`~~
corrected 2026-11-22: the session `GET`/`DELETE` handlers in `main.py` were removed as dead
duplicates on 2026-10-15; `GET /api/v1/watchlist` is the router's entry list below and
`DELETE /api/v1/watchlist/{entry_id}` is admin, `watchlist.entry.remove`),
the watchlist entry reads in `routes/watchlist.py` (`GET /api/v1/watchlist`
entries, `/history`, and (added 2026-10-25) `/registrations`), plus
`GET /api/v1/flights/{ident}/assignments` and `/api/v1/flights/{ident}/{date}/assignments`
in `routes/assignments.py` (added 2026-10-25), and `POST /api/v1/withheld/pair` and
`/api/v1/withheld/screen` in `routes/withheld.py` (added 2026-11-02; AST at `703d8b70`:
17 `require_tier(Tier.T1)` dependencies).

### Tier 2 — `shares` bearer token

`GET /api/v1/cui/status` (audit-logged).

### Admin — `admin` bearer token

`/admin/{healthz,feeds,audit,tokens,version,triggers,watchdog/status}`,
`POST /admin/refresh-feed/{feed_name}`, `POST /admin/force-recompute-cps`,
`POST /admin/force-opsplan-snapshot`, `POST /admin/force-osint-scrape`,
`POST /admin/push-alert` (alias `/admin/push-test-alert`),
`GET/POST /admin/vip`, `DELETE /admin/vip/{entry}`, `POST/DELETE /admin/bandwidth-priority`,
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

~~30 route decorators in `src/runner/main.py` (32 method+path pairs; the
dispatch proxy takes GET/POST/DELETE).~~ ~~(corrected 2026-10-25, AST 15:41:53Z:
33 route decorators (24 GET, 5 POST, 2 DELETE, 1 PUT, 1 `api_route`), 35
method+path pairs; the dispatch proxy takes GET/POST/DELETE.)~~ (corrected 2026-11-21:
37 route decorators (26 GET, 7 POST, 2 DELETE, 1 PUT, 1 `api_route`), 39
method+path pairs; the dispatch proxy takes GET/POST/DELETE.) Sensitive surfaces (admin proxy,
non-GET `/api/v1/*` proxy, chat history, `PUT /api/v1/config`) are gated by
`_is_trusted()` (Tailscale CGNAT 100.64.0.0/10, RFC1918, loopback;
`CF-Connecting-IP` used exclusively when present). In demo mode an untrusted
origin also needs the `ctdc_demo_session` cookie.

Routes: `/healthz`, `/api/whoami`, `/api/demo/{login,status,webhook-log}`,
`/api/withheld/pair` (GET/POST) and `POST /api/withheld/forget` (withheld-category
device passes; added 2026-11-02),
`/api/adsb/{local,live}` (both local receiver only), `/api/{vdl2,acars,hfdl}/messages`,
`/api/ais/vessels`, `/api/utm/drones`, `POST /api/ask` + `GET/DELETE /api/chat/history`
(Dispatch Drawer chat via llama-server), `/api/dispatch/{path}` (proxy to
:8000 with Tier-1 token injection for an allowlist, see
`docs/auth-token-proxy-pattern.md`), `/api/stream` (SSE),
`/api/ntfy/stream`, `GET/PUT /api/v1/config`, `/api/v1/frontend-config`,
the map (`/api/map/aircraft`, `/api/map/scope`, `/api/map/focus`; added
2026-10-25, `docs/MAP.md`) and its same-origin OSM tile cache
(`/tiles/osm/{z}/{x}/{y}.png`; added 2026-11-04),
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

### Withheld categories (passes)

(added 2026-11-22; since 2026-11-02, `src/common/withheld.py`, `src/web/routes/withheld.py`, migration
`0085_withheld`) LADD aircraft, privileged trains and privileged vessels are *withheld*. A device without
a pass sees counts only. A pass covers one device and the categories it names, for at most 12 h. A master
token covers every category, present and future. Only the operator issues them, over SSH with
`scripts/withheld.sh`, after an SSH-signed approval. A master also needs the operator's GPG signature,
and any kill or lockdown wipes it. `withheld.sh` prints a one-time pairing code that is valid for 10
minutes, and the device enters it at the runner's `/api/withheld/pair`. `withheld.sh off` is the kill
switch: it freezes every category and revokes every pass.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/withheld/pair` | Tier 1: exchange the pairing code for a pass or master token |
| POST | `/api/v1/withheld/forget` | Revoke the presented token |
| GET | `/api/v1/withheld/whoami` | What the presented token reveals now |
| POST | `/api/v1/withheld/screen` | Tier 1: which items the caller may not see (for the runner's local receivers) |

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
`GET /admin/approval-requests/{request_id}/resolve` (`src/web/main.py:2889`; was ~~`:2804`~~, ~~`:2821`~~, ~~`:2823`~~, ~~`:2878`~~ (corrected 2026-11-21), line corrected 2026-10-15, 2026-10-25 and 2026-11-02).
It requires the per-action key `k` carried only in the operator's push
(wrong or missing key = 403), and since 2026-10-04 it can only **deny**:
`action=allow` always returns 403. Allowing takes a human SSH signature over
the exact request (`POST /api/v1/approvals/{id}/resolve`, `scripts/approve.sh`).

### Agent accounts and tooling

(added 2026-11-22) The design is in `docs/AGENT_SEGMENTATION.md`, signing in `docs/BOARD_SIGNING.md`
and personal API grants in `docs/PER_AGENT_API_ACTIONS.md`. Each account's identity, kind and role,
repositories, grants and switch-off rules are rendered into its own pamphlet from
`scripts/agent-segmentation/onboarding-template.md` (`render-onboarding.sh`). The pamphlet, not this
README, is where an account looks up its role. On-box accounts hand work over through the shared `/srv`
folders listed under "Key paths". Cloud-only accounts use vault contributions instead.

| Script | Use |
|---|---|
| `scripts/board-sign.sh` | Sign a board or API request with the account's own key |
| `scripts/board-signer-ctl.sh` | Operator: show, activate or deactivate a signer |
| `scripts/kill-order.sh` | Signed kill order against an account |
| `scripts/team-liveness.sh` | Root: the liveness AND (login, signer, no revocation, no kill order) |
| `scripts/council.sh` | Request a council or arena (inert until a human signs it) |
| `scripts/approve.sh` | Human SSH-signed approval (operator only) |
| `scripts/skill-grants.sh` | List or apply per-account skill grants |
| `scripts/mint-agent-api-token.sh` | Operator: mint an account's personal, action-scoped API token |
| `scripts/with-dispatch-env.sh` | Load an env file verbatim for one command (never `source`) |
| `scripts/workspace-contribute.sh` | Create-only, attributed vault contribution |
| `scripts/handoff-put.sh` / `scripts/handoff-apply.sh` | Hash-pinned patch handoff; apply refuses any byte that does not match the hash |
| `scripts/agent-relink.sh ACCOUNT [--slug S] [--dry-run]` (one file per agent in `scripts/relink/`) | Operator: link or relink an on-box Claude Code agent to its own gateway connector at `/mcp/<slug>` |
| `scripts/agent-segmentation/` | Account plan, pamphlet rendering, secrets subsets, `/srv` layout and access (`srv-layout.sh`, `srv-access.sh`) |

---
## Watchlist system

Two tiers share one pipeline (`src/shared/watchlist.py`,
`src/shared/watchlist_README.md`):

**Permanent** — JSON files in `/opt/corporatetraveldc/watchlists/`
(`permanent_flights.json`, `permanent_trains.json`, `permanent_vessels.json`,
`permanent_drones.json`), re-read by `WatchlistFileWatcher` on a 60 s mtime poll.

**Transient** — added via `POST /api/v1/watchlist/{flights,trains,vessels}`
(admin tier; each route checks and audits its own action name, for example
`watchlist.flight.add`, so a personal action-scoped token works only for the
action it was minted for; batch adds are `POST .../flights/batch`,
`.../trains/batch` and `.../permanent/batch`, `src/web/routes/watchlist.py`).
Expire at `auto_remove_at`; when omitted, 6 h after scheduled
arrival for flights and vessels, 3 h for trains, else 24 h after the add.
The poller sweeps expiries every 60 s, flights every 120 s, trains and vessels
every 300 s (vessels need `AIS_AISHUB_ID`). The same loop (`WatchlistSweep`,
`src/poller/main.py`) cross-references local aircraft against the watchlist
every 60 s and, since 2026-11-19, closes never-updated `flight_events` rows
every 30 min (`src/common/flight_expiry.py`).

Four entry types: flight (callsign; marketed codeshare numbers resolve to the
operating flight via `db.resolve_operating_callsign`), train (Amtrak number),
vessel (MMSI) and drone (separate `uas_phase` columns, not OOOI). Events fire
dual ntfy pushes (domain topic + concise `dispatch`) with forward-only
content-hash dedup. The domain topics are `flight-alerts`, `train-alerts`,
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

OOOI phases `pre_departure → out → off → on → in` never revert. Same-phase
ties resolve by `_OOOI_SOURCE_PRIORITY` in `src/common/db.py`: ACARS (4) >
SMES (3) > TFMS / TFMS-airline (2) > TBFM (1) > ADS-B (0) > FIDS (−1).
(Added 2026-10-15: the FIDS entry only ranks a tie. The authority gate denies
every FIDS OOOI claim, "enrichment-only, never OOOI-authoritative",
~~`src/common/db.py:6167`~~ ~~`src/common/db.py:7051`~~ `src/common/db.py:7185`
(line corrected 2026-11-21; `_OOOI_SOURCE_PRIORITY` at `:7063`).)
Separate authority rules (2026-10-05): ADS-B never asserts ON or IN, and
airline-posted TFMS times are denied for OUT/OFF at an SMES origin and ON/IN
at an SMES destination (KDCA/KIAD/KBWI).

**Pending identity** (added 2026-11-22; since 2026-11-18, `src/shared/pending_identity.py`). A
flight watch added before the day's FAA plan exists is stored with hex and registration empty.
They are never filled from FIDS, an aggregator or another day's plan. It gets one p3 push when
created (queued for tail confirmation) and one p4 60 min before scheduled departure if still
unbound. If its ET date ends with no plan for that occurrence, it gets one final push and is
removed. Binding happens on the first contact for the same occurrence
(`shared.watchlist.resolve_flight_identity`). Since 2026-11-19, a TFDM Terminal Publication record can bind
a watched leg's tail before departure, ahead of SFDPS, when it is the same occurrence, the flight is
not airborne, and the FAA registry (and OpenSky, if it knows the registration) agree on the hex. A
disagreement binds nothing. After OFF the FDPS/TBFM/TFMS order is unchanged (`src/common/tfdm_authority.py`).
The FDPS pre-departure guardrail (`src/common/fdps_predeparture.py`) reports two gaps: no same-day plan
within 45 min of scheduled departure, and a flight airborne without one. It also checks the
codeshare partners' numbers, and it never binds an identity.

**Airline flight numbers in privacy checks** (added 2026-11-22; since 2026-11-20,
`src/common/privacy_jurisdictions.py`). A watch typed as a public IATA flight number reads through
`common.airline_codes.IATA_TO_ICAO` before the private-aircraft test, so a foreign airliner is no longer
withheld under its country's private-aircraft default. An unknown two-character prefix still reads as
private (fail closed).

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
| `<family>-alerts` + `<family>-<zone>` (zones zny/zdc/zid/zob/zatl/zhu/zla/zse) | Escalating TFMS/TBFM/FDPS/ITWS/AIM-FNS (`aim_fns-*`)/STDDS sector alerts, per-topic throttled. STDDS airports in the zdc/zny/zatl/zob zones also get their own airport topic (e.g. `stdds-dca`). | 2–4 |
| `ops-brief` / `ep` / `ep-advance` | Hourly briefs | 2–4 |
| `ops-health` | Freshness audit, watchdogs, thermal guard | 2–5 |
| `osint-alerts` | OSINT scope hits | 2–3 |
| `reservations` / `calls` | Inbound webhook events | 3 |
| `approval-gate` | Approval notices (sudo gate, token gate, council, connector link): deny link only; allowing is a signed `scripts/approve.sh` | 3–5 (corrected 2026-11-22: sudo gate, token gate and console sign-in 4, sudo requests that auto-qualify as high-risk 5; council and connector-link requests 3) |

Full catalog: `docs/ALERT_REFERENCE.md`; rationale: `docs/ALERT_ARCHITECTURE.md`.

**Push parity lock** (added 2026-11-22; since 2026-11-04). Every ntfy push goes through one gate,
`common.push_policy.decide()`, which returns fire or suppress and a priority. The registry of classes,
thresholds and verticals is `common.push_policy_registry`. Change detection is semantic, not textual.
Each slot's last fired values live in the Postgres table `push_policy_state` (migration 0087), shared
by every container, so a re-worded status, an unchanged TMI re-send or the same OFF seen by three
sources is pushed once. The gate fails open but never silently: an unregistered kind or a state-store
error is sent at the caller's priority and logged.

---

## CPS — Critical Predictability State

Part 135.609-informed go/no-go score from ceiling, visibility, wind,
precipitation (METAR), airspace (TFRs + static restricted areas) and GDP (NAS
programs). Output `GREEN/GO`, `YELLOW/MARGINAL`, `RED/NO-GO`. Computed by
`src/poller/skills/cps_recompute.py` every 3600 s from the poller's
`SKILL_SCHEDULE`, and on demand via `POST /admin/force-recompute-cps` (Admin).

Added 2026-10-15: the inputs also include SWIM (in the code since 2026-08-30).

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
- `corporatetraveldc-runner-demo` (:8005) reads demo-api. With
  `DEMO_MODE=true` it is password-gated (`POST /api/demo/login`, HMAC-signed
  `ctdc_demo_session` cookie, `src/demo/profiles.py`, 8 h default) with
  signals sanitized server-side. (Added 2026-10-15: the session profiles live
  in Postgres (`demo_profiles`, migration 0057, since 2026-09-20)~~, so both
  runner-demo and demo-api mount the `corporatetraveldc-pgsql-sock` volume
  and load `dispatch.env`~~. (corrected 2026-11-21: since relay 2,
  2026-10-19, `demo_profiles` is on the demo Postgres instance; both
  containers mount only `corporatetraveldc-pgsql-demo-sock` and load
  `demo-secrets.env` and `demo.env`, not `dispatch.env`.) Neither mounts the
  live state tree `/var/lib/corporatetraveldc`; runner-demo mounts the
  demo-only `/var/lib/corporatetraveldc-demo` in its place.)
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
- Public hostname `https://dispatch-runner.example.com`; no
  Cloudflare Access policy fronts it. Its nginx vhost exists only on the host
  (not tracked in any repo).

`GET /api/v1/demo/readiness` reports per-tier (2w/8w/12w/24w/36w/52w) archive
readiness.

---

## Second brain (vault)

(added 2026-11-22) The operator's knowledge store, also called "Jarvis". Documents live in Nextcloud
(`nextcloud-app` + `nextcloud-db`). `src/second_brain/` maintains an index over the vault and the
layers derived from it. Agents read it through `GET /api/v1/vault/research` (and `/research/list`)
and write only create-only, attributed contributions (`POST /api/v1/workspace/contribute`).

| Module | Role |
|---|---|
| `index_db.py` | Persistent index over the vault (`second-brain-index-scan`) |
| `scrub_gate.py` | CUI/PII scrub gate on every ingestion path |
| `remember.py` | The operator's manual capture into `01-Sources/manual/`, through the same scrub gate |
| `semantic/` | Semantic-layer compile (`semantic-compile-daily`, timer plus `.path`) |
| `knowledge_graph/` | `graph.json` / `vault-graph.html` build and the wikilink retrofit (`knowledge-graph-compile`, `retrofit-links`) |
| `client_entity_ingest.py`, `doc_generation.py`, `webdav_client.py` | Contact-export entity import; .docx/.pptx/PDF generation; shared WebDAV client |

Its scheduled jobs are in the "Second brain" row of "Scheduled jobs". The long ones run under
"Heavy-job admission". The weekly cloud dump and its local backstop are described under "Local LLM".

---

## Supported platforms

> Detail in **`docs/platform-compatibility.pdf` (private repository)**.
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
# corporatetraveldc-stack-boot-stagger, which brings up the 35 units in
# scripts/stack-boot-ctl.sh ORDER one at a time; was 32, then 34 (2026-10-25),
# corrected 2026-11-21: ingest-tfdm added 2026-11-19)
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

### Map

(added 2026-10-24) The runner's `/map` is one Leaflet aircraft map; the
vessel, train and drone tabs follow the same pattern. Full reference:
`docs/MAP.md`.

- **Default.** With only `ULTRAFEEDER_URL` and the receiver coordinates set,
  the map shows your own receiver (readsb / ultrafeeder `aircraft.json`) —
  a tar1090-style view of your own data, with tar1090's altitude colours.
- **Extend by configuration.** (A) link out to a public aggregator, hex-only
  (`?icao=<hex>`); (B) your own FAA SWIM subscriptions (next section) — with
  SWIM configured you get the same merged receiver + SWIM map this
  deployment runs internally, drawn from what the storage filter keeps
  (`src/ingest/parsers/geo_filter.py`: 250 nm around the primary airport
  plus the core-airport list; nationwide means widening that filter, a
  load and storage decision on small hardware).
- **Public-facing deployments.** Do not expose SWIM-fed positions publicly
  (a standard SWIM data agreement does not permit redistribution or public
  display; review your subscription's terms), and do not expose the exact
  receiver location (`READSB_RX_LOCATION_ACCURACY=0` or rounded,
  `SiteShow=false`, no heywhatsthat panorama, range outline off, strip
  `r_dst` / `r_dir` from public aircraft JSON). The runner already hands
  untrusted callers a public-airport placeholder instead of the receiver
  centre, and dispatch-web LADD-filters any untrusted caller of the SWIM
  position routes fail-closed.
- `TAR1090_URL` (`dispatch.env`, optional) adds an "Open full receiver map"
  link to the local tar1090 for trusted callers only.

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

Metered uplink (added 2026-11-22, `scripts/lib/heavy_lock.sh`): while the default route is the
metered hotspot, only the nightly backup and the restore test may take the heavy-job turn. Every
other heavy job (rollout, stack refresh, image pulls) waits for the primary uplink within its
`max_wait`, then gives up with "deferred: metered uplink". To let named jobs through for one run,
set `HEAVY_METERED_ALLOW` (comma-separated job names) in the operator's own shell. Nothing sets it by
default, and every use is logged on stderr.

Relay tooling (added 2026-11-22): before a relay is posted it passes `scripts/relay-lint.py`, and
`scripts/relay-preflight.sh` runs every check the relay will hit, in order, on the exact tree it
will ship. `scripts/relay-wait-load.sh` holds a heavy
step while load1 is 24 or more (`RELAY_MAX_LOAD`) and gives up after an hour. Agent patches arrive
hash-pinned through `/srv/ctdc-handoff` or `/srv/ctdc-dev/submissions` and are applied with
`scripts/handoff-apply.sh`.

Every `podman build` passes `--platform linux/<host arch>` and is followed by
`scripts/build/provenance.py record`, which refuses an image built for another
architecture. `scripts/build/build-policy.py` enforces both statically. Python
dependencies change only by editing a `*.in` intent file and re-locking with
`scripts/build/lock-python.py`. Detail and limits: `docs/REPRODUCIBLE_BUILDS.md`.

Image updates: `scripts/stack-refresh.sh --weekly` (timer
`corporatetraveldc-weekly-external-image-update.timer`, Sun 04:15 America/New_York: 09:15Z under EST, 08:15Z under EDT; corrected 2026-11-21, was "08:15Z" only) pulls
every external image, rebuilds every local image from signed HEAD, gates each
image, and restarts every container one at a time. A tripwire variant
(`corporatetraveldc-stack-refresh-tripwire.timer`, hourly with a random
delay) fires on a randomly drawn instant 36 h to 7 d after the last run,
audits "is what is running what was signed?" first, and holds everything on
any finding. Rollback is `podman tag <svc>:previous <svc>:latest` + restart;
`corporatetraveldc-podman-prune.timer` (07:15 America/New_York: 12:15Z under EST, 11:15Z under EDT; corrected 2026-11-21, was "11:15Z" only) prunes dangling images only.

**If you run a real ADS-B receiver, set your antenna's real coordinates**
(`ULTRAFEEDER_LAT`/`ULTRAFEEDER_LON`, plus the UltraFeeder Quadlet's
`READSB_LAT`/`READSB_LON`/`TAR1090_DEFAULTCENTERLAT`/`TAR1090_DEFAULTCENTERLON`).
Unset, the platform falls back to a DC placeholder and every distance,
range ring and MLAT position is wrong. The frontend reads coordinates only
from the runner: `/api/v1/frontend-config`, and on the map `/api/map/scope`'s
receiver centre first (real centre for trusted callers only; corrected
2026-11-21, `src/runner/frontend/src/components/aircraftMap/AircraftMap.jsx`)
(see `docs/GPS_COORDINATE_CONFIGURATION.md`).

### Signed-manifest coverage

| Surface | Checked |
|---|---|
| ~~39~~ ~~40 (corrected 2026-10-15)~~ 38 (corrected 2026-10-25: `grep -l verified-exec ~/.config/containers/systemd/*.container \| wc -l` = 38 at 15:39:53Z) Quadlets whose `Exec=` goes through `scripts/verified-exec.sh` (the 37 oneshot skills and demo-api) | at every start |
| `src/common/llm.py` (calls `scripts/verify-manifest.sh` before inference) | at inference |
| `corporatetraveldc-integrity-sweep.timer` (every 15 min) | whole tree |
| web, poller, pusher, ingest (all eight ingest units, ingest-tfdm included), runner, amtrak-tracker, acars-watcher, verifier, and (added 2026-11-21) demo, runner-demo, csexec-contact | **not at start** (their `CMD` launches the app directly); the image build gate and the 15-min sweep cover them |

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
# test_*.py files on 2026-10-15; 2,180 in 255 test_*.py files on 2026-10-25
# 15:39:53Z; 2,873 in 327 test_*.py files at 703d8b70, 2026-11-21 23:22Z;
# parametrised cases add more). Keep every
# tempfile and TMPDIR inside tmp_path (the /tmp headroom guard, 2026-10-14).
PYTHONPATH=src python -m pytest tests/ -x --tb=short
```

### Skill runtime rules

**SR-1** (`src/common/sr1_log.py`): call `log_usage()` in a `finally` block.
Logged to `/var/lib/corporatetraveldc/api-usage.csv`.

**SR-2** (`src/common/sr2_gate.py`): `check_gate()` before expensive work,
`commit_gate()` only after it succeeded. Hash content-bearing fields only.
On skip, `sys.exit(0)`. Support `--force`.

### Source layout (`src/`)

(added 2026-11-22)

| Package | Role |
|---|---|
| `web/`, `poller/`, `pusher/`, `ingest/`, `runner/`, `demo/` | The core containers (see Architecture) |
| `common/` | Shared library: database backend and migrations (`pg_schema/`), personas, flight identity, push policy, withheld categories, sentinels |
| `shared/` | Watchlist pipeline, RSS catalog |
| `auth/`, `ctdc_token/` | Tier resolution; the `ctdc-token` CLI |
| `second_brain/` | Vault index, scrub gate, semantic layer, knowledge graph (see "Second brain") |
| `amtrak_tracker/`, `acars_watcher/` | Amtrak push writer; ACARS watcher container |
| `ais_watcher/`, `utm_watcher/` | AIS and UTM watchers (Quadlets disabled pending hardware) |
| `executive_standard/` | Static renderer for the self-hosted Executive Standard mirror |
| `geo/` | Static DC airspace features (TFRs come from the feed) |
| `swim_test.py` | Standalone SWIM connectivity test |

`src/audit/` and `src/exporters/` hold only an empty `__init__.py`.

### Schema

Postgres DDL is the ~~73~~ ~~75 (corrected 2026-10-15)~~ ~~83 (corrected 2026-10-25, numbered to `0084`)~~ 88 (corrected 2026-11-21: numbered to `0089`; there is no `0080`) numbered files in `src/common/pg_schema/`, applied by
`scripts/pg-migrate.sh`. The legacy SQLite schema constants in
`src/common/db.py` (`SCHEMA` … `SCHEMA_V40`, ~~`SCHEMA_V43`~~ `SCHEMA_V45` (corrected 2026-10-25)) and
`src/common/db_swim.py` (`SCHEMA_SWIM_V41` … ~~`V46`~~ `V47` (corrected 2026-10-25)) remain for the rollback
backend; `init_db_all()` does not apply the `db_swim` versions. Additive only:
never drop or rename a column.

---

## Local LLM — llama.cpp

- One host process, `corporatetraveldc-llama.service` (user unit, tracked at
  `.config/systemd/user/`): `llama-server -m qwen3-4b-instruct-2507-q4_0.gguf
  --host 100.x.x.x --port 8093 -np 2 --kv-unified -c 12288 -fa on
  -ctk q8_0 -ctv q8_0 -t 2 -tb 2 ~~--cache-ram 0~~ --cache-ram 1024 --cache-reuse 256 (corrected 2026-10-25: changed 2026-10-19, `.config/systemd/user/corporatetraveldc-llama.service:78`) --no-webui`,
  `Slice=production.slice`, `CPUQuota=200%`, `MemoryLow=5120M`,
  `MemoryHigh=7168M`, `MemoryMax=8448M`, `MemorySwapMax=0`. The unit file sets
  `CPUWeight=9000`; a `systemctl set-property` drop-in under
  `~/.config/systemd/user.control/` overrides it to **10000** live (plus
  `IOWeight=10000`).
- `corporatetraveldc-llama-restart.timer` restarts it daily at 19:45 America/New_York, ~~23:45Z~~ (corrected 2026-11-21: 00:45Z while ET is on standard time, 23:45Z in daylight time; the last fire was 2026-11-21 00:45Z).
- Clients find it through `LLAMA_BASE_URL` (`http://100.x.x.x:8093` in
  `dispatch.env`); `OLLAMA_BASE_URL` is read only as a deprecated fallback
  (renamed 2026-10-05). `src/common/llama_pool.py` resolves one host/port
  for every tier (`HOT_PORT = CHAT_PORT = LLAMA_PORT`) and serialises
  long-running generations with a box-wide lock.
- Models on disk: `qwen3-4b-instruct-2507-q4_0.gguf` (loaded, chosen
  2026-09-21, `docs/MODEL_EVALUATION_2026-09-21.md`) and
  `phi3-mini-q4_0.gguf` (kept, not loaded).
- Per-skill behaviour is the ~~22~~ 23 (corrected 2026-10-25: `claude-weekly-fallback` added) personas in `src/common/personas.py`
  (`PERSONAS`); a skill's `corporatetraveldc-pi5-<task>:latest` model string
  maps to a persona via `persona_key_for()`. The ~~21~~ 23 (corrected 2026-10-25, `git ls-files | grep -cE '^corporatetraveldc\.'`)
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
(timer enabled, ~~Sun 02:00Z~~ Sun 02:00 America/New_York, ~~= 06:00Z in EDT (corrected 2026-10-25)~~ 07:00Z while ET is on standard time, 06:00Z in daylight time (corrected 2026-11-21; next fire 2026-11-22 07:00Z)) runs `claude --model opus -p …`
(~~`scripts/second-brain-weekly-dump.sh:139`~~ `scripts/second-brain-weekly-dump.sh:93`, corrected 2026-11-21) ~~over `CLAUDE.md` and Claude's
memory files~~ (corrected 2026-11-21: since 2026-11-01 it reads only the operator session's Claude memory files, with `/srv/ctdc-context/PLATFORM_CONTEXT.md` and the two canonical vault rules notes as instructions; it never resets `CLAUDE.md`, the shared context or `AGENTS.md`, and it resets the memory files only after a confirmed, retrievable vault write; since 2026-11-18 an empty memory directory counts as success with no model run) and writes the result to the vault. On a failed or unconfirmed
run it writes a failure marker
(`common.claude_weekly_fallback.mark_failure`, ~~`scripts/second-brain-weekly-dump.sh:162`~~ `scripts/second-brain-weekly-dump.sh:139`, corrected 2026-11-21).
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

---
## FAA SWIM / NMS credentials

Per feed key `<KEY>` ∈ FDPS, STDDS, TFMS, AIM, TBFM, ITWS, TFDM (AIM serves the
`fns` feed and heartbeat; TFDM, added 2026-11-19, is opt-in and runs only in
`corporatetraveldc-ingest-tfdm`, the one container that sets
`SWIM_NMS_TFDM_ENABLED=true`):

```
SWIM_NMS_USER_<KEY> / SWIM_NMS_PASS_<KEY> / SWIM_NMS_QUEUE_<KEY>
SWIM_NMS_VPN_<KEY>  (default: <KEY>, and an empty value also means the default; this deployment uses AIM_FNS for AIM)
SWIM_NMS_HOST_<KEY> (fallback SWIM_NMS_HOST, default tcps://ems1.swim.faa.gov:55443)
```

The ingest containers (core, the six per-feed containers and `ingest-tfdm`)
read these from `/etc/corporatetraveldc/svc/ingest.env` (generated from
`dispatch-secrets.env`). After a credential change, regenerate the scoped
file and restart only the affected feed:

```bash
scripts/ingest-feed-ctl.sh restart <feed>
scripts/ingest-feed-ctl.sh restart all --order=lightest-first --stagger=15
```

`ingest-feed-ctl.sh` knows `core` and the six feeds `fdps stdds tfms tbfm
itws notam` (`lightest-first` is the default order). It does not manage
TFDM: restart that one directly with
`systemctl --user restart corporatetraveldc-ingest-tfdm.service`.

Standing convention: a SWIM account in doubt is retired and replaced with a
fresh email and passwords. New-deployment access: `docs/DATA_SOURCES.md`.

Display scope (2026-10-24): stored FDPS / TAIS positions feed the runner's
aircraft map (`docs/MAP.md`) through `GET /api/v1/swim/positions`,
`/swim/scope`, `/swim/last-leg` and (added 2026-10-25) `/swim/resolve`,
`/swim/aggregator` (`src/web/routes/swim_positions.py`, five routes). (Defect
noted 2026-10-25, operator decision: `/swim/scope` reports `swim_enabled: false`
while the SWIM heartbeats are seconds old, because `_swim_enabled()` compares
`feed_name` against `("fdps","stdds")` but the ingest writes `push:fdps` /
`push:stdds`, `src/web/routes/swim_positions.py:68,162-172`. Still open
2026-11-22 00:10Z: the live route answered `swim_enabled: false`.)
A standard FAA SWIM data agreement does not permit redistribution or public
display of the data, so SWIM-fed positions stay on trusted (tailnet / LAN)
origins; a public-facing deployment that wants to show them must hold a tier
or agreement that allows it. Independently of the agreement, LADD/PIA
blocking has a federal-law basis (FAA Reauthorization Act of 2024, sec. 803):
~~any Tier-0 caller of those routes is LADD-filtered~~ (corrected 2026-11-21:
since the operator's 2026-11-02 decision tier no longer decides it.
`/positions` and `/last-leg` show a LADD-listed aircraft only to a caller
holding an aircraft pass or the master token; every other caller, Tier 1
included, has it withheld, and `/last-leg` answers a withheld leg exactly as
a miss), and a filter failure is a 503, never an unfiltered list.

---

## Key paths

| Path | Purpose |
|---|---|
| `/opt/corporatetraveldc/private/ctdi-dispatch-internal/` | This repo (`/opt/corporatetraveldc/ctdi-dispatch-internal` is a symlink) |
| `corporatetraveldc-pgsql` (volumes `corporatetraveldc-pgsql-data`, `corporatetraveldc-pgsql-sock`) | Live database |
| ~~`/var/lib/corporatetraveldc/corporatetraveldc.db`~~ | ~~Legacy SQLite path (`DISPATCH_DB`). **Present**: a 69,632-byte file last modified 2026-10-05 23:21Z (recreated by a CLI smoke test that used the wrong env-var name; see Findings in `CHANGES-core.md`). Not the live store.~~ (2026-10-20: destroyed by the 2026-10-19 relay. `DISPATCH_DB` has no default and is removed from `config/dispatch.env`, so nothing can re-create the file: `docs/POSTGRES_MIGRATION.md` §3.7.) |
| `/etc/corporatetraveldc/dispatch.env` | Non-secret config |
| `/etc/corporatetraveldc/dispatch-secrets.env` | Master secrets file (0600); source for the scoped files |
| `/etc/corporatetraveldc/svc/<svc>.env` | Per-service scoped secrets (web, poller, pusher, ingest, runner, demo, demo-archive, amtrak-tracker, acars-watcher, execstandard-verifier); ~~53~~ ~~54 (corrected 2026-10-15)~~ ~~52 (corrected 2026-10-25)~~ 53 (corrected 2026-11-22: top-level tracked Quadlets; 52 plus `ingest-tfdm`. The two files in `retired-20261020/` are not counted) Quadlets read one |
| `/usr/local/libexec/ctdc/` | Root-owned installed copies of root-run scripts |
| `/var/lib/corporatetraveldc/api-usage.csv` | SR-1 usage log |
| `/var/lib/corporatetraveldc/skill-state/` | SR-2 gate state |
| `/run/corporatetraveldc/triggers/` | Admin trigger files: the path inside web and poller, both of which mount the podman volume `corporatetraveldc-triggers` at `/run/corporatetraveldc`. Host-side writers (the failover kickover guardrail, since 2026-10-14) write into that volume's mountpoint, not into a host `/run` directory (added 2026-10-15) |
| `scripts/backup/`, `/etc/corporatetraveldc/backup/` | Backup scripts; passphrase file (operator-only) and settings. See `docs/BACKUP.md` (added 2026-10-15) |
| `/opt/corporatetraveldc/watchlists/` | Permanent watchlist JSON (a symlink to the repo's `watchlists/`) |
| `/var/lib/corporatetraveldc/models/` | GGUF models |
| `.config/containers/systemd/` (repo) → `~/.config/containers/systemd/` (live) | Quadlets (copied, not symlinked) |
| `/opt/corporatetraveldc/private/executivestandard-website/articles/` | Canonical Executive Standard articles |
| `scripts/stack-refresh.sh`, `scripts/serialized-rollout.sh` | Weekly/tripwire refresh; one-at-a-time rollout |
| `scripts/cf-dns-record.sh` | Cloudflare DNS upsert/delete with the local management token (no dashboard changes) |
| `scripts/gui-window.sh` | On-demand headless maintenance desktop over SSH tunnel |
| `/srv/ctdc-handoff/` | (added 2026-11-22) Shared drop for on-box accounts (`root:ctdc-dev`, sticky): each account writes its own `<name>-from-<account>/` entries and nobody overwrites another's. `research-intake/` is the one bilateral folder, with cloud agents' vault contributions mirrored into `research-intake/from-vault/`. `status/status.json` is the scrubbed live status, rewritten every 5 min by `corporatetraveldc-agent-status-snapshot`, with states, counts and names only |
| `corporatetraveldc-handoff-mirror` (`scripts/handoff-mirror.py`) | (added 2026-11-22) Every 15 min from :08: copies `.md`/`.txt` drafts from `/srv/ctdc-handoff` into the vault at `01-Sources/personal-notes/Series/handoff/from-box/<date>/`, scrub-gated and create-only, with a `CURRENT.md` pointer. Copies cloud agents' contributions back into `research-intake/from-vault/`. Patches and binaries stay on the box, listed by sha256. Never deletes either side. `docs/HANDOFF_MIRROR.md` |
| `/srv/ctdc-dev/` | (added 2026-11-22; provisioned 2026-11-20) Development relay: `submissions/` (hash-pinned patches) and `relays/` (review records), both sticky; `checkouts/` is operator-owned and team-readable. `docs/DEV_RELAY.md` |
| `/srv/ctdc-context/PLATFORM_CONTEXT.md` | (added 2026-11-22) Installed shared platform context, from `config/platform-context.md` (`docs/SHARED_PLATFORM_CONTEXT.md`) |
| `/srv/ctdc-{claude,ea,cowork,llama,dispatch,grok,grokbot,…}/` | (added 2026-11-22) Per-account private folders, each shared only by that account and the operator. Any other access is by the operator's directive or a council's, recorded with `scripts/agent-segmentation/srv-access.sh`. `/srv/ctdc-blog/` is operator-only. `/srv/ctdc-publish-drop/` and `/srv/ctdc-publish-research/` are retired and read-only |
| `scripts/ladd-import.sh` | (added 2026-11-22) Operator-run LADD filter import (`docs/LADD_CUI_HANDLING.md`). It encrypts the RAM intake with a per-run key, imports in the poller container and runs an exhaustive check. Then it crypto-erases the intake and writes the `ladd-source-deleted` (and optional `ladd-supersede`) ledger entries. Counts and hashes only, never a value |
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
  still carries 2 identifiers in its history: open operator item) (unverified
  2026-10-25: the public-mirror and archived-repo histories are off-box and
  were not re-scanned in this pass)
  (`docs/security-reviews/2026-10-08-07-agents-and-adversarial-receipt.md`).

---

## Reservation system integration

Credential-gated inbound webhooks (`/webhooks/limoanywhere/reservations`,
`/webhooks/ringcentral/events`, `/webhooks/3cx/events`), or the watchlist API
directly:

```
POST /api/v1/watchlist/flights          (admin-tier token scoped to watchlist.flight.add)
{"identifier": "UAL2341", "origin": "KORD", "destination": "KDCA",
 "auto_remove_at": "2026-07-01T22:00:00Z", "notes": "pickup"}
```

Trains: `POST /api/v1/watchlist/trains` (scope `watchlist.train.add`) with the
bare train number. Full contract: `docs/INTEGRATION_SPEC.md`. Permanent
entries: edit the JSON files in `/opt/corporatetraveldc/watchlists/`.

---

## License

**Business Source License 1.1** (source-available, not OSI open source).
Licensor CS Executive Services, LLC; Licensed Work "Corporate Travel Dispatch
Intelligence (CTDI), version 2026.08"; Change Date 2030-08-24; Change License
GPL v3 or later. Full text and the Additional Use Grant: [`LICENSE`](LICENSE).
Summary, not a substitute:

- Free for non-production use.
- Free for personal self-hosted production use, or as an internal relay
  inside an organisation, provided it never serves a fee-based product or
  service to a third-party client (see the grant's carve-outs).
- Reselling, hosting, white-labeling, embedding in another commercial
  platform, or use in any fee-based client service needs a commercial license.

> The Additional Use Grant language is a working draft under legal review.
> Confirm current terms with CS Executive Services, LLC before relying on it.

---

---
## Superseded (kept for the record)

The superseded sections (the pre-2026-10 install, run and architecture notes, kept for the record) moved
verbatim to [`docs/legacy/README-superseded-2026-11.md`](docs/legacy/README-superseded-2026-11.md) on 2026-11-21.
