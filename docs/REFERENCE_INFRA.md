# CTDI — Infra Map (public reference)

> Generalized, sanitized template of a private document (`docs/INFRA_MAP.md`):
> the same architecture with no real domains, addresses, accounts, repo paths
> or credentials. Placeholders: `dispatch.example.com`-style hostnames,
> `192.0.2.10` for the node's tailnet address, `<prefix>` for unit names.
> Verified against the private source on 2026-10-06 22:45Z / 18:45 ET
> (private source verified against HEAD 2c3f81b and live state at 18:25Z / 14:25 ET;
> the reference deployment is one Raspberry Pi 5).

---

## 1. What this is

**CTDI (Corporate Travel Dispatch Intelligence)** — a real-time executive
travel intelligence platform. Monitors commercial flights (FAA SWIM), trains,
weather and airspace for a configured metro area; pushes ntfy alerts; serves
a tiered REST API, an OAuth 2.1 remote-MCP gateway for AI agents, and
(optionally) the access gate for a members-only publication site.

Runs on a single host as rootless Podman containers under systemd `--user`
Quadlets, owned by one unprivileged operator account. Reference host: a
Raspberry Pi 5 (16 GB, 4 cores, no GPU, `aarch64`), Fedora 44, SELinux
enforcing, NVMe boot. Host clock is **UTC**; units that need wall-clock
semantics say `America/New_York` explicitly (§4.7).

See `docs/REGIONALIZATION.md` for retargeting to another metro area.

---

## 2. Repo topology

Each public repo below has a private `-internal` working twin; public mirrors
are produced by a scrub-and-push script (`scripts/push-public.sh`) that
substitutes placeholders and drops operator-only files.

| Repo | Purpose |
|---|---|
| `ctdi-dispatch` | The platform: web API, poller, pusher, ingest, watchlists, auth, CPS scoring, ops dashboard ("Runner"), agent gateway, operator console. |
| `ctdi-dispatch-self-managed` | Thinner self-managed deployment variant. |
| `pihole-unbound-selinux` | Host layer: Pi-hole v6 + Unbound recursive resolver + SELinux hardening. |
| `corporatetravel-pi-fullstack` | Bundler: clones the host layer and the platform as submodules; one script installs both. |
| Website repos (one per public site) | Static sites; each owns its own nginx vhost(s). The publication site's repo owns the members-gate and invite vhosts. |
| `agentic-management-tooling-mcp` | Standalone MCP server: vendor-agnostic agentic safety-rail primitives plus flight/train/weather tools. Needs no dispatch platform. |
| `corporatetravel-dispatch-mcp` | **Archived.** Former MCP server behind OpenAPI bridges; retired 2026-08-18, replaced by the built-in agent gateway (§9). |

---

## 3. Deployment topology

- **Uplink**: Wi-Fi primary plus a cellular USB-Ethernet backup, selected by
  route metric and an active failover watchdog (§3.1). There is no bond.
- **Public ingress**: a Cloudflare Tunnel (`cloudflared` as a **user** unit,
  `--protocol http2`, config file tracked in the repo and diffed against the
  live copy) to nginx on the host. Every public hostname routes to nginx
  `127.0.0.1:80`; the catch-all is `http_status:404`. Identity-aware access
  sits in front of the API hostname with narrow path-scoped bypasses. No
  tunnel or access setting is managed in the vendor dashboard by hand: all
  changes go through local scripts.
- **Host DNS**: Pi-hole FTL (DNS :53, web UI on a local port) → Unbound
  (127.0.0.1:5335, recursive, DNSSEC, no forwarders). Pi-hole, Unbound and
  `tailscaled` get top CPU weight.
- **Time**: chronyd serves NTS (:4460) with a tailnet-issued certificate,
  refreshed by a root timer.
- **Overlay network**: Tailscale (Tailscale SSH, Tailnet Lock enabled; the
  node is also the tailnet DNS resolver). Private access to the ops dashboard
  and the operator console. Network origin alone grants no API tier. Access
  model: `docs/HEADLESS_ACCESS.example.md`.
- **LLM**: one llama.cpp `llama-server` as a host user unit, bound to the
  tailnet address on :8093 (`LLAMA_BASE_URL`); one small instruct model
  (4B, 4-bit), two slots, `CPUQuota=200%`, a memory cap, restarted daily.
  Exposes an OpenAI-compatible API; nothing else serves inference.
- **Systemd watchdog**: hardware watchdog via `RuntimeWatchdogSec`, set from
  measured scheduling stalls (a stall monitor records every ≥ 1.5 s stall; a
  one-shot tune timer derives the value as 1.2 × worst stall, floor 30 s,
  cap 180 s).

### 3.1 Uplink failover

A user timer runs `scripts/net-failover-watchdog.sh` every 60 s. It probes
upstream **through each interface by IP** (ICMP, then TCP:443 fallback —
never a hostname, which would fail during the outage being detected).
After 3 consecutive primary failures **while the backup is healthy** it
lowers the backup connection's route metric below the primary's; after 3
consecutive good primary probes it restores the saved metric. Both down =
alert only, no route change. NetworkManager's own connectivity probe stays
disabled (it only reports state and misfired before the resolver was up).
DNS needs no special handling: the local recursive resolver follows the
current default route. The failover is not gated on the thermal guard
(§4.1): load-shedding sheds services, never routes.

### 3.2 Proposed, not built

A second node for inference on a point-to-point link with standby DNS.
Nothing is live.

---

## 4. Service map

Reference figures: 35 containers running; 76 tracked `.container` Quadlets
(38 long-running, 38 oneshot). Ports below bind to `127.0.0.1` unless noted;
"tailnet" means the node's tailnet address (e.g. `192.0.2.10`).

**Core spine**

