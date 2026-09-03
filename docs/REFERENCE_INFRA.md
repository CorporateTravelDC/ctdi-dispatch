# CTDI — Infra Map

> Source material for the forthcoming FAQ / full documentation — an
> architecture reference, not the FAQ copy itself. Redacted for public
> consumption: no real domains, IPs, or repo-internal references. Values
> shown as placeholders (`ops.example.com`, `100.x.x.x`) follow the same
> convention as the rest of this repo's README.
>
> _Refreshed 2026-08-11 against the reference deployment; corrected
> 2026-08-19._
>
> **2026-08-19 correction.** This is the public-facing counterpart doc, so its
> staleness ships publicly. Two things were out of date and are fixed below:
> (1) the **MCP layer** was presented as a current capability of this
> deployment — the reference deployment **retired its MCP bridge on
> 2026-08-18**, though the MCP server software remains a live standalone
> project (§9); (2) **SWIM feed liveness** was presented as unconditional —
> the reference deployment automatically load-sheds SWIM ingest containers
> under CPU/thermal pressure (§4a).
>
> **2026-08-23 correction, live-verified.** §4a's tier table still described a
> load ladder the reference deployment retired the same day; it is replaced
> with the current single-stage lockdown model, read out of the guard script
> itself rather than carried over from the previous doc revision. §4's Runner
> row also implied the public demo instance is password-gated as-shipped — it
> is not: the gate is an off-by-default flag, and in the reference deployment
> it is unset. Both were misleading in a doc whose staleness ships publicly.
>
> **2026-09-03 correction.** Three updates against the reference deployment:
> the guard's LLM-contention lockdown trigger was demoted to
> informational-only and the guard no longer touches the LLM services at all
> (2026-08-27 — the reference deployment also replaced its Ollama daemon
> with per-tier llama.cpp `llama-server` units the same week); the demo
> runner instance now sets its `DEMO_MODE` flag explicitly and its password
> gate is active (§4); and watchlist flight tracking is local-first — the
> third-party position-API default described in §7 was removed (2026-08-27
> local-only directive).

---

## 1. What this is

**CTDI (Corporate Travel Dispatch Intelligence)** — a real-time executive
travel intelligence platform. Monitors commercial flights (FAA SWIM),
trains, and weather for a configured metro area; fires push alerts via
ntfy; serves a tiered REST API. An MCP tool layer for agent-driven operations
is available as a separate, optional component (§9) — note the reference
deployment no longer runs it as of 2026-08-18. Designed to run on a single
Raspberry Pi 5 (or any
`aarch64`/`x86_64` host), as rootless Podman containers under systemd
Quadlets.

See **"Deploying outside DC"** in the main README for how to retarget this
to a different metro area, hub airports, and weather field offices.

---

## 2. Repo topology

| Repo | Purpose |
|---|---|
| `ctdi-dispatch` | The dispatch platform itself: web API, poller, pusher, ingest, watchlists, auth, CPS scoring, ops dashboard ("Runner"). |
| `ctdi-dispatch-self-managed` | Thinner, self-managed deployment variant. |
| `pihole-unbound-selinux` | Layer 1: Pi-hole v6 + Unbound recursive resolver + SELinux enforcing, host DNS/hardening. |
| `corporatetravel-pi-fullstack` | Bundler repo — clones the DNS-hardening layer and the dispatch platform as submodules; one script installs the full stack. |
| `corporatetravel-dispatch-mcp` | MCP server: thin HTTP client exposing the dispatch platform's REST API as portable agent tools (34 tools; a 26-tool public-safe subset can run as a separate process). Works with any MCP-compatible agent. **Optional, and not deployed in the reference deployment since 2026-08-18** — the repo remains available and the server still runs standalone against any reachable dispatch platform (§9). |
| `agentic-management-tooling-mcp` | MCP server: vendor-agnostic agentic safety-rail primitives (mutation gates, budget tracking, session snapshots) plus flight/train/weather data tools (54 tools total). No proprietary infrastructure required — runs standalone. |

---

## 3. Deployment topology

