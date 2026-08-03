# CTDI — Infra Map

> Source material for the forthcoming FAQ / full documentation — an
> architecture reference, not the FAQ copy itself. Redacted for public
> consumption: no real domains, IPs, or repo-internal references. Values
> shown as placeholders (`ops.example.com`, `100.x.x.x`) follow the same
> convention as the rest of this repo's README.

---

## 1. What this is

**CTDI (Corporate Travel Dispatch Intelligence)** — a real-time executive
travel intelligence platform. Monitors commercial flights (FAA SWIM),
trains, and weather for a configured metro area; fires push alerts via
ntfy; serves a tiered REST API and an MCP tool layer for agent-driven
operations. Designed to run on a single Raspberry Pi 5 (or any
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
| `corporatetravel-dispatch-mcp` | MCP server: thin HTTP client exposing the dispatch platform's REST API as portable agent tools (21 tools). Works with any MCP-compatible agent. |
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
| `ingest` | FAA SWIM push feeds (NMS/Solace AMQP), REST fallback via poller when absent | Internal |
| Runner (ops dashboard) | Operator-facing dashboard | Optionally public, e.g. `https://ops.example.com` |

All services share one SQLite database (WAL mode) — a single schema authority (`src/common/db.py`), versioned additively.

---

## 5. Data flow (text diagram)

```
FAA SWIM (push)  ──┐
REST fallback  ────┼──▶  poller (fetchers + skills)  ──▶  SQLite DB (WAL)
Amtrak / weather ──┘                                          │
                                                    ┌───────────┴───────────┐
                                                    ▼                       ▼
                                              web (REST API)          pusher (ntfy)
                                                    │                       │
                                          MCP servers (dispatch-mcp,   ntfy topics
                                          agentic-management-tooling)  (tfr-alert,
                                                    │                   flight-alerts,
                                          any MCP-compatible agent     train-alerts, …)
                                          (Claude Code, Cline, Cursor,
                                           Zed, Windsurf, Open WebUI)
```

`agentic-management-tooling-mcp` runs independently of the dispatch web API — it keeps its own local state (watchlists, budget tracking, session snapshots) and has its own flight/train/weather lookups, so it works standalone without the rest of this stack.

---

## 6. Auth tiers

| Tier | Requirement |
|---|---|
| T0 | Anonymous, no token |
| T1 | Tailscale identity header, or `cert` bearer token |
| T2 (SHARES) | Bearer token, `tier=shares`, audit-logged |
| Admin | Bearer token, `tier=admin` — required for `/admin/*` |

Token format: `ctdc_<user>_<32-char-random>`. Only a SHA-256 hash is stored server-side; plaintext is shown once at creation.

---

## 7. Watchlist system

- **Permanent** entries: file-backed, watched for changes and merged into the DB.
- **Transient** entries: carry an expiry timestamp, swept automatically.
- Every watchlist event fires two ntfy pushes: a domain topic (full detail: `flight-alerts` / `train-alerts`) and a concise `dispatch` summary. A short dedup window prevents re-firing the same event repeatedly during routine data churn.
- Flight tracking defaults to the free `airplanes.live` API, with an optional FlightAware AeroAPI fallback tier if a key is configured, plus schedule inference when live position data is unavailable.

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

Two independent MCP servers, each usable standalone or together:

- **`corporatetravel-dispatch-mcp`** — a thin HTTP client. Requires the dispatch platform running somewhere reachable; most tools work at T0 (no auth), a few require Tailscale or an admin token. See that repo's README for the full 34-tool table, including a `dispatch_remember` tool for capturing notes into a second-brain vault (§10) from any MCP client.
- **`agentic-management-tooling-mcp`** — no dispatch platform required. Ships its own safety-rail primitives (mutation confirmation gates, API cost/budget tracking, durable session snapshots across context resets) plus standalone flight/train/weather tools. See that repo's README for the full 54-tool table.

Both are designed to run concurrently from multiple MCP clients (desktop app, CLI, remote-control sessions, mobile) against shared local state safely — write paths use file locking and atomic writes so concurrent clients cannot corrupt or silently drop each other's updates.

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
- **"Is this specific to Washington DC?"** — No — see "Deploying outside DC" in the main README for regionalizing hub airports and weather field offices.