| Service | Port(s) | Role |
|---|---|---|
| `web` | 127.0.0.1 + tailnet :8000 | FastAPI tiered REST API (~126 routes), agent gateway, operator console. One uvicorn worker. |
| `poller` | — | Async scheduler: 12 REST fetchers, in-process skills, watchlist sweeps, admin trigger directory |
| `pusher` | — | ntfy sender; polls the DB every 30 s for unnotified events |
| `ingest-core` | — | NWS push (NWWS-OI) + local airspace from own RF receivers |
| `ingest-{fdps,stdds,tfms,tbfm,itws,notam}` | — | One SWIM push feed per container (`notam` = AIM/FNS). Any one restarts without dropping the rest. Load-shed under pressure (§4.1). |
| `pgsql` | 127.0.0.1:5432 + shared socket volume | PostgreSQL 16, the live database (73 numbered migrations) |

**Feeds / members gate / runner / demo**

| Service | Port | Role |
|---|---|---|
| `amtrak-tracker` | — | Rail push writer |
| `execstandard-verifier` | 127.0.0.1:8787 | nginx `auth_request` verifier for the members site + invite host backend (poller image, own scoped env) |
| `runner` | 127.0.0.1 + tailnet :8001 | Ops dashboard; tailnet-only |
| `runner-demo` | 127.0.0.1 + tailnet :8005 → 8001 | Public demo runner; `DEMO_MODE` (off by default) enables an app-layer password gate |
| `demo` | — | Recorder → Postgres `demo_snapshots` every 300 s |
| `demo-api` | 127.0.0.1 + tailnet :8004 | Playback API over a scrubbed, promoted SQLite copy (§4.3) |

**SDR / RF (optional, hardware-dependent)**: an ADS-B aggregator
(`ultrafeeder`: tailnet :8080/:8081, Beast :30005, SBS on localhost :30003),
an ACARS/VDL router (:9080, :15555) and hub (tailnet :8092), `dumpvdl2`, an
ACARS watcher (UDP 5005; local + two network ACARS sources), and four
third-party feeder clients (each with its own `*-secrets.env`). Further
decoders (`acarsdec`, `dumphfdl`, AIS, UTM) ship as `*.container.disabled`
until the hardware exists.

**Collaboration**: Nextcloud app (127.0.0.1:8090) + its own Postgres on a
dedicated podman network; Open WebUI (:3000) pointed at llama.cpp's
OpenAI-compatible API.

**Comms / misc**: `ntfy` (:2586); a local IMAP mail bridge (tailnet :1025);
outbound mail from ntfy and the website contact API goes through a provider's
SMTP submission port (587); website contact API (127.0.0.1:8002); RSS-Bridge
(tailnet :3001); small nginx containers for client previews.

**Host-level (not containers)**: nginx (80/443, system unit), cloudflared
(user unit; metrics on localhost), tailscaled, Pi-hole FTL, Unbound, chronyd,
fail2ban, the llama unit, and the root units in §4.4.

### 4.1 SWIM ingest liveness — load-shedding is normal

A thermal/CPU guard (`scripts/thermal-ingest-guard.py`) runs every 120 s and
**stops containers under pressure, restarting them when the box recovers**:

| Trip | Condition | What is shed |
|---|---|---|
| Temp, mild | CPU ≥ 74 °C | the two heaviest SWIM feeds (`tfms`, `stdds`) |
| **Lockdown** | CPU ≥ 79 °C **or** 1-min load ≥ 40 | all six SWIM feeds, core ingest, poller, pusher, runner. `web` survives; the LLM server is never touched. |
| Informational | 70–74 °C, load 15–40, or any LLM-contention fallback count | nothing |
| Restore | < 65 °C **and** load < 20 (half the lockdown trip), held 300 s | in boot order; each restarted feed is re-checked and a failed one raises a p5 `RESTORE FAILED` |
| Dormancy | 600 s after any restore: observe only (≥ 79 °C still trips) | — |

History, from the reference deployment's operating data: a two-stage load
ladder (trip 10/14, restore < 6) was replaced 2026-08-23 because every real
trip had been load-driven and normal full-stack load (5–7) sat inside the
old restore bar; an LLM-contention lockdown trigger was demoted to
informational 2026-08-27 after a night of false trips; the restore bar moved
from a fixed 15 to half the trip on 2026-10-03 because 15 sat below median
healthy load. If you adapt this guard, set the restore bar clearly above
your own measured busy-but-healthy load.

Two consequences:

- A shed unit exits 0, not failed — invisible to "any unit failed?" checks;
  restarting it by hand gets it shed again.
- The health endpoint can report `degraded` with stale push feeds during a
  shed. Check the guard's JSON state file and journal before suspecting
  credentials.

### 4.2 Signed-manifest gate

Every tracked file is listed in a GPG-signed manifest
(`MANIFEST.sha256{,.asc}`); the trust pin is a root-owned file under `/etc`
that is parsed, never sourced, and takes precedence over the in-repo pin.

| Surface | When checked |
|---|---|
| Oneshot Quadlets (+ `demo-api`) started via `Exec=scripts/verified-exec.sh …` | each start |
| `src/common/llm.py` | before inference (refuses if the verifier is missing) |
| Integrity sweep timer | every 15 min, whole tree (~1,250 files) |
| Long-running app containers (web, poller, pusher, ingest, runner, …) | at build: images are built only from a signed, clean tree and pass an in-image gate; the sweep covers the rest |

Any tracked edit left unsigned fails the sweep within 15 minutes. Deploy
order is always **sign → build → restart** (and for root-run scripts, sign →
install root copies → daemon-reload).

### 4.3 Demo pipeline

Recorder → Postgres `demo_snapshots` (every 300 s). The only bridge to what
the public demo serves is a scrub-and-promote script
(`scripts/scrub-demo-source.py`) on its own timer, producing a scrubbed
SQLite file the playback API reads. The demo is only as fresh as its last
promote; in the reference deployment that timer is disabled, so the demo
replays a 2026-08 window.

### 4.4 Root (system) units