- **Hardware**: Raspberry Pi 5 reference deployment (`aarch64`), Fedora-based, SELinux enforcing. Also tested on Pi 4, ARM cloud instances, and `x86_64`.
- **Host DNS**: Pi-hole v6 → Unbound (recursive, DNSSEC-validated) — no third-party upstream DNS.
- **Overlay network**: Tailscale (optional but recommended) for T1-tier auth and remote access to the ops dashboard without opening it to the public internet.

---

## 4. Service map

| Service | Role | Exposure |
|---|---|---|
| `web` | FastAPI — tiered REST API | Localhost / Tailscale by default |
| `poller` | Async scheduler — runs fetchers on intervals, invokes skills, watches an admin trigger directory | Internal |
| `pusher` | ntfy alert sender — polls DB every 30s for unnotified events | Internal |
| `ingest` ×7 | Push feeds (NMS/Solace) split into per-feed containers — one per SWIM feed (FDPS/STDDS/TFMS/TBFM/ITWS/FNS) plus a "core" container (NWS push, rail, local RF) — so any single feed restarts without dropping the rest; REST fallback via poller when a push feed is absent. On constrained single-node hardware these are also individually **load-shed** — see §4a | Internal |
| Runner (ops dashboard) | Operator-facing dashboard | Private-overlay-network only in the reference deployment. A second instance *can* serve a public demo replaying archived, scrubbed data behind an app-layer password gate — the gate is opt-in via a `DEMO_MODE` environment flag that is **off by default**. As of 2026-08-24 the reference deployment sets it explicitly (`DEMO_MODE=true` plus the session secret in the instance's own unit) and its demo protections are active. The lesson stands: treat "public demo" as a mode you must explicitly turn on and verify, not something that happens by deploying a second instance |

All services share one SQLite database (WAL mode) — one schema authority versioned additively across `src/common/db.py` and `src/common/db_swim.py`.

### 4a. SWIM feeds: provisioned ≠ continuously running

Documentation of this platform (this doc included) has said "all six SWIM
feeds are live". That is true in the sense that matters for setup — all six
are provisioned and credentialed — but it is misleading about runtime, and
the reason is by design rather than a defect.

On a single constrained node, a thermal/CPU-load guard runs on a short timer
and **stops SWIM ingest containers under pressure, restarting them when the
box recovers**. In the reference deployment (`scripts/thermal-ingest-guard.py`,
2-minute timer) the tiers are:

| Trip | Condition | What is shed |
|---|---|---|
| Temp, mild | CPU temp ≥ 74 °C | two heaviest SWIM feeds only |
| **Lockdown** | CPU temp ≥ 79 °C **or** 1-min load ≥ 40.0 | **everything except the API service** — all six SWIM feeds, the core ingest container, scheduler, alert sender, ops dashboard |
| Informational only | temp 70–74 °C, or load 15–40, or any contention-attributed LLM fallback count | nothing |
| Restore | temp < 65 °C **and** load < 15.0, sustained 5 min | mild restores its two feeds; lockdown restores the whole stack |

(2026-08-27 refinements, from the reference deployment's own operating data:
the third lockdown trigger — contention-attributed LLM fallbacks — was
demoted to informational-only after one night of false trips at normal load,
and the guard was changed to never stop or start the LLM services at all —
shedding ingest does nothing to relieve LLM contention, and the hot alert
path should survive exactly the events lockdown responds to.)

Thresholds are configurable. **This model replaced an earlier two-stage load
ladder (trip at 10/14, restore below 6.0) on 2026-08-23**, on the strength of
the reference deployment's own operating data: every real trip on record had
been load-driven, never temperature-driven, and normal full-stack load sat at
5–7 — i.e. the old restore bar was inside ordinary noise rather than
comfortably above it, so restores were rare and shed periods routinely lasted
hours (an ~8-hour shed was observed 2026-08-18/19). Temperature kept its
original two-stage trigger as a backstop for a scenario that has not actually
occurred yet; load was re-scaled to fire only on genuine runaway. If you
adapt this guard, size the restore bar clearly *above* your own measured
idle-with-full-stack load, not inside it.

Two consequences worth knowing before you diagnose anything:

- A shed ingest unit reports **stopped-cleanly (exit 0)**, not failed — so it
  is invisible to "any unit failed?" checks, and restarting it by hand just
  gets it shed again on the next pass. This is expected behaviour, not a fault
  to fix.
- The health endpoint will correctly report `degraded` with stale push feeds
  during a shed. Consult the guard's own state (a JSON state file plus its
  journal) as the authoritative answer to "why is this feed quiet?" before
  suspecting credentials or connectivity.

