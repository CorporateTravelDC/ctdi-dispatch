# CTDI Dispatch: On-Premises Architecture & Compliance Datasheet

The Corporate Travel Dispatch Intelligence (CTDI) platform is architected for zero-trust, on-premises deployment. It runs entirely within the operator's own managed environment -- no data leaves the deployment to any third-party cloud service by default, and every inference call stays local (see `DESIGN-PRINCIPLES.md`).

**Framing note (2026-08-03):** this document was previously written with several sections describing features that were never built -- a "Compliance Egress Hook Engine," direct PNR/reservation-record processing, and specific SEC/FINRA integration claims that don't correspond to anything in this codebase. That content has been replaced below with an accurate description of what the platform actually does and how it's meant to be positioned. The platform does not process travel bookings, PNRs, or reservation records itself, and does not claim to. What it *does* do -- run entirely on-premises, log its own actions locally and immutably, and stay out of a client's existing travel/booking data path -- is real, and is what this document now describes.

---

## 1. Positioning: Compliance-Supportive Infrastructure, Not a Compliance Product

CTDI is not a recordkeeping system, a PNR processor, or a regulatory filing tool. It is a situational-awareness and dispatch platform (weather, TFRs, NAS/ATCSCC ground programs, Amtrak status, ADS-B/ASDE-X ground movement, and a chauffeur/executive-transport runsheet) that an operator runs on their own hardware, in parallel with whatever travel-management, booking, or dispatch software they already use (a GDS, a limo-dispatch platform like LimoAnywhere, a PBX/call system like RingCentral or 3CX, or an internal scheduling tool). The `runsheet` table in this platform is deliberately ingest-only today for exactly this reason -- it's built to receive trip data *from* an operator's existing system, not to replace it or to become the operator's system of record (see `runsheet_ingest_only_until_limoanywhere_tiein` in project history).

The compliance-relevant claim this document supports is narrow and accurate: **an operator who runs CTDI alongside their existing travel/booking platforms is not introducing a new third-party data-handling risk into their compliance posture**, because CTDI:

- Never contacts a cloud LLM or third-party data-processing API by default (see `DESIGN-PRINCIPLES.md` §2) -- there is no vendor in the loop reading operational data.
- Never receives or stores PNR, reservation, or payment data -- it has no data model for any of that (confirmed: no such table exists in this platform's schema).
- Keeps every action it takes -- feed fetches, admin actions, alert fires -- in a local, append-only audit log that never leaves the device (§3 below).

This is a statement about *what CTDI adds to* an operator's existing compliance posture (nothing that wasn't already there, no new external dependency, no new data-handling surface), not a claim that CTDI itself is a regulatory-compliance product, that it satisfies SEC Rule 17a-4 or FINRA Rule 4511 recordkeeping requirements on its own, or that it has been reviewed by any regulator. Those rules govern a firm's own books-and-records obligations for its own regulated activity; whether and how they apply is a determination the operator's own compliance counsel makes, not something this platform certifies. If an operator's existing travel/booking platform has its own recordkeeping obligations, CTDI's job is to stay out of that data path entirely -- which, by having no PNR/booking data model at all, it does by construction.

---

## 2. Data Sovereignty & Isolation

| Data Classification | Processing Location | External Network Escape | Storage State |
| :--- | :--- | :--- | :--- |
| **Operational feeds** (weather, TFR, NOTAM, ATCSCC ops-plan, Amtrak, ADS-B/ASDE-X, runsheet) | Internal Podman containers | None by default -- read-only pulls from government/public-interest sources only (see `DESIGN-PRINCIPLES.md` §3) | Local SQLite (WAL mode), on-device |
| **LLM Inference** | Native host Ollama daemon | None (air-gapped compatible) | Ephemeral -- no query or response is sent to any external provider |
| **Audit Logs** | Systemd journald + local SQLite `audit_log` table | None (0% outbound) | Append-only, local disk, 90-day retention |
| **Public demo playback** | Dedicated `demo-api`/`runner-demo` containers, sovereign SQLite file (see below) | Internal-only today (no public DNS/CF Tunnel ingress); password-gated when it is exposed | Sovereign file, physically separate directory, `:ro` container mount |

By default, the platform binds its web interfaces, backend processes, and LLM orchestration layer strictly to the host environment or internal container-network interfaces. Operational data -- flight tracks, TFRs, weather, watchlist entries, runsheet trips -- is processed and stored entirely on the deployed device. Nothing here describes or requires third-party PNR data ingestion; if a future runsheet integration (LimoAnywhere/RingCentral/3CX) is built, the same isolation principles apply to whatever trip-level data that integration actually carries, and this section will be updated to reflect the real data model at that time rather than a hypothetical one.