| Unit role | Schedule | What it does |
|---|---|---|
| Watchdog | every 90 s | thermal/throttle/services/containers/API/feed freshness; alert-only for system services |
| Team liveness (+ `.path` on the kill-orders dir) | hourly | agent/human liveness dead-man switch and kill-order execution (§4.8) |
| Skill grants | hourly | materialise / claw back per-agent skill grants |
| Local-LLM council participant | hourly | runs as the LLM service account |
| NTS cert refresh | daily | chrony NTS certificate |
| Tailnet cert renew | daily | nginx tailnet HTTPS certificate; reload-or-restart nginx |
| Watchdog tune | once | set `RuntimeWatchdogSec` from stall data |
| Stall monitor | running | records ≥ 1.5 s scheduling stalls |

Root-run scripts execute from root-owned installed copies
(`/usr/local/libexec/<prefix>/`), installed by `scripts/install-root-copies.sh`
only after the manifest verifies and the bytes match the signed hash —
never from the operator's checkout.

### 4.5 Storage

- **Postgres** is primary (`DISPATCH_DB_BACKEND=postgres`). Containers reach
  it through a shared socket volume mounted at `/var/run/postgresql`; host
  processes use `127.0.0.1:5432`. Schema = numbered, additive migrations
  under `src/common/pg_schema/` (73 at this revision). SQLite remains as a
  rollback selector and for the demo playback copy.
- **Second-brain index** lives in the same Postgres since 2026-09-19 (§10).
- **Vault files** live in Nextcloud (WebDAV), not in the platform DB.
- **State/report files** live under `/var/lib/<prefix>/`, which is mounted
  into containers with `:z`. Rule learned the hard way: nothing under that
  tree may be unreadable to the operator, or podman's recursive relabel
  fails every container start. Root-only state (kill-order staging) lives in
  a separate tree outside every container mount.

### 4.6 Scoped secrets

First-party Quadlets each read a per-service file
`/etc/<prefix>/svc/<svc>.env` (oneshot jobs on the poller image share one
file, the ingest family shares one; web, runner, pusher, demo, rail tracker,
ACARS watcher and the members verifier each have their own). A generator
(`scripts/service-env/generate.py`) writes them from one root-only master file
against per-service allowlists = reviewed names ∪ the import closure of the
service's code. No Quadlet reads the master file. Third-party containers
have their own `*-secrets.env`. Scripts never put tokens or passwords on argv
(world-readable via `/proc`): headers go through private file descriptors and
env passthrough, enforced by a contract test.

### 4.7 Scheduling

63 active user timers in the reference deployment, in these groups:

| Group | Pattern |
|---|---|
| Briefs | hourly ops brief, hourly advance brief, fallback monitor |
| Rolling maintenance | a dispatcher every 20 min (+ random offset) starts enrolled long-runners (daily topic watches, digests) **one at a time** inside the day's drawn quiet windows; enrolled jobs have no timer of their own. A daily report builds a 30-day load profile and draws 3–4 two-hour windows per day (≥ 4 h apart, one overnight); a missing/stale profile falls back to a fixed overnight window. The operational day rolls at 05:00 local. |
| Weekly | Monday weeklies (watches, desk memo, summary, vault weekly) |
| Daily | usage snapshot, DNS restart, vault index scan, demo archiver, codeshare seed, freshness audit, ops plan, image prune, LLM restart, vault daily digest |
| Several per day | entity-tracking digest and semantic compile (every 6 h), RSS (2 h), pull-path verify |
| Every few minutes | thermal guard, memory watch, ingest restart, failover watchdog (60 s), SDR crash-loop guard, egress compliance push, Nextcloud and SWIM-session health, board sweep, integrity sweeps (platform and website), feed DB integrity check, … |
| Image lifecycle | weekly refresh (Sunday) + an **adversarial tripwire** (below) |

Long-runners share one `flock` with a bounded wait and an exit code (75)
that counts as success when the lock is busy. Timer zones: wall-clock jobs
carry an explicit `America/New_York`; a timer written without a zone follows
the host clock (UTC) — audit for that after any host time-zone change.

**Stack refresh** (`scripts/stack-refresh.sh`): `--weekly` pulls every
external image, rebuilds every local image from signed HEAD, gates each,
and restarts every container one at a time (even with no update, so
`StartedAt` timestamps become a tamper signal; a container left behind is a
finding). `--tripwire` runs from an hourly timer with no schedule of its own:
it fires only at a drawn instant in `[last + 36 h, last + 7 d]`, snapped to a
quiet window, with at least one never-used window per month — deliberately
unpredictable. Before touching anything it audits "is what is running what
was signed?" (manifest, tracked-vs-live units, running image IDs vs signed
`:latest`, external digests, untracked containers, authorized_keys/sudoers/
listen-port/unit-set fingerprints vs baseline). Any finding → p5 alert, exit
2, everything held. Held units (major-version bump, failed gate, dirty tree)
accumulate as review debt until restarted clean. Postgres image updates go
only through a dedicated safe-update script; `:previous` tags allow rollback.

### 4.8 Segmentation: humans, agents, services

- **Groups**: `<prefix>-dev` (read-only repo access for non-operators),
  `<prefix>-ops` (humans), `<prefix>-agents` (agent runtimes), with separate
  systemd slices and CPU weights for humans and agents.
- **Account kinds** (registry file under `/etc`): *agent* (login mode
  `claude` = vendor CLI credentials, or `ssh`), *service* (no login shell,
  no inbound key; e.g. the local-LLM participant, a cloud-hosted agent whose
  only credential is a scoped, labelled token), *preloaded* (created, not yet
  active). Naming convention `<prefix>-agent-<vendor>-<product>`.
- **One identity per account**: one inbound SSH key and one signing key,
  comment `account@node`, never shared; git author = the account.