If continuous six-feed push ingest matters more than thermal headroom, the
answer is more hardware (or a higher-powered host), not disabling the guard.

---

## 5. Data flow (text diagram)

```
FAA SWIM (push)  ──┐   (load-shed under pressure — §4a)
REST fallback  ────┼──▶  poller (fetchers + skills)  ──▶  SQLite DB (WAL)
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

The MCP branch is an optional add-on, not part of the core data path. The
reference deployment **retired its own MCP bridge on 2026-08-18** and now runs
everything above it without one; the servers themselves are unaffected and can
be attached to any deployment (§9).

`agentic-management-tooling-mcp` runs independently of the dispatch web API — it keeps its own local state (watchlists, budget tracking, session snapshots) and has its own flight/train/weather lookups, so it works standalone without the rest of this stack.

---

## 6. Auth tiers

| Tier | Requirement |
|---|---|
| T0 | Anonymous, no token |
| T1 | `cert` bearer token (network origin alone grants no tier) |
| T2 (SHARES) | Bearer token, `tier=shares`, audit-logged |
| Admin | Bearer token, `tier=admin` — required for `/admin/*` |

Token format: `ctdc_<user>_<32-char-random>`. Only a SHA-256 hash is stored server-side; plaintext is shown once at creation.

---

## 7. Watchlist system

- **Permanent** entries: JSON-file-backed (flights, trains, and vessels by MMSI), watched for changes and merged into the DB.
- **Transient** entries: carry an expiry timestamp, swept automatically.
- Every watchlist event fires two ntfy pushes: a domain topic (full detail: `flight-alerts` / `train-alerts`) and a concise `dispatch` summary. A short dedup window prevents re-firing the same event repeatedly during routine data churn.
- Flight tracking is **local-first** (2026-08-27 directive): the deployment's own ADS-B receiver, then its already-ingested SWIM flight data and locally-imported aircraft registries, plus schedule inference when live position data is unavailable. Third-party position APIs are no longer queried by default; an optional FlightAware AeroAPI tier remains in the code but is dormant without a key.

---

## 8. ntfy topics

| Topic | Content | Priority |
|---|---|---|
| `tfr-alert` | VIP/high-priority TFR | 5 (max) |
| `flight-alerts` | Flight status events, diversions | 4–5 |
| `train-alerts` | Train delay events | 4–5 |
| `dispatch` | Concise bottom line, all events | mirrors source |
| `cps` | Critical Predictability State changes | 3–5 |
| `ops-brief` | Daily/weekly operational brief | 3 |
| `ops-health` | Feed freshness audit | 2 |

---

## 9. MCP layer

Two independent MCP servers exist, each usable standalone or together — **as
standalone projects**, distinct from whether any particular deployment of the
dispatch platform is currently running one. **The reference deployment
retired its own `corporatetravel-dispatch-mcp` bridge on 2026-08-18** (it had
run since 2026-07/08-11 as two `mcpo` OpenAPI-bridge processes in front of the
MCP server; both were removed and the checkout archived). That is a statement
about this one deployment's current configuration, not about the software:

- **`corporatetravel-dispatch-mcp`** — a thin HTTP client. Requires the dispatch platform running somewhere reachable; most tools work at T0 (no auth), a few require Tailscale or an admin token. See that repo's README for the full 34-tool table, including a `dispatch_remember` tool for capturing notes into a second-brain vault (§10) from any MCP client. **Still exists and works as a standalone project** — attaching it to a dispatch deployment (this one or another) is a matter of running the `mcpo` bridge (or an MCP-native client) against that deployment's API, not a platform requirement.
- **`agentic-management-tooling-mcp`** — no dispatch platform required. Ships its own safety-rail primitives (mutation confirmation gates, API cost/budget tracking, durable session snapshots across context resets) plus standalone flight/train/weather tools. See that repo's README for the full 54-tool table. Unaffected by the reference deployment's MCP retirement — it never depended on the dispatch platform.

Both are designed to run concurrently from multiple MCP clients (desktop app, CLI, remote-control sessions, mobile) against shared local state safely — write paths use file locking and atomic writes so concurrent clients cannot corrupt or silently drop each other's updates.

**NEEDS OPERATOR DECISION:** this doc otherwise reads as "here's what's available if you stand this up" — worth confirming whether the intent going forward is to keep describing `corporatetravel-dispatch-mcp` as a first-class part of the reference deployment story (with a note that the reference instance itself currently opts out) or to reposition it more clearly as a bring-your-own-bridge integration now that the reference deployment doesn't run it. This pass took the conservative option (kept the section, added the correction) rather than deleting content.

---

## 10. Second-brain vault & multi-session coordination (optional layer)

Not required to run the dispatch platform itself, but a pattern worth adopting if you're running multiple AI agents/sessions against the same deployment (desktop app, CLI, scheduled/background agents, mobile) — which this platform is explicitly designed to support concurrently.

**Cross-provider shared memory.** A Nextcloud-hosted (or any WebDAV target) knowledge vault, PARA-organized, with one subtree dedicated to AI-agent memory specifically — separate from the general knowledge content. Each agent/provider (Claude, a local model, another cloud provider) gets its own subfolder with two files: a distilled, current-state `memory-index.md` (updated in place, not a log) and an append-only `session-log/` for detailed write-ups. All agents share one entry-format contract and one low-friction `notepad/` drop zone for anything worth capturing without the ceremony of a structured entry — triaged into the right place on a daily automated pass. The point isn't "give Claude memory" — every major provider already has some private persistence mechanism — it's giving *every* agent a shared place to read what another agent (or an earlier session of itself) already established, so context survives switching tools.

**Multi-session coordination.** With no atomic locking between concurrent agent-driven sessions by default, the same `notepad/` doubles as an interim, out-of-band coordination channel: before starting work that could collide with another concurrent session (shared build/deploy scripts, network/tunnel config, anything touching live infra state outside a session's own files), check it for recent notes from other agents first; after finishing or pausing such work, drop a checkpoint note — what was touched, what's pending, an explicit ask for conflicts to be flagged back. Human-readable, provider-agnostic, and reuses infrastructure (WebDAV write path, scrub gate) that already exists for the memory vault itself rather than building a separate mechanism.

Every write path into a shared vault like this should run through a CUI/PII scrub gate first — a **block**, not a redact: refuse and surface the failure rather than silently laundering sensitive content, since silent redaction hides the fact a human needs to look at it.

---

## 11. Notes for the FAQ

A few things worth anticipating in the eventual FAQ, based on what's easy to get wrong when standing this up:

- **"Why is a feed showing REST-fallback freshness instead of live push?"** — SWIM/NMS push credentials are provisioned separately from the rest of setup; until they arrive, REST polling covers every feed automatically, no code changes needed once credentials land.
- **"Can I run just the MCP tools without the dispatch platform?"** — Yes, for `agentic-management-tooling-mcp`; `corporatetravel-dispatch-mcp` needs a running dispatch platform to talk to.
- **"Is the MCP layer required, or does the reference deployment even run it?"** — Required: no, it's an optional add-on (§9). Running it: as of 2026-08-18 the reference deployment itself does **not** — its `mcpo` bridges were retired and the platform now serves only the REST API + ntfy alerts. The MCP servers remain independently usable against this or any other dispatch deployment; standing one back up is a matter of re-attaching a bridge, not a code change.
- **"Why is a SWIM feed's push data stale even though credentials are configured?"** — Most likely a load-shed in progress, not a credential problem — see §4a. Check the guard's state/journal before assuming an outage.
- **"Is this specific to Washington DC?"** — No — see "Deploying outside DC" in the main README for regionalizing hub airports and weather field offices.