**Demo/production isolation (2026-08-14, closing F6).** The public demo
instance reads from `/var/lib/corporatetraveldc-demo-source/demo-source.db`
-- a physically separate SQLite file, in a separate top-level directory
from the live `/var/lib/corporatetraveldc` tree, populated exclusively by
`scripts/scrub-demo-source.py`. That script is the only component
permitted to touch both sides: it runs host-side (never inside a
container that could be conflated with the public-facing demo surface),
self-verifies against the signed manifest before running, reads live data
read-only, and every row passes a two-layer scrub (`src/demo/scrub_rules.py`
substitution + fail-closed allowlist verification, modeled on
`scripts/scrub-public-tree.py`'s public-mirror discipline) before
promotion -- a row that still matches a forbidden pattern after
substitution is dropped, never shipped with a warning. `demo-api` and
`runner-demo` hold no live-DB connection and no live-directory mount at
all; not an application-level choice not to read it, no filesystem path
to it exists. A scheduled refresh
(`corporatetraveldc-demo-source-refresh.timer`, nightly 04:45 ET) keeps
the demo tracking real platform growth without ever serving data more
recent than the last promotion. See
`docs/DEMO_DATA_ISOLATION_PLAN_2026-08-13.md` for the full design and
`docs/PENTEST_CLEARANCE_CHECK_2026-08-13.md` for the finding this closes.

Housekeeping note found while grounding this section: several code
comments elsewhere in the repo cite a "Signed Manifest Integrity" section
of this document that doesn't exist here under that exact heading -- the
mechanism itself (`scripts/sign-manifest.sh`/`verify-manifest.sh`,
`_verify_before_inference()` in `src/common/llm.py`, the self-check every
privileged script in this section runs before proceeding) is real and
live; the cross-reference is drift, not a missing control. Flagging here
rather than silently fixing every comment, since some of those comments
may be intentionally pointing at a future consolidated section.

---

## 3. Audit Logging (Real, As-Built)

CTDI maintains a genuine append-only audit log (`audit_log` table, local SQLite, never leaves the device) recording every admin action taken through the platform's API:

```sql
CREATE TABLE audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time      REAL DEFAULT (unixepoch()),
    action          TEXT NOT NULL,
    tier            TEXT NOT NULL,
    token_prefix    TEXT,           -- first 8 chars of token only, never the full token
    remote_addr     TEXT,
    detail          TEXT            -- JSON, action-specific
);
```

This is the platform's actual, current audit mechanism. The data lives locally in this table first and always; it can be queried directly (`/admin/audit`, token-gated) whether or not anything below is ever turned on.

As of 2026-08-03, CTDI also ships a real, disabled-by-default outbound egress hook for operators whose own recordkeeping platform (Global Relay, Smarsh, an internal SIEM, or anything else) needs a copy of this audit trail pushed out rather than pulled. It is off unless an operator explicitly configures it:

```
COMPLIANCE_HOOK_ENABLED=false        # default -- module no-ops immediately if false
COMPLIANCE_TARGET_URL=               # operator's own endpoint; unset = no-op even if enabled
COMPLIANCE_TARGET_AUTH_HEADER=       # optional bearer/auth header for that endpoint
COMPLIANCE_RETRY_LIMIT=5             # attempts before a row is marked failed_permanent
```

`COMPLIANCE_TARGET_URL` and `COMPLIANCE_TARGET_AUTH_HEADER` are operator secrets (they can reveal or grant access to an internal endpoint) and belong in `dispatch-secrets.env`, never in the tracked non-secret config.

Mechanically: a 5-minute systemd timer (`corporatetraveldc-compliance-egress-push.timer`) runs `common.compliance_egress.push_pending_audit_events()` inside the poller container. It checks `COMPLIANCE_HOOK_ENABLED` and `COMPLIANCE_TARGET_URL` first and returns immediately (no DB read, no network call) if either is unset. When both are set, it batches unshipped `audit_log` rows (tracked via `egress_status`/`egress_attempts`/`egress_last_error` columns on that same table -- no second table, no duplicated log) and POSTs a fixed envelope: `record_id, event_time_utc, source_node, action, tier, token_prefix, remote_addr, detail`. That is the entire payload shape -- there is no PNR, reservation, or trip-level field in it, because none of that lives in `audit_log` to begin with. Rows that fail are retried up to `COMPLIANCE_RETRY_LIMIT` times, then marked `failed_permanent` so a persistently unreachable target degrades to "stop trying" rather than retrying forever.

---

## 4. Alignment with ISO/IEC 42001 (AI Management System Standard)

**Status, stated plainly: CTDI is not ISO/IEC 42001 certified.** Certification requires an accredited third-party certification body to run a two-stage audit (documentation review, then an operational-effectiveness evaluation with staff interviews and evidence collection) followed by annual surveillance audits. No such audit has been performed on this platform or on [operator LLC], LLC. Any claim to the contrary would be inaccurate and is not made anywhere in this document.

What *is* true, and is the actual basis for this section: CTDI's core design principles were built around several of the same control areas ISO/IEC 42001's Annex A asks an AI management system to address, independent of and prior to this document being written. That is a genuine architectural fact, not a marketing gloss -- the table below maps specific, existing platform behavior to the relevant control area.

| ISO/IEC 42001 Annex A control area | What CTDI actually does |
| :--- | :--- |
| A.7 -- Data for AI systems | No operator query or model input/output is sent to any external party. Ollama-only local inference is a hard default (`DESIGN-PRINCIPLES.md` §2); CUI-classified radio data is handled under an explicit, non-negotiable ruleset (never in code, configs, or exports). |
| A.9 -- Responsible use of the AI system | Deterministic fallback is required when local inference is unavailable -- the system does not silently fail over to a cloud provider. Any cloud LLM integration must be an explicit, operator-controlled opt-in, never a default. |
| A.10 -- Third-party / supplier relationships | Local-only inference removes the AI supply-chain risk (model-vendor data handling, training-data exposure, vendor outage dependency) that ISO/IEC 42001 is partly designed to help organizations manage. |
| A.4 -- Resources for AI systems | Real, evidenced resource guardrails (network, memory, CPU, thermal) exist for every AI-adjacent process, each backed by dated incident data and telemetry, not a theoretical worst case (`GUARDRAILS_JUSTIFICATION.md`). |

What is genuinely missing, and would need to be built before a real certification audit could be attempted: a formal, top-management-owned AI policy document (as opposed to the informal, code-enforced rules in `DESIGN-PRINCIPLES.md`), defined AI-governance roles and responsibilities (this is presently a single-operator business), a documented AI risk/impact-assessment methodology, and a management-review cadence. None of these are difficult to build on top of what already exists -- the underlying practices they'd formalize are already there -- but they are real gaps, not paperwork technicalities, and no pitch material should describe them as already closed.

**Net position for a client or partner conversation:** CTDI is not certified, and does not claim to be. It was built using ISO/IEC 42001's control areas as design guardrails from early in its development, which means an operator adopting it starts substantively closer to a certifiable posture than a platform built without that framework in mind -- "compliance-adjacent from day one" in the sense that the hard technical work (data handling, vendor isolation, resource governance) is already done, not in the sense that a certificate exists. A full standalone treatment of this, including the honest gap list, lives in `ISO_42001_ALIGNMENT.md`.

---

## 5. Host Integration & Hardening Guidelines

* **SELinux Support:** The system includes ready-to-use Type Enforcement modules (`.te`) that authorize the background systemd service layers to function within targeted enforcement contexts.
* **Process Priority:** The platform isolates worker processes inside rootless Podman containers, keeping system resources strictly ringfenced away from host operations.
* **Thermal Resource Caps:** System configurations map physical thread caps directly to the hardware (`PARAMETER num_thread 3`), ensuring core components (such as Pi-hole or local network routers) always have dedicated computing headroom.

### SELinux Grant Policy: Scoped Labels Over Domain-Wide Booleans

Every SELinux exception this platform requires is granted at the narrowest
scope that resolves it -- a specific `.te` allow rule or a specific
`semanage port` label -- rather than a domain-wide boolean such as
`container_use_devices` or `httpd_can_network_connect`. A boolean grants a
whole security domain (every container, or `nginx` itself) blanket reach
to a whole permission class; a scoped label or rule grants exactly the one
resource one process needs, and nothing else.

This keeps `semanage port -l` and `semodule -l` a complete, self-explaining
audit trail: every entry maps to one named module or one documented
backend, not an open-ended grant whose blast radius has to be inferred.
Concretely:

* `selinux/corporatetraveldc-sdr-usb.te` grants `container_t` access to the
  RTL-SDR USB devices only (`usb_device_t` chr_file) -- not the broader
  `container_use_devices` boolean, which would apply to every container on
  the host, present or future.
* `selinux/label-nginx-backend-ports.sh` labels each nginx `proxy_pass`
  target individually as `http_port_t`, and `selinux/corporatetraveldc-nginx-proxy.te`
  grants `httpd_t` name_connect to that type specifically -- not the
  `httpd_can_network_connect` boolean, whose scope is every TCP port class
  in the base policy, not just the ones this repo defines. Each labeled
  port traces to exactly one vhost in `nginx/conf.d/`.
* `selinux/corporatetraveldc-fail2ban-lockdown.te` grants `fail2ban_t` the
  specific search/read/write/ioctl/getattr/name_connect permissions
  `scripts/lockdown.sh`/`restore-network.sh` actually use -- discovered
  across three rounds (2026-07-10, then 2026-08-09) as fail2ban's
  actionban/actionunban exercised more of the scripts' real behavior: reach
  the quadlets under `/home/corporatetraveldc`, run `systemctl daemon-reload`/
  `restart` (including stat-ing the `systemctl` binary itself), edit
  `ollama.service.d/10-binding.conf` in place via `sed -i`'s temp-file+rename
  sequence, and send the ntfy incident notification. No broader domain
  transition or unconfined exec for fail2ban -- just the grants the scripts
  actually exercise, added as each round of enforcing surfaced the next one.
* `selinux/corporatetraveldc-fail2ban-cf-egress.te` grants `fail2ban_t` one
  thing -- name_connect on `pihole_port_t` -- so `scripts/cf-honeypot-ban.sh`
  (the honeypot's Cloudflare-edge IP Access Rule ban/unban action, see
  `docs/HONEYPOT_FAIL2BAN.md`) can reach `api.cloudflare.com` over HTTPS.
  Port 443 is labeled `pihole_port_t` on this box rather than the stock
  `http_port_t` (pre-existing Pi-hole customization, not introduced here),
  so any fail2ban action making an outbound HTTPS call needs this grant
  regardless of destination. `audit2allow`'s first pass over the same
  denials also proposed `self:process execmem` (for `grep -oP`'s PCRE JIT
  compiler) -- deliberately not granted; the script was rewritten to use
  `sed` instead, avoiding the permission rather than carrying it as a
  standing exception.

**Adding a new network-facing service:** add its `proxy_pass` target to
`nginx/conf.d/`, add one line to the `PORTS` list in
`selinux/label-nginx-backend-ports.sh`, and re-run
`selinux/apply-selinux-policy.sh`. Commit both changes together so the port
grant and the vhost that needs it are reviewed as one auditable unit.

### Container Network Isolation: Air-Gapped by Default

The same scoped-grant principle applies to container-to-host networking.
Podman's rootless networking (`pasta`) is air-gapped from the host by
default on this platform -- a container cannot reach anything bound to the
host's own interfaces unless it explicitly opts in. As of 2026-07-10, 15 of
18 containers in this stack need no host access at all and have none; the
LLM inference layer stays true to the "None (Air-gapped compatible)" row in
the Data Sovereignty & Isolation Matrix above -- Ollama listens on this
host's own Tailscale IP only, never on the public-WiFi-facing interface or
any container-default-reachable address.

**Three opt-in mechanisms, chosen by network mode and by what the target
service itself binds to, all scoped to the one container or the one
capability that needs them:**

* **Pasta-mode containers reaching a `0.0.0.0`-bound host service**
  (Podman's default, no `Network=` line or an explicit `Network=pasta`) opt
  in individually with `Network=pasta:--map-gw` on that container's own
  quadlet. This restores the `host.containers.internal` alias for that
  container only. See `corporatetraveldc-pusher.container` (reaching `ntfy`)
  for the pattern -- it carries a comment explaining what it needs to reach
  and why.
* **Pasta-mode containers reaching a service bound to a specific
  non-loopback host IP** (e.g. Ollama on its Tailscale address) address
  that IP directly instead -- `Network=pasta:--map-gw` doesn't apply here
  and isn't needed: the kernel refuses externally-arriving traffic destined
  for `127.0.0.0/8` regardless of pasta's routing (anti-spoofing), so
  `host.containers.internal` can never reach a strictly loopback-bound
  service under any pasta flag, while a real routable IP is reachable via
  normal outbound NAT with no opt-in at all. See `openwebui.container`
  (reaching Ollama) and `corporatetraveldc-runner.container` (reaching
  `dispatch`/`ultrafeeder`) for the pattern.
* **Bridge-mode containers** (`Network=<name>.network`, e.g.
  `corporatetraveldc-acarshub` on `acars-net.network`) have no per-container
  equivalent for the `host.containers.internal` alias: rootless Podman's
  bridge networking is itself tunneled through one shared `rootless-netns`
  pasta process, so that opt-in is host-wide --
  `pasta_options = ["--map-gw"]` in `.config/containers/containers.conf`.
  This is the one case where Podman's current architecture doesn't allow
  per-container scoping; it's documented there as affecting every
  bridge-networked container on the host, not just the one that needed it.
  (This mechanism only works for host services bound beyond loopback, same
  constraint as above.)

**`Network=host` requires a comment justifying it, the same way
`host.containers.internal` usage does.** It grants a container the host's
full network stack with no isolation boundary at all -- broader than either
opt-in mechanism above. `corporatetraveldc-runner.container` ran with
`Network=host` undocumented until 2026-07-10; it was removed in favor of
the same IP-scoped `PublishPort=` pattern `corporatetraveldc-web.container`
already used, since nothing about that service actually required full host
networking.

**Adding host-reach to a new container:** default to no `Network=` line at
all. If it needs to reach a host-bound service, use
`Network=pasta:--map-gw` and comment why. Reach for the bridge-mode
host-wide setting or `Network=host` only if the per-container mechanism
genuinely doesn't apply, and say so in a comment either way.

### External API Action Safety Pattern: Verify Success, Verify Identity

Standing rule for any script that calls an external API to enforce a
consequential, state-mutating action (bans, deletes, anything a monitoring
or security control fires automatically and unattended) -- established
2026-08-09 fixing `scripts/cf-honeypot-ban.sh` twice in one day, each round
catching a failure mode the previous round's fix didn't cover:

1. **Check the response body, not just the HTTP status code.** Some APIs
   (Cloudflare's included) return `200 OK` with `{"success": false, ...}`
   for validation failures -- an HTTP-status-only check treats that as
   success. Every call must check both: 2xx status AND the body's own
   success/status field.
2. **A lookup-then-mutate flow must verify the looked-up object's identity
   before acting on it, every time** -- never assume a filtered query
   actually filtered. Concretely: `cf-honeypot-ban.sh`'s unban path does
   `GET .../rules?configuration.value=<ip>` then `DELETE` on the first
   result; if that GET's filter silently fails to apply (see #3) and
   returns the full unfiltered list instead, blindly deleting
   `.result[0]` deletes an unrelated rule -- a different IP's legitimate
   ban lifted by accident, not the one being unbanned. Confirm
   `result[0]`'s own identity field matches the intended target before the
   mutating call, or abort loudly.
3. **`curl -X GET --data-urlencode ...` without `-G` sends the params as a
   request body, not a URL query string** -- many APIs (Cloudflare
   included) silently ignore a GET request's body and just return
   everything unfiltered. This is exactly the bug that made #2 a real risk
   rather than a theoretical one. Always pass `-G` when using
   `--data`/`--data-urlencode` with `-X GET`.
4. **Plain `curl -s` alone is not sufficient error detection** (see the
   `docs/HONEYPOT_FAIL2BAN.md` postmortem) -- it only signals non-zero on
   connection-level failure, never on an HTTP error response. Combine with
   #1's body check, always.

None of these are Cloudflare-specific despite the pattern being discovered
there -- apply all four to any future external-API action a jail, timer, or
watchdog fires unattended.

---

## 6. Request Trust Model: Network-Layer ACL vs. Application-Layer Tier Checks

Two independent, non-interchangeable enforcement layers, each covering a
traffic path the other structurally cannot see. This section exists
because the two were briefly conflated (2026-08-05 investigation, below)
-- the short version is that neither is redundant with the other, and
assuming otherwise is what left a real hole open.

**Network layer -- Tailscale ACL, tag-scoped grants (`tailscale/policy.hujson`).**
Governs one thing: which devices can even open a connection to a port
bound on the tailnet interface (100.x.x.x) at all. Default posture is
explicit-allow -- an untagged or improperly-tagged device gets nothing,
enforced by Tailscale's control plane before a packet reaches this box.
This is what actually protects the admin runner today: `runner/main.py`
(port 8001) is reachable only via `tailscale-dispatch-runner.conf`, a
Tailscale-cert HTTPS vhost bound to the tailnet IP. A device that isn't
tagged `tag:corporatetraveldc-server` (owner's own devices, via
`autogroup:self`) cannot reach that port to begin with -- there's no
header to forge, because there's no connection to forge it over.

**Application layer -- the `X-CTDI-Public` marker (`auth.py::resolve_tier`).**
Governs a completely different question: for a request that already
reached this app's shared backend process, did it arrive through the
public Cloudflare Tunnel or not. This exists because `dispatch.example.com`
(port 8000, the web API) is deliberately public -- Cloudflare Tunnel
traffic terminates at a local nginx listener and is proxied to the exact
same FastAPI process a tailnet request would reach. **Tailscale ACLs have
zero visibility into this path** -- tunnel traffic never touches the
tailnet interface, so no tag, grant, or ACL rule ever evaluates it. The
only thing standing between an anonymous internet request and an
elevated tier is whatever the app itself decides to trust, which is why
this specific check has to be correct on its own, independent of how
good the tailnet ACL is.

**2026-08-05 finding: the previous app-layer check was spoofable, and the
ACL work does not cover the gap.** `auth.py` previously trusted
`Tailscale-User-Login` and an `X-Forwarded-For` prefix of `"100."` as
proof of tailnet origin. The live public vhost forwarded
`X-Forwarded-For` via nginx's `$proxy_add_x_forwarded_for`, which
*appends* the connecting peer's address rather than replacing the
header -- so a plain internet client sending `X-Forwarded-For:
100.64.0.1` reached the app as `"100.64.0.1, 127.0.0.1"`, which still
satisfied a naive `.startswith("100.")` check. Verified exploitable
against the live `dispatch.example.com` endpoint (which
gates 7 Tier-1 API routes) with no token at all. The tailnet rebuild and
ACL/tag hardening done the same night do not touch this: that traffic
never reached the tailnet in the first place, so no amount of ACL
correctness closes an application-layer header-trust bug on a path the
ACL never sees.

**Fix**: nginx now sets `X-CTDI-Public: 1` via a literal
`proxy_set_header` on every location block in `dispatch.example.com.conf`
that proxies to port 8000. `resolve_tier()` forces Tier 0 whenever that
marker is present, before any token lookup runs -- so even a *valid*
bearer token presented through the tunnel cannot elevate. This is safe
specifically because `proxy_set_header` **replaces** the header for the
proxied request regardless of what the client sent (unlike
`$proxy_add_x_forwarded_for`'s append semantics above) -- empirically
verified in an isolated test harness: a client sending `X-CTDI-Public: 0`
still reached a test backend as `"1"`. The corollary risk this creates --
a location block that forgets the directive silently lets the client's
own value straight through -- was verified the same way and is why every
location block proxying to a public-facing port must carry it
explicitly; nginx location blocks that define any `proxy_set_header` of
their own do not inherit server-level ones.

Verified against the live (rebuilt) `corporatetraveldc-web` container
after the fix: a spoofed `X-Forwarded-For: 100.x.x.x` with no token is
rejected (403); a request carrying `X-CTDI-Public: 1` **and a real,
valid cert-tier bearer token** is still rejected (403) -- the marker
overrides a genuinely valid credential, which is the actual property
this fix needed to have; a request with no marker and a valid token
still succeeds (200), confirming genuine tailnet/direct access is
unaffected.

**`runner/main.py`'s CF-Connecting-IP check (`_is_trusted`) -- documented
past work, effectively superseded by the ACL for its one remaining live
path.** This function predates the marker approach (2026-07-21 bugfix
for the same class of problem: `ops.example.com`, then a
public vhost in front of the runner, was intermittently trusting a
Cloudflare-tunnel loopback hop as if it were a LAN origin). `ops.example.com`
was retired as a public endpoint 2026-08-02/03 -- confirmed no matching
nginx vhost exists for it today, so its CF-Connecting-IP branch is
currently unreachable in practice. The runner's only remaining live
front door is the tailnet-only vhost above, where reachability is
already gated by tag-scoped ACL grants before the request arrives --
making this specific check close to true network/app-layer redundancy
for the path that's actually live. Left as-is (not rewritten to the
marker model) since it isn't exploitable today and the stale
`ops.example.com` Cloudflare ingress rule that used to front
it has been removed outright (not just left dead) -- see
`cloudflared/config.yml`. If the runner is ever re-exposed publicly, it
needs the same `X-CTDI-Public` treatment `auth.py` now has, not a revival
of IP-header trust.