- **Liveness is an AND**: login factor live (credentials refreshed / recent
  SSH login, per kind) AND active board signer AND no token revocation in
  24 h AND no executable kill order. Any factor fails → the account goes
  inert everywhere (sessions ended, units stopped, linger off, account
  expired, signer deactivated, all its tokens revoked). New accounts get a
  grace period; registry unavailable = hold, never kill.
- **Kill orders**: SSH-signed JSON orders; a member/agent/service target
  needs one admin or two distinct non-admins; an admin target needs a
  configurable quorum (2 or 3). The operator account is never a target.
- **Signed approvals**: state-changing approvals (sudo gate, council,
  workspace grants, console login, agent-gateway consent) are allowed only
  by a human SSH signature, from a separate passphrase-protected approval
  key, over the exact request; tap-through links can only **deny**.
- **Board**: a shared coordination board; posts are authenticated by key or
  by a per-account SSH signature over method/path+query/timestamp/body hash
  (single-use, 300 s window), with tiers up to signature + short-lived token
  and admin co-signature.
- **Shared workspace**: signed, scrub-gated, create-only contributions at
  server-chosen paths; agents draft, never publish, never approve.
- **Skill grants**: every signed skill is available to any agent by grant;
  deny wins; grants can expire or be scoped to a task; unsanctioned skills
  are quarantined.
- **Repo**: the operator's checkout is not group-writable; agents work from
  their own clones. Signing and sudo stay operator-only.

Detail: `docs/AGENT_SEGMENTATION.md`, `docs/BOARD_SIGNING.md`.

---

## 5. Data flow

```
FAA SWIM (push) ───▶ SWIM ingest ×6 ─┐  (load-shed under pressure — §4.1)
NWS push / local RF ─▶ core ingest ──┤
Rail push ──────────▶ amtrak-tracker ┤
REST fallbacks ─────▶ poller ────────┼──▶ PostgreSQL
                                     │        │
                         ┌───────────┴──┐     ▼
                         ▼              │   pusher ──▶ ntfy topics
                 web (REST API) ◀───────┘
               │        │          │
   runner (tailnet)  console    agent gateway (OAuth 2.1 + remote MCP)
               │    (tailnet)      │
             nginx ◀── tunnel ◀── public hostnames (§6)
                                   │
                    any MCP-capable agent, one connector per agent account
```

Only three REST feeds have a push twin (NWS, NOTAM, Amtrak); the poller
skips each while its push heartbeat is fresh (90 s; 660 s for Amtrak). FDPS,
STDDS, TFMS, TBFM and ITWS have no public REST equivalent.

---

## 6. Public surface (by category)

| Category | Placeholder hostname | Exposure / backing |
|---|---|---|
| API | `dispatch.example.com` | Behind identity-aware access (302 to login); narrow bypasses (`/robots.txt`, the board read, webhooks) reach the app. nginx → web :8000 and stamps `X-CTDI-Public: 1`, which pins the request to Tier 0. |
| Public demo | `dispatch-runner.example.com` | No access proxy; nginx → `runner-demo` :8005 with its app-layer password gate |
| Agent gateway | `agents.example.com` | nginx → web :8000, gateway paths only (`/` 404, OAuth metadata 200, `/mcp/*` 401 without a token). **Must not** sit behind the access proxy (vendor OAuth clients cannot complete an interactive login). |
| Members gate | `members.<publication>.example.com` | nginx static docroot + `auth_request` to the verifier :8787; not signed in → 302 to the invite host |
| Invites | `invite.<publication>.example.com` | nginx → verifier `/_invite` paths. Not behind the access proxy; logs never record the invite code. Invites are personal (revocable by the publisher) or short-lived capped promo codes; redemption is a 2-minute single-use hand-off to a `__Host-` session cookie; hashes only; per-member device cap. Routing is by proxied **path**, never by a header. |
| Website | `www.example.com` + apex | nginx static site + contact API |
| Vault (interactive) | `dav.example.com` | nginx → Nextcloud :8090 (web UI, WebDAV/CalDAV/CardDAV) |
| Vault (automation) | `cloud.example.com` | nginx → Nextcloud, vault WebDAV path only (`/` 404 by design) |
| Other services | `ntfy.`, `openwebui.`, `pihole.` `example.com` | nginx → services |
| Retired MCP host | `mcp.example.com` | tunnel ingress `http_status:404` |
| **Tailnet-only** | `<node>.<tailnet>.ts.net` | nginx 443 (tailnet cert) → runner :8001; `/console` → web :8000 (the public API vhost 404s `/console`; the app 404s it on any other Host). Not resolvable or reachable from the internet. |

**Operator console** (`/console`, tailnet name only): sign-in is an
SSH-signed approval bound to a browser-only nonce (completes on reload, so it
survives a backgrounded phone tab); 8 h session; CSRF tokens derived from the
session secret; hold and gateway-thaw actions remain signature-gated.

**Rate limiting / honeypot**: nginx rate-limit zones keyed on the real client
address (`CF-Connecting-IP`, not the tunnel's loopback peer — keying on the
peer makes one global bucket). Design rule: a rate-limit trip bans the client
only; it must never trigger a stack-wide lockdown (anyone could repeat it). Changing a zone's key needs an nginx
**restart**: a reload silently keeps the old config, and `nginx -t` passing
does not mean the reload applied. A honeypot path feeds fail2ban.

**Repo vs live**: nginx vhosts and the tunnel config are tracked and diffed
against live; every live vhost should be tracked in some repo.

---

## 7. Auth tiers

| Tier | Requirement |
|---|---|
| T0 | Anonymous; any request through the public vhost is pinned to T0 regardless of token |
| T1 | `cert` bearer token |
| T2 (SHARES) | `shares` bearer token, audit-logged |
| Admin | `admin` bearer token; every allowed and denied call is audited; a token can be scoped to a list of action names (`allowed_actions`, fnmatch) |

Token format `ctdc_<user>_<32 random chars>`; only a SHA-256 hash is stored.
Other credentials: webhook secret header; board key or per-account SSH
board signature; SSH-signed approvals (approvals, council, workspace, console
login, agent consent); OAuth 2.1 bearer (agent gateway). The approval
tap-through link takes no bearer, needs a per-action key carried only in the
operator's push, and can only deny.

---

## 8. Watchlist system and alerting

- **Permanent** entries: JSON files (flights, trains, vessels by MMSI,
  drones), re-read on change (60 s mtime poll) and merged into the DB.
- **Transient** entries: default expiry 6 h after arrival for
  flights/vessels, 3 h for trains, 24 h if no arrival is known; expiry sweep
  every 60 s, flights every 120 s, trains/vessels every 300 s.
- Every event fires two ntfy pushes: a domain topic and a concise `dispatch`
  summary. Dedup is forward-only by content hash.
- Flight tracking is local-first: own ADS-B receiver, SWIM flight data,
  locally imported registries, airport FIDS, schedule inference. OOOI phases
  never revert; same-phase source precedence ACARS > SWIM surface > SWIM
  traffic-flow (incl. airline-reported) > TBFM > ADS-B > FIDS. ADS-B never
  asserts landing/in-gate; SWIM beats airline-posted times. Marketing vs
  operating flight numbers resolve through a codeshare map. An optional
  FlightAware AeroAPI tier is dormant without a key.
- Escalating per-family, per-ARTCC topics (`tfms-*`, `tbfm-*`, `fdps-*`,
  `itws-*`, `aim_fns-*` × centre) with per-topic throttle/enable/sanitize via
  admin routes.

| Topic | Content | Priority |
|---|---|---|
| `tfr-alert` / `hot-alerts` | VIP/high-priority TFR, severe ops events | 5 |
| `flight-alerts` / `train-alerts` / `vessel-alerts` | Watchlist events | 2–5 |
| `dispatch` | Concise bottom line, all events | mirrors source |
| `cps` | Critical Predictability State changes | 3–5 |
| `wx-alerts` / `nas-alerts` | Weather / NAS program alerts | 2–5 |
| `ops-brief` / `ep` / `ep-advance` | Hourly briefs | 2–4 |
| `ops-health` | Freshness audit, watchdogs, thermal guard | 1–5 |

Catalog: `docs/ALERT_REFERENCE.md`; rationale: `docs/ALERT_ARCHITECTURE.md`.

---

## 9. MCP / agent access

The built-in **agent gateway** (since 2026-10-05; `src/web/routes/agent_gateway.py`):
OAuth 2.1 authorization-server metadata, dynamic client registration (and
client ID metadata documents), authorize/token/revoke endpoints, and a remote
MCP endpoint per agent connector (`/mcp/<slug>`, one slug per agent
account). Loopback `http` redirects are accepted for native CLI clients
(RFC 8252); everything else is `https`. Consent for each connection is the
operator's signed approval. Vendors renew (1 h access tokens, rotating
refresh), but the platform side is authoritative: an inactive signer, a
revocation or a kill order ends the chain; an operator dead-man (14 days)
refuses without revoking; a signed hold (≤ 90 days) keeps a grant alive.
Kill switches: per account, plus one command
(`scripts/agent-gateway.sh kill-all`) that revokes every connection,
disables every connector and takes the gateway hostname down; `thaw` is
signature-gated.

The earlier arrangement — `corporatetravel-dispatch-mcp` behind two OpenAPI
bridge processes — was retired 2026-08-18 and the repo archived.
`agentic-management-tooling-mcp` remains an independent, standalone project.

---

## 10. Second-brain vault & multi-agent coordination (optional layer)

Not required for the platform. A WebDAV (Nextcloud) vault, PARA-organised,
under a dedicated non-admin account, with every write path running through a
CUI/PII **block** gate (refuse and surface, never silently redact). The vault
index, semantic layer and knowledge-graph tables live in the platform's
PostgreSQL. Agents read a scoped set of folders through the API or the
gateway and write only through the signed, create-only workspace route; no
agent holds vault credentials. Living status: `docs/SECOND_BRAIN_STATUS.example.md`.

---

## 11. Notes for the FAQ

- **"Why is a feed on REST-fallback freshness?"** — Its push twin is down or
  being shed, or SWIM credentials have not arrived. Only NWS, NOTAM and Amtrak
  have REST twins; the five other SWIM feeds simply go stale.
- **"Is MCP required?"** — No. The gateway is part of the web service and can
  be disabled; the platform runs on REST + ntfy alone.
- **"Why is a SWIM feed stale with good credentials?"** — Most likely a load
  shed (§4.1). Check the guard's state first.
- **"Is this DC-specific?"** — No; see `docs/REGIONALIZATION.md`.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-core.md`.


### (top of document)

**~~CTDI — Infra Map~~** *(former heading)*


### CTDI — Infra Map

> ~~Source material for the forthcoming FAQ / full documentation — an architecture reference, not the FAQ copy itself. Redacted for public consumption: no real domains, IPs, or repo-internal references. Values shown as placeholders (`ops.example.com`, `100.x.x.x`) follow the same convention as the rest of this repo's README.  _Refreshed 2026-08-11 against the reference deployment; corrected 2026-08-19._  **2026-08-19 correction.** This is the public-facing counterpart doc, so its staleness ships publicly. Two things were out of date and are fixed below: (1) the **MCP layer** was presented as a current capability of this deployment — the reference deployment **retired its MCP bridge on 2026-08-18**, though the MCP server software remains a live standalone project (§9); (2) **SWIM feed liveness** was presented as unconditional — the reference deployment automatically load-sheds SWIM ingest containers under CPU/thermal pressure (§4a).  **2026-08-23 correction, live-verified.** §4a's tier table still described a load ladder the reference deployment retired the same day; it is replaced with the current single-stage lockdown model, read out of the guard script itself rather than carried over from the previous doc revision. §4's Runner row also implied the public demo instance is password-gated as-shipped — it is not: the gate is an off-by-default flag, and in the reference deployment it is unset. Both were misleading in a doc whose staleness ships publicly.  **2026-09-03 correction.** Three updates against the reference deployment: the guard's LLM-contention lockdown trigger was demoted to informational-only and the guard no longer touches the LLM services at all (2026-08-27 — the reference deployment also replaced its Ollama daemon with per-tier llama.cpp `llama-server` units the same week); the demo runner instance now sets its `DEMO_MODE` flag explicitly and its password gate is active (§4); and watchlist flight tracking is local-first — the third-party position-API default described in §7 was removed (2026-08-27 local-only directive).~~


### CTDI — Infra Map › 1. What this is

~~**CTDI (Corporate Travel Dispatch Intelligence)** — a real-time executive travel intelligence platform. Monitors commercial flights (FAA SWIM), trains, and weather for a configured metro area; fires push alerts via ntfy; serves a tiered REST API. An MCP tool layer for agent-driven operations is available as a separate, optional component (§9) — note the reference deployment no longer runs it as of 2026-08-18. Designed to run on a single Raspberry Pi 5 (or any `aarch64`/`x86_64` host), as rootless Podman containers under systemd Quadlets.~~

~~See **"Deploying outside DC"** in the main README for how to retarget this to a different metro area, hub airports, and weather field offices.~~


### CTDI — Infra Map › 2. Repo topology

| ~~Repo~~ | ~~Purpose~~ |
|---|---|
| ~~`ctdi-dispatch`~~ | ~~The dispatch platform itself: web API, poller, pusher, ingest, watchlists, auth, CPS scoring, ops dashboard ("Runner").~~ |
| ~~`ctdi-dispatch-self-managed`~~ | ~~Thinner, self-managed deployment variant.~~ |
| ~~`pihole-unbound-selinux`~~ | ~~Layer 1: Pi-hole v6 + Unbound recursive resolver + SELinux enforcing, host DNS/hardening.~~ |
| ~~`corporatetravel-pi-fullstack`~~ | ~~Bundler repo — clones the DNS-hardening layer and the dispatch platform as submodules; one script installs the full stack.~~ |
| ~~`corporatetravel-dispatch-mcp`~~ | ~~MCP server: thin HTTP client exposing the dispatch platform's REST API as portable agent tools (34 tools; a 26-tool public-safe subset can run as a separate process). Works with any MCP-compatible agent. **Optional, and not deployed in the reference deployment since 2026-08-18** — the repo remains available and the server still runs standalone against any reachable dispatch platform (§9).~~ |
| ~~`agentic-management-tooling-mcp`~~ | ~~MCP server: vendor-agnostic agentic safety-rail primitives (mutation gates, budget tracking, session snapshots) plus flight/train/weather data tools (54 tools total). No proprietary infrastructure required — runs standalone.~~ |


### CTDI — Infra Map › 3. Deployment topology

- ~~**Hardware**: Raspberry Pi 5 reference deployment (`aarch64`), Fedora-based, SELinux enforcing. Also tested on Pi 4, ARM cloud instances, and `x86_64`.~~
- ~~**Host DNS**: Pi-hole v6 → Unbound (recursive, DNSSEC-validated) — no third-party upstream DNS.~~
- ~~**Overlay network**: Tailscale (optional but recommended) for T1-tier auth and remote access to the ops dashboard without opening it to the public internet.~~


### CTDI — Infra Map › 4. Service map

| ~~Service~~ | ~~Role~~ | ~~Exposure~~ |
|---|---|---|
| ~~`web`~~ | ~~FastAPI — tiered REST API~~ | ~~Localhost / Tailscale by default~~ |
| ~~`poller`~~ | ~~Async scheduler — runs fetchers on intervals, invokes skills, watches an admin trigger directory~~ | ~~Internal~~ |
| ~~`pusher`~~ | ~~ntfy alert sender — polls DB every 30s for unnotified events~~ | ~~Internal~~ |
| ~~`ingest` ×7~~ | ~~Push feeds (NMS/Solace) split into per-feed containers — one per SWIM feed (FDPS/STDDS/TFMS/TBFM/ITWS/FNS) plus a "core" container (NWS push, rail, local RF) — so any single feed restarts without dropping the rest; REST fallback via poller when a push feed is absent. On constrained single-node hardware these are also individually **load-shed** — see §4a~~ | ~~Internal~~ |
| ~~Runner (ops dashboard)~~ | ~~Operator-facing dashboard~~ | ~~Private-overlay-network only in the reference deployment. A second instance *can* serve a public demo replaying archived, scrubbed data behind an app-layer password gate — the gate is opt-in via a `DEMO_MODE` environment flag that is **off by default**. As of 2026-08-24 the reference deployment sets it explicitly (`DEMO_MODE=true` plus the session secret in the instance's own unit) and its demo protections are active. The lesson stands: treat "public demo" as a mode you must explicitly turn on and verify, not something that happens by deploying a second instance~~ |

~~All services share one database — one schema authority versioned additively across `src/common/db.py` and `src/common/db_swim.py`. The backend is selected by `DISPATCH_DB_BACKEND`: `sqlite` (one WAL file) or `postgres`; see `docs/POSTGRES_MIGRATION.md`.~~


### CTDI — Infra Map › 4. Service map › 4a. SWIM feeds: provisioned ≠ continuously running

~~Documentation of this platform (this doc included) has said "all six SWIM feeds are live". That is true in the sense that matters for setup — all six are provisioned and credentialed — but it is misleading about runtime, and the reason is by design rather than a defect.~~

~~On a single constrained node, a thermal/CPU-load guard runs on a short timer and **stops SWIM ingest containers under pressure, restarting them when the box recovers**. In the reference deployment (`scripts/thermal-ingest-guard.py`, 2-minute timer) the tiers are:~~

| ~~Trip~~ | ~~Condition~~ | ~~What is shed~~ |
|---|---|---|
| ~~Temp, mild~~ | ~~CPU temp ≥ 74 °C~~ | ~~two heaviest SWIM feeds only~~ |
| ~~**Lockdown**~~ | ~~CPU temp ≥ 79 °C **or** 1-min load ≥ 40.0~~ | ~~**everything except the API service** — all six SWIM feeds, the core ingest container, scheduler, alert sender, ops dashboard~~ |
| ~~Informational only~~ | ~~temp 70–74 °C, or load 15–40, or any contention-attributed LLM fallback count~~ | ~~nothing~~ |
| ~~Restore~~ | ~~temp < 65 °C **and** load < 15.0, sustained 5 min~~ | ~~mild restores its two feeds; lockdown restores the whole stack~~ |

~~(2026-08-27 refinements, from the reference deployment's own operating data: the third lockdown trigger — contention-attributed LLM fallbacks — was demoted to informational-only after one night of false trips at normal load, and the guard was changed to never stop or start the LLM services at all — shedding ingest does nothing to relieve LLM contention, and the hot alert path should survive exactly the events lockdown responds to.)~~

~~Thresholds are configurable. **This model replaced an earlier two-stage load ladder (trip at 10/14, restore below 6.0) on 2026-08-23**, on the strength of the reference deployment's own operating data: every real trip on record had been load-driven, never temperature-driven, and normal full-stack load sat at 5–7 — i.e. the old restore bar was inside ordinary noise rather than comfortably above it, so restores were rare and shed periods routinely lasted hours (an ~8-hour shed was observed 2026-08-18/19). Temperature kept its original two-stage trigger as a backstop for a scenario that has not actually occurred yet; load was re-scaled to fire only on genuine runaway. If you adapt this guard, size the restore bar clearly *above* your own measured idle-with-full-stack load, not inside it.~~

~~Two consequences worth knowing before you diagnose anything:~~

- ~~A shed ingest unit reports **stopped-cleanly (exit 0)**, not failed — so it is invisible to "any unit failed?" checks, and restarting it by hand just gets it shed again on the next pass. This is expected behaviour, not a fault to fix.~~
- ~~The health endpoint will correctly report `degraded` with stale push feeds during a shed. Consult the guard's own state (a JSON state file plus its journal) as the authoritative answer to "why is this feed quiet?" before suspecting credentials or connectivity.~~

~~If continuous six-feed push ingest matters more than thermal headroom, the answer is more hardware (or a higher-powered host), not disabling the guard.~~

**~~5. Data flow (text diagram)~~** *(former heading)*


### CTDI — Infra Map › 5. Data flow (text diagram)

*Superseded block:*
```text superseded
FAA SWIM (push)  ──┐   (load-shed under pressure — §4a)
REST fallback  ────┼──▶  poller (fetchers + skills)  ──▶  Postgres DB
Amtrak / weather ──┘                                          │
                                                    ┌───────────┴───────────┐
                                                    ▼                       ▼
                                              web (REST API)          pusher (ntfy)
                                                    │                       │
                                          MCP servers — OPTIONAL       ntfy topics
                                          (dispatch-mcp,               (tfr-alert,
                                           agentic-management-tooling)  flight-alerts,
                                                    │                   train-alerts, …)
                                          any MCP-compatible agent
                                          (Claude Code, Cline, Cursor,
                                           Zed, Windsurf, Open WebUI)
```

~~The MCP branch is an optional add-on, not part of the core data path. The reference deployment **retired its own MCP bridge on 2026-08-18** and now runs everything above it without one; the servers themselves are unaffected and can be attached to any deployment (§9).~~

~~`agentic-management-tooling-mcp` runs independently of the dispatch web API — it keeps its own local state (watchlists, budget tracking, session snapshots) and has its own flight/train/weather lookups, so it works standalone without the rest of this stack.~~


### CTDI — Infra Map › 6. Auth tiers

| ~~Tier~~ | ~~Requirement~~ |
|---|---|
| ~~T0~~ | ~~Anonymous, no token~~ |
| ~~T1~~ | ~~`cert` bearer token (network origin alone grants no tier)~~ |
| ~~T2 (SHARES)~~ | ~~Bearer token, `tier=shares`, audit-logged~~ |
| ~~Admin~~ | ~~Bearer token, `tier=admin` — required for `/admin/*`~~ |

~~Token format: `ctdc_<user>_<32-char-random>`. Only a SHA-256 hash is stored server-side; plaintext is shown once at creation.~~


### CTDI — Infra Map › 7. Watchlist system

- ~~**Permanent** entries: JSON-file-backed (flights, trains, and vessels by MMSI), watched for changes and merged into the DB.~~
- ~~**Transient** entries: carry an expiry timestamp, swept automatically.~~
- ~~Every watchlist event fires two ntfy pushes: a domain topic (full detail: `flight-alerts` / `train-alerts`) and a concise `dispatch` summary. A short dedup window prevents re-firing the same event repeatedly during routine data churn.~~
- ~~Flight tracking is **local-first** (2026-08-27 directive): the deployment's own ADS-B receiver, then its already-ingested SWIM flight data and locally-imported aircraft registries, plus schedule inference when live position data is unavailable. Third-party position APIs are no longer queried by default; an optional FlightAware AeroAPI tier remains in the code but is dormant without a key.~~


### CTDI — Infra Map › 8. ntfy topics

| ~~Topic~~ | ~~Content~~ | ~~Priority~~ |
|---|---|---|
| ~~`tfr-alert`~~ | ~~VIP/high-priority TFR~~ | ~~5 (max)~~ |
| ~~`flight-alerts`~~ | ~~Flight status events, diversions~~ | ~~4–5~~ |
| ~~`train-alerts`~~ | ~~Train delay events~~ | ~~4–5~~ |
| ~~`dispatch`~~ | ~~Concise bottom line, all events~~ | ~~mirrors source~~ |
| ~~`cps`~~ | ~~Critical Predictability State changes~~ | ~~3–5~~ |
| ~~`ops-brief`~~ | ~~Daily/weekly operational brief~~ | ~~3~~ |
| ~~`ops-health`~~ | ~~Feed freshness audit~~ | ~~2~~ |

**~~9. MCP layer~~** *(former heading)*


### CTDI — Infra Map › 9. MCP layer

~~Two independent MCP servers exist, each usable standalone or together — **as standalone projects**, distinct from whether any particular deployment of the dispatch platform is currently running one. **The reference deployment retired its own `corporatetravel-dispatch-mcp` bridge on 2026-08-18** (it had run since 2026-07/08-11 as two `mcpo` OpenAPI-bridge processes in front of the MCP server; both were removed and the checkout archived). That is a statement about this one deployment's current configuration, not about the software:~~

- ~~**`corporatetravel-dispatch-mcp`** — a thin HTTP client. Requires the dispatch platform running somewhere reachable; most tools work at T0 (no auth), a few require Tailscale or an admin token. See that repo's README for the full 34-tool table, including a `dispatch_remember` tool for capturing notes into a second-brain vault (§10) from any MCP client. **Still exists and works as a standalone project** — attaching it to a dispatch deployment (this one or another) is a matter of running the `mcpo` bridge (or an MCP-native client) against that deployment's API, not a platform requirement.~~
- ~~**`agentic-management-tooling-mcp`** — no dispatch platform required. Ships its own safety-rail primitives (mutation confirmation gates, API cost/budget tracking, durable session snapshots across context resets) plus standalone flight/train/weather tools. See that repo's README for the full 54-tool table. Unaffected by the reference deployment's MCP retirement — it never depended on the dispatch platform.~~

~~Both are designed to run concurrently from multiple MCP clients (desktop app, CLI, remote-control sessions, mobile) against shared local state safely — write paths use file locking and atomic writes so concurrent clients cannot corrupt or silently drop each other's updates.~~

~~**NEEDS OPERATOR DECISION:** this doc otherwise reads as "here's what's available if you stand this up" — worth confirming whether the intent going forward is to keep describing `corporatetravel-dispatch-mcp` as a first-class part of the reference deployment story (with a note that the reference instance itself currently opts out) or to reposition it more clearly as a bring-your-own-bridge integration now that the reference deployment doesn't run it. This pass took the conservative option (kept the section, added the correction) rather than deleting content.~~

**~~10. Second-brain vault & multi-session coordination (optional layer)~~** *(former heading)*


### CTDI — Infra Map › 10. Second-brain vault & multi-session coordination (optional layer)

~~Not required to run the dispatch platform itself, but a pattern worth adopting if you're running multiple AI agents/sessions against the same deployment (desktop app, CLI, scheduled/background agents, mobile) — which this platform is explicitly designed to support concurrently.~~

~~**Cross-provider shared memory.** A Nextcloud-hosted (or any WebDAV target) knowledge vault, PARA-organized, with one subtree dedicated to AI-agent memory specifically — separate from the general knowledge content. Each agent/provider (Claude, a local model, another cloud provider) gets its own subfolder with two files: a distilled, current-state `memory-index.md` (updated in place, not a log) and an append-only `session-log/` for detailed write-ups. All agents share one entry-format contract and one low-friction `notepad/` drop zone for anything worth capturing without the ceremony of a structured entry — triaged into the right place on a daily automated pass. The point isn't "give Claude memory" — every major provider already has some private persistence mechanism — it's giving *every* agent a shared place to read what another agent (or an earlier session of itself) already established, so context survives switching tools.~~

~~**Multi-session coordination.** With no atomic locking between concurrent agent-driven sessions by default, the same `notepad/` doubles as an interim, out-of-band coordination channel: before starting work that could collide with another concurrent session (shared build/deploy scripts, network/tunnel config, anything touching live infra state outside a session's own files), check it for recent notes from other agents first; after finishing or pausing such work, drop a checkpoint note — what was touched, what's pending, an explicit ask for conflicts to be flagged back. Human-readable, provider-agnostic, and reuses infrastructure (WebDAV write path, scrub gate) that already exists for the memory vault itself rather than building a separate mechanism.~~

~~Every write path into a shared vault like this should run through a CUI/PII scrub gate first — a **block**, not a redact: refuse and surface the failure rather than silently laundering sensitive content, since silent redaction hides the fact a human needs to look at it.~~


### CTDI — Infra Map › 11. Notes for the FAQ

~~A few things worth anticipating in the eventual FAQ, based on what's easy to get wrong when standing this up:~~

- ~~**"Why is a feed showing REST-fallback freshness instead of live push?"** — SWIM/NMS push credentials are provisioned separately from the rest of setup; until they arrive, REST polling covers every feed automatically, no code changes needed once credentials land.~~
- ~~**"Can I run just the MCP tools without the dispatch platform?"** — Yes, for `agentic-management-tooling-mcp`; `corporatetravel-dispatch-mcp` needs a running dispatch platform to talk to.~~
- ~~**"Is the MCP layer required, or does the reference deployment even run it?"** — Required: no, it's an optional add-on (§9). Running it: as of 2026-08-18 the reference deployment itself does **not** — its `mcpo` bridges were retired and the platform now serves only the REST API + ntfy alerts. The MCP servers remain independently usable against this or any other dispatch deployment; standing one back up is a matter of re-attaching a bridge, not a code change.~~
- ~~**"Why is a SWIM feed's push data stale even though credentials are configured?"** — Most likely a load-shed in progress, not a credential problem — see §4a. Check the guard's state/journal before assuming an outage.~~
- ~~**"Is this specific to Washington DC?"** — No — see "Deploying outside DC" in the main README for regionalizing hub airports and weather field offices.~~
