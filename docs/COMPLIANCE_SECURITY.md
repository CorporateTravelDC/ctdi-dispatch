# CTDI Dispatch: On-Premises Architecture & Compliance Datasheet

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.

The Corporate Travel Dispatch Intelligence (CTDI) platform runs on the
operator's own hardware (one Raspberry Pi 5, Fedora, rootless Podman). By
default no operational data goes to a third-party cloud service and every
inference call stays on the box (`docs/DESIGN-PRINCIPLES.md`).

Earlier revisions described features that were never built (a "Compliance
Egress Hook Engine" as a product, PNR processing, SEC/FINRA integrations).
That content was removed 2026-08-03. This revision also removes the
superseded August history (the demo-runner three-way disagreement, the
pre-`require_admin` audit gap, the 90-day prune) and replaces it with the
state at HEAD.

---

## 1. Positioning: compliance-supportive infrastructure, not a compliance product

CTDI is a situational-awareness and dispatch platform (weather, TFRs,
NAS/ATCSCC programs, Amtrak, ADS-B/ASDE-X, a chauffeur runsheet) that runs in
parallel with whatever booking or dispatch software an operator already uses.
The `runsheet` table is ingest-only by design.

The narrow, accurate claim: an operator who runs CTDI alongside an existing
travel/booking platform does not add a new third-party data-handling risk,
because CTDI

- does not contact a cloud LLM by default. An Anthropic fallback exists in
  `src/common/llm.py` but is closed by two gates: `ANTHROPIC_FALLBACK_ENABLED`
  (live value `false` in `/etc/corporatetraveldc/dispatch.env`) and the
  per-call `allow_anthropic` flag;
- has no data model for PNR, reservation or payment data;
- keeps its own action log on the device (§3).

CTDI is not a recordkeeping system and does not by itself satisfy SEC Rule
17a-4, FINRA Rule 4511 or any other books-and-records rule. Whether those
apply is for the operator's counsel.

Separate from the dispatch data path, the platform now also hosts two
identity surfaces an assessor should know about: the **agent gateway**
(`agents.example.com`, OAuth 2.1 + remote MCP for cloud agents,
`src/common/agent_gateway.py`) and **Executive Standard reader invites**
(`invite.executivestandard.example.com`,
`src/common/es_invites.py`). Both store only hashes of their credentials.

---

## 2. Data sovereignty & isolation

| Data | Processing location | External egress | Storage |
| :--- | :--- | :--- | :--- |
| Operational feeds (weather, TFR, NOTAM, ATCSCC, Amtrak, ADS-B/ASDE-X, runsheet) | Rootless Podman containers | Read-only pulls from government/public sources | Local Postgres (primary since the 2026-09-20 cutover); SQLite remains for some paths and the demo |
| LLM inference | One native llama.cpp server, user unit `corporatetraveldc-llama.service`, bound to the tailnet IP `100.x.x.x:8093` only (`ss -ltn`) | None | Ephemeral |
| Audit log | `audit_log` table, local Postgres; uvicorn access log in journald | Optional, operator-enabled egress hook (§3); off live | Hash-chained on Postgres; past the retention horizon rows are archived, signed and moved to the operator's own Nextcloud, never deleted bare (§3) |
| Public demo | `corporatetraveldc-runner-demo` (:8005) + `demo-api`, reading a separate SQLite file | Public vhost `dispatch-runner.example.com` (200 on 2026-10-06); app-layer password gate active (`Environment=DEMO_MODE=true` in the quadlet, `DEMO_SESSION_SECRET` from `/etc/corporatetraveldc/demo-secrets.env`) | `/var/lib/corporatetraveldc-demo-source/demo-source.db`, `:ro` mount |

**Demo/production isolation.** The demo reads only
`/var/lib/corporatetraveldc-demo-source/demo-source.db`, populated by
`scripts/scrub-demo-source.py` (host-side, self-verifies against the signed
manifest, two-layer scrub with fail-closed allowlist from
`src/demo/scrub_rules.py`). `demo-api` and `runner-demo` have no mount of the
live tree. Design: `docs/DEMO_DATA_ISOLATION_PLAN_2026-08-13.md`.

**The demo file is frozen, not refreshed.**
`corporatetraveldc-demo-source-refresh.timer` is `disabled` and has never
fired (`LastTriggerUSec` empty); the file's mtime is still
2026-08-14 14:21Z. It changes only when someone runs
`scripts/scrub-demo-source.py` by hand. This errs on the safe side for
sovereignty (nothing newer than 2026-08-14 can be in it) and is a freshness
defect, not a containment one.

### Signed-manifest coverage: prevention vs detection

| Mechanism | Kind | Scope |
|---|---|---|
| `scripts/verified-exec.sh` as the `Exec=` wrapper | Prevention (refuses to start) | 39 live skill/fetcher quadlets (`grep -l verified-exec ~/.config/containers/systemd/*.container \| wc -l`). Scoped check: `src/`, `scripts/verify-manifest.sh`, `scripts/verified-exec.sh`, `security/trusted-signing-key.pub.asc`. |
| `src/common/llm.py::_verify_before_inference()` | Prevention | Every inference call. |
| Root-run installed copies (`scripts/install-root-copies.sh`) | Prevention | Root executes only `/usr/local/libexec/ctdc/*`; installation refuses unless the checkout verifies and each file matches its manifest hash; fail2ban actions call `installed-check.sh` (hash vs the root-owned `.ctdc-installed`) before each run. |
| `corporatetraveldc-integrity-sweep.timer` | Detection (15 min) | Whole tree. |
| `scripts/stack-refresh.sh` adversarial audit | Detection + gate | On each weekly/tripwire run: every running local container must run the signed `:latest` (or `:previous`/a recorded build) and pass `verified-exec.sh true` inside the running container; tracked-vs-live quadlets/units/skills; authorized_keys/sudoers/listening-port fingerprints. Any finding: ntfy p5, exit 2, nothing applied. |

**Not prevented: the long-running core containers.** `web`, `poller`,
`pusher`, `ingest-*` and `runner` launch their application from a bare `CMD`
(e.g. `Containerfile.web`: `CMD ["uvicorn", "web.main:app", ...]`; live
`podman inspect` shows an empty entrypoint). A modified image or bind-mounted
source would start and serve; the sweep and the stack-refresh audit detect it
afterwards. **NEEDS OPERATOR DECISION** (open since 2026-08-19): add a
verifying entrypoint to these images, or keep this paragraph as the honest
statement that their manifest control is detection.

**Live state at verification time:** `corporatetraveldc-integrity-sweep.service`
was `failed` -- the 18:05Z run found `scripts/stack-refresh.sh` modified in
the working tree and not covered by the signed manifest (`git status`: ` M
scripts/stack-refresh.sh`). The same condition makes
`corporatetraveldc-skill-grants` HOLD ("checkout does not verify against the
signed manifest -- nothing changed", 18:06Z). Both are the controls working as
designed; they clear on the next sign.

### The public mirror is self-verifying (2026-10-03)

`scripts/push-public.sh` builds, inside the scrubbed tree and before
`git commit-tree`, via `scripts/lib/public-manifest.sh`:

| File in the public tree | What it is |
|---|---|
| `MANIFEST.sha256`, `.asc` | Hashes of exactly the public files, detached-signed by the operator key. |
| `PRIVATE-TREE-MANIFEST.sha256`, `.asc` | The private manifest with every path replaced by `sha256(path)`, freshly signed. Blinded because private paths carry the real domain and personal names. |
| `security/signing.env`, `security/trusted-signing-key.pub.asc` | Re-injected so the fingerprint pin works. Public by nature. |

Gate order: scrub -> `verify_scrubbed()` -> blind + sign the private manifest
-> inject signing material -> generate + sign the public manifest ->
`scrub-public-tree.py --verify-only` on the final tree -> extract and run the
final tree's own `scripts/verify-manifest.sh` -> only then commit and push.
`--dry-run` skips the two GPG calls and the push and verifies with
`verify-manifest.sh --unsigned-dry-run` (used by no production gate).
`scrub_tree()` also rewrites entry names, after the first build found the
domain in nginx vhost file names.

A stranger can check a public clone with:

```
scripts/verify-manifest.sh
gpg --import security/trusted-signing-key.pub.asc
gpg --verify PRIVATE-TREE-MANIFEST.sha256.asc PRIVATE-TREE-MANIFEST.sha256
sha256sum README.md; printf 'README.md' | sha256sum   # look up the second hash in PRIVATE-TREE-MANIFEST.sha256
```

On the first build (2026-10-03) 785 of 1,028 public files were byte-identical
to signed private entries. [UNVERIFIED: whether a public push has run since;
not checked against the remote.]

---

## 3. Audit logging (as built)

### What writes `audit_log`

| Source | Actions | Where |
|---|---|---|
| `require_admin(action)` dependency factory | One hand-named action per admin route; **37** call sites: `src/web/main.py` 23, `routes/watchlist.py` 8, `routes/sectors.py` 5, `routes/remember.py` 1 (`grep -c 'require_admin("'`). Allowed calls write the redacted request body as `detail`; **denied** calls are audited too (`{"result": "denied"}`, since 2026-08-25), including a token used outside its `allowed_actions` scope (migration 0070). | `src/auth/auth.py` |
| CUI status read | `cui_status_read` (the only direct `db.audit()` call in the web layer) | `src/web/main.py`, `/api/v1/cui/status` |
| Board tokens | `board_refresh`, `board_mint_read_token`, `board_revoke_token`, `board_revoke_all_for_account` | `src/common/db.py` |
| Guardrails | `SR1_ALLOWED`, `SR1_INTERCEPT`, `SR2_ROUTE`, `SR2_BLOCK` | `src/common/guardrails.py` |
| Session grants / agent manifest signing | `session_grant_created`, `session_grant_revoked`, `agent_sign_manifest` | `src/common/db.py`, `scripts/sign-manifest.sh` |

The full admin action-name list: `admin.{healthz, feeds.list, audit.list,
tokens.list, version, triggers.list, feed.refresh, cps.force_recompute,
opsplan.force_snapshot, osint.force_scrape, alert.push, vip.list, vip.add,
vip.remove, bandwidth_priority.set, bandwidth_priority.clear,
approval_request.create, approval_request.get, approval_request.list,
watchdog.status}`, `osint.scope.{create,update,delete}`,
`watchlist.{flight.add, train.add, vessel.add, batch_remove, entry.remove,
flight.add_batch, train.add_batch, permanent.add_batch}`,
`sectors.{topic.throttle, topic.sanitize, topic.enabled, sector.silence,
feed.silence}`, `vault.remember`.

`/healthz` reports the rolling figure (`audit_count_24h` = 9 at
18:10Z 2026-10-06; `token_count_active` = 6).

### What does not write `audit_log` (evidence lives elsewhere)

The Wave 2 and 2026-10-05 governance surfaces keep their own evidence rows and
are **not** in the hash chain:

| Surface | Evidence | Table / file |
|---|---|---|
| Signed approvals (sudo gate, council, gateway link/hold/thaw, console login) | the approver account and the SSH signature itself | `approval_requests.resolved_by`, `.resolution_sig` (migration 0069) |
| Workspace contributions | the signed file in the vault (path chosen server-side, create-only) | Nextcloud `Series/contributions/...` |
| Agent gateway | connection / token state, revocations | `agent_connections`, `oauth_tokens`, `agent_gateway_settings` (0071, 0073) |
| ES reader invites | `es_invite_events` | 0072 |
| Console sessions | `console_sessions` | 0073 |
| Kill orders / liveness | archived order files + journal | `/var/lib/ctdc-liveness/orders-archive/` (root 0700), `journalctl -u corporatetraveldc-team-liveness` |

### Tamper evidence and retention (migration 0062, 2026-09-21/22)

- On Postgres a `BEFORE INSERT` trigger (`audit_log_chain_trg`) sets
  `prev_hash` and `row_hash = sha256(prev_hash|event_time|action|tier|token_prefix|remote_addr|detail)`
  under an advisory lock. Any edit, insertion or deletion breaks the chain
  from that point. SQLite gets no trigger (dev/rollback path only).
- There is **no** UPDATE/DELETE-blocking trigger and **no** writer/owner role
  split: the `dispatch` role that writes rows can also rewrite them. The chain
  detects a rewrite only if someone verifies it against a signed checkpoint.
- Retention: the 90-day bare prune was **retired 2026-09-22**
  (`poller/skills/audit_log_prune.py` is a refusing stub;
  `db.prune_audit_log()` raises). `poller/skills/audit_log_archive.py`
  (scheduled in `src/poller/main.py` as `audit-log-archive`) takes rows past
  `AUDIT_ARCHIVE_RETENTION_DAYS` (default 90), uploads a tarball to the
  operator's Nextcloud (`archives/audit_log`), records a GPG-signed checkpoint
  (`audit_checkpoints`) and a signed stub (`audit_archive_stubs`), and deletes
  only after the upload is confirmed. It ran `ok (rc=0)` at 03:00Z and 08:04Z
  on 2026-10-06. Rows have existed only since 2026-08-16, so the horizon has
  not been reached and the signing path has not yet archived production rows
  [inferred from the dates; UNVERIFIED by query].
- **No chain verifier exists in the repo** (no script or test reads
  `row_hash` back). Until one exists the chain is evidence that can be
  checked, not a check that runs.

### The access log

`Containerfile.web` runs uvicorn with `--proxy-headers
--forwarded-allow-ips=127.0.0.1 --log-level info` (forwarded headers are
trusted only from nginx's own hop). Every request lands in
`journalctl --user -u corporatetraveldc-web` with client IP, method, path and
status, but no actor identity or payload. Retention is journald's size budget,
not a date.

### Token expiry

Expiry is enforced in `db.lookup_token()` (`expires_at IS NULL OR expires_at >
now`), which both `resolve_tier()` and `require_admin()` use. Since the August
finding, `ctdc-token create` defaults to `--expires 365` days
(`src/ctdc_token/cli.py`); `--expires 0` still mints a non-expiring token.
Admin tokens may carry an `allowed_actions` fnmatch list
(`scripts/mint-agent-api-token.sh` mints account-labelled, scoped tokens).
[UNVERIFIED: how many of the 6 active tokens still have `expires_at IS NULL`,
and whether the retired `mcpo-corporatetraveldc-dispatch-mcp` admin token was
revoked; the live table was not queried.]

### Optional outbound egress hook

Off unless configured. `corporatetraveldc-compliance-egress-push.timer`
(enabled, `OnCalendar=*:0/5`) runs
`common.compliance_egress.push_pending_audit_events()`, which returns
immediately unless `COMPLIANCE_HOOK_ENABLED=true` and `COMPLIANCE_TARGET_URL`
is set (live: `COMPLIANCE_HOOK_ENABLED=false`). When enabled it POSTs unshipped
`audit_log` rows (`record_id, event_time_utc, source_node, action, tier,
token_prefix, remote_addr, detail`), tracked by `egress_status` /
`egress_attempts` / `egress_last_error`, giving up after
`COMPLIANCE_RETRY_LIMIT` (default 5). `COMPLIANCE_TARGET_URL` and
`COMPLIANCE_TARGET_AUTH_HEADER` are secrets.

---

## 4. ISO/IEC 42001

CTDI is **not** ISO/IEC 42001 certified; no accredited audit has taken place.
The control-area mapping and the honest gap list are in
`docs/ISO_42001_ALIGNMENT.md`.

---

## 5. Host integration & hardening

### Resource caps

73 of 74 live `.container` quadlets carry `Memory=` (with an equal
`--memory-swap` on the core services); the exception is
`corporatetraveldc-execstandard-verifier` (no `Memory=`, no `CPUQuota=`).
`CPUQuota=` is on the long-running services but absent from 36 quadlets, most
of them timer-driven skill oneshots (values in
`docs/GUARDRAILS_JUSTIFICATION.md`). The llama.cpp server is capped by its own
unit: `CPUQuota=200%`, `MemoryHigh=7168M`, `MemoryMax=8448M`,
`MemorySwapMax=0`. The 21 `corporatetraveldc.*` Modelfiles still say
`PARAMETER num_thread 2`; they are not live configuration (manifest-verified
persona text for `src/common/personas.py`).

### SELinux: scoped grants over booleans

Every SELinux exception is a named module or a single port label, never a
domain-wide boolean. Eight modules in `selinux/`:
`corporatetraveldc-{sdr-usb, nginx-proxy, fail2ban-lockdown, fail2ban-cf-egress,
secrets-isolation, tailscaled, logind-userns, virtqemud}.te`, built by
`selinux/apply-selinux-policy.sh`; nginx backend ports are labelled
individually by `selinux/label-nginx-backend-ports.sh`. Notable:

- `fail2ban-cf-egress.te` grants `fail2ban_t` one thing, `name_connect` on
  `pihole_port_t` (port 443 carries that label on this box), so the
  Cloudflare ban action can reach the API. `self:process execmem` was refused;
  the notes script uses `sed` instead of `grep -P`.
- `secrets-isolation.te` gives `fail2ban_t` `search` on `ctdc_secrets_dir_t`
  and read on exactly `ctdc_secret_cf_honeypot_t`. **Caveat found
  2026-10-06:** the fail2ban action has read the token from
  `/etc/corporatetraveldc/cf-honeypot.token` since 2026-10-03, and that file is
  labelled `container_file_t` (the whole `/etc/corporatetraveldc` tree is,
  by `apply-selinux-policy.sh` step 3). The isolated type now labels only the
  stale copy in `~/.secrets`. See `docs/SECRETS_NAMING_AND_ISOLATION.md`.

Adding a network-facing service: add its `proxy_pass` target in
`nginx/conf.d/`, one line in `label-nginx-backend-ports.sh`'s `PORTS`, re-run
`apply-selinux-policy.sh`, commit both together.

### Container network isolation

Rootless pasta networking cannot reach host-bound services unless a container
opts in. Live counts (2026-10-06): 74 `.container` quadlets; 29 carry
`Network=pasta:--map-gw` (including web and pusher); 6 use a named bridge
network; none uses `Network=host` (the runner's quadlet mentions it only in a
comment recording its removal). The bridge-mode opt-in is host-wide
(`pasta_options = ["--map-gw"]` in `~/.config/containers/containers.conf`).
The scoped-opt-in rule stands: no `Network=` line by default;
`Network=pasta:--map-gw` with a comment when a host service is needed;
`Network=host` only with a written justification.

No container mounts `/etc/corporatetraveldc`; secrets arrive through
`EnvironmentFile=` (63 quadlets read the non-secret `dispatch.env`; the
first-party services read only their `/etc/corporatetraveldc/svc/<svc>.env`;
none reads `dispatch-secrets.env`).

### External API action safety pattern

For any unattended script that calls an external API to mutate state (bans,
deletes): (1) check the body's own success field, not only the HTTP status;
(2) in lookup-then-mutate flows verify the looked-up object's identity before
mutating; (3) use `curl -G` with `--data-urlencode` on GETs; (4) `curl -s`
alone hides HTTP errors. Established fixing `scripts/cf-honeypot-ban.sh`
(2026-08-09).

---

## 6. Request trust model: network ACL vs application tier

Two independent layers; neither covers the other's path.

**Network layer: Tailscale.** Ports bound to `100.x.x.x` are reachable
only from the tailnet; `tailscale0` sits in firewalld's `trusted` zone, while
the LAN zone (`enu1`, `wld0`) opens only ssh, mdns, samba-client,
dhcpv6-client and 80/tcp. The admin runner (`:8001`) and the operator console
(`/console`) are served only by `nginx/conf.d/tailscale-dispatch-runner.conf`
(server name `corporatetraveldc-dispatch.tailxxxxxxx.ts.net`, 443 closed on the
LAN). [UNVERIFIED: the live tailnet ACL; the tracked
`tailscale/policy.hujson` was last changed 2026-08-05 and does not contain
`tag:headless-admin`, which the node advertises.]

**Edge layer: Cloudflare Access.** On 2026-10-06 the public
`dispatch.example.com` answered 302 to the Cloudflare Access login
for `/api/v1/tfr`, `/console` and `POST /api/v1/approvals/{id}/resolve`, and
passed through `/robots.txt`, `/api/v1/board/health`,
`/api/v1/vault/research/list` (401 from the app) and
`/admin/approval-requests/{id}/resolve` (404 from the app for an unknown id).
[UNVERIFIED: the Access application list itself; the bypass apps are inferred
from these responses.]

**Application layer: `X-CTDI-Public`.** All proxied locations in
`nginx/conf.d/dispatch.example.com.conf` set `X-CTDI-Public: 1`
with `proxy_set_header` (replace, not append); `resolve_tier()` and
`resolve_identity()` return Tier 0 / anonymous when it is present, before any
token lookup, so even a valid admin token cannot elevate through the tunnel.
This replaced (2026-08-05) a spoofable `X-Forwarded-For` "100." prefix check.
`agents.example.com` does not set the marker; it proxies only the
OAuth/MCP paths (regex location, everything else `return 404`), which
authenticate with gateway tokens, not tier tokens.

**Runner `_is_trusted()`** (`src/runner/main.py`) is an IP classifier
(CF-Connecting-IP, else client/X-Forwarded-For, against Tailscale CGNAT,
RFC1918, loopback). It decides only whether the runner injects its cert-tier
service token for the operator-only path set (`docs/auth-token-proxy-pattern.md`);
reachability of `:8001` is the network layer's job.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-security.md`.


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet

~~The Corporate Travel Dispatch Intelligence (CTDI) platform is architected for zero-trust, on-premises deployment. It runs entirely within the operator's own managed environment -- no data leaves the deployment to any third-party cloud service by default, and every inference call stays local (see `DESIGN-PRINCIPLES.md`).~~

~~**Framing note (2026-08-03):** this document was previously written with several sections describing features that were never built -- a "Compliance Egress Hook Engine," direct PNR/reservation-record processing, and specific SEC/FINRA integration claims that don't correspond to anything in this codebase. That content has been replaced below with an accurate description of what the platform actually does and how it's meant to be positioned. The platform does not process travel bookings, PNRs, or reservation records itself, and does not claim to. What it *does* do -- run entirely on-premises, log its own actions locally and immutably, and stay out of a client's existing travel/booking data path -- is real, and is what this document now describes.~~

**~~1. Positioning: Compliance-Supportive Infrastructure, Not a Compliance Product~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 1. Positioning: Compliance-Supportive Infrastructure, Not a Compliance Product

~~CTDI is not a recordkeeping system, a PNR processor, or a regulatory filing tool. It is a situational-awareness and dispatch platform (weather, TFRs, NAS/ATCSCC ground programs, Amtrak status, ADS-B/ASDE-X ground movement, and a chauffeur/executive-transport runsheet) that an operator runs on their own hardware, in parallel with whatever travel-management, booking, or dispatch software they already use (a GDS, a limo-dispatch platform like LimoAnywhere, a PBX/call system like RingCentral or 3CX, or an internal scheduling tool). The `runsheet` table in this platform is deliberately ingest-only today for exactly this reason -- it's built to receive trip data *from* an operator's existing system, not to replace it or to become the operator's system of record (see `runsheet_ingest_only_until_limoanywhere_tiein` in project history).~~

~~The compliance-relevant claim this document supports is narrow and accurate: **an operator who runs CTDI alongside their existing travel/booking platforms is not introducing a new third-party data-handling risk into their compliance posture**, because CTDI:~~

- ~~Never contacts a cloud LLM or third-party data-processing API by default (see `DESIGN-PRINCIPLES.md` §2) -- there is no vendor in the loop reading operational data.~~
- ~~Never receives or stores PNR, reservation, or payment data -- it has no data model for any of that (confirmed: no such table exists in this platform's schema).~~
- ~~Keeps every action it takes -- feed fetches, admin actions, alert fires -- in a local, append-only audit log that never leaves the device (§3 below).~~

~~This is a statement about *what CTDI adds to* an operator's existing compliance posture (nothing that wasn't already there, no new external dependency, no new data-handling surface), not a claim that CTDI itself is a regulatory-compliance product, that it satisfies SEC Rule 17a-4 or FINRA Rule 4511 recordkeeping requirements on its own, or that it has been reviewed by any regulator. Those rules govern a firm's own books-and-records obligations for its own regulated activity; whether and how they apply is a determination the operator's own compliance counsel makes, not something this platform certifies. If an operator's existing travel/booking platform has its own recordkeeping obligations, CTDI's job is to stay out of that data path entirely -- which, by having no PNR/booking data model at all, it does by construction.~~

**~~2. Data Sovereignty & Isolation~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 2. Data Sovereignty & Isolation

| ~~Data Classification~~ | ~~Processing Location~~ | ~~External Network Escape~~ | ~~Storage State~~ |
| :--- | :--- | :--- | :--- |
| ~~**Operational feeds** (weather, TFR, NOTAM, ATCSCC ops-plan, Amtrak, ADS-B/ASDE-X, runsheet)~~ | ~~Internal Podman containers~~ | ~~None by default -- read-only pulls from government/public-interest sources only (see `DESIGN-PRINCIPLES.md` §3)~~ | ~~Local database, on-device — SQLite (WAL mode) or local Postgres per `DISPATCH_DB_BACKEND` (see `docs/POSTGRES_MIGRATION.md`); on-device either way~~ |
| ~~**LLM Inference**~~ | ~~Native host llama.cpp (`llama-server`) systemd user units — per-tier, tailnet-IP-bound (the Ollama daemon was retired 2026-08-27)~~ | ~~None (air-gapped compatible)~~ | ~~Ephemeral -- no query or response is sent to any external provider~~ |
| ~~**Audit Logs**~~ | ~~Systemd journald + local `audit_log` table (SQLite or local Postgres per `DISPATCH_DB_BACKEND` — `audit_log` moves with the write path, see `docs/POSTGRES_MIGRATION.md`)~~ | ~~None (0% outbound)~~ | ~~Append-only, local disk, 90-day retention via `poller/skills/audit_log_prune.py` (daily, `db.prune_audit_log(days=90)`)~~ |
| ~~**Public demo playback**~~ | ~~Dedicated `demo-api`/`runner-demo` containers, sovereign SQLite file (see below)~~ | ~~**A public nginx vhost exists** (`dispatch-runner.example.com` → `127.0.0.1:8005`); the app-layer password gate is **active — `DEMO_MODE=true` + `DEMO_SESSION_SECRET` are set in the runner-demo Quadlet since the 2026-08-24 restore** (see the demo-runner note in §2)~~ | ~~Sovereign file, physically separate directory, `:ro` container mount~~ |

~~Retention note: an earlier 2026-08-19 correction here recorded that the 90-day claim had no implementation behind it. That gap was closed the same day — `db.prune_audit_log(days=90)` plus the daily `audit_log_prune` poller skill (§3) make the 90-day figure real.~~

~~By default, the platform binds its web interfaces, backend processes, and LLM orchestration layer strictly to the host environment or internal container-network interfaces. Operational data -- flight tracks, TFRs, weather, watchlist entries, runsheet trips -- is processed and stored entirely on the deployed device. Nothing here describes or requires third-party PNR data ingestion; if a future runsheet integration (LimoAnywhere/RingCentral/3CX) is built, the same isolation principles apply to whatever trip-level data that integration actually carries, and this section will be updated to reflect the real data model at that time rather than a hypothetical one.~~

~~**Demo/production isolation (2026-08-14, closing F6).** The public demo instance reads from `/var/lib/corporatetraveldc-demo-source/demo-source.db` -- a physically separate SQLite file, in a separate top-level directory from the live `/var/lib/corporatetraveldc` tree, populated exclusively by `scripts/scrub-demo-source.py`. That script is the only component permitted to touch both sides: it runs host-side (never inside a container that could be conflated with the public-facing demo surface), self-verifies against the signed manifest before running, reads live data read-only, and every row passes a two-layer scrub (`src/demo/scrub_rules.py` substitution + fail-closed allowlist verification, modeled on `scripts/scrub-public-tree.py`'s public-mirror discipline) before promotion -- a row that still matches a forbidden pattern after substitution is dropped, never shipped with a warning. `demo-api` and `runner-demo` hold no live-DB connection and no live-directory mount at all; not an application-level choice not to read it, no filesystem path to it exists. See `docs/DEMO_DATA_ISOLATION_PLAN_2026-08-13.md` for the full design and `docs/PENTEST_CLEARANCE_CHECK_2026-08-13.md` for the finding this closes.~~

~~**The isolation above is real; the refresh below is not (corrected 2026-08-19).** This section previously stated that a scheduled refresh (`corporatetraveldc-demo-source-refresh.timer`, nightly 04:45 ET) "keeps the demo tracking real platform growth". It does not, and never has:~~

* ~~`systemctl --user list-unit-files` reports the timer as **`disabled`** (the `.service` is `static`).~~
* ~~`LastTriggerUSec` is empty and `journalctl --user -u corporatetraveldc-demo-source-refresh.service` returns **"-- No entries --"**. The unit has never fired, not once.~~
* ~~`/var/lib/corporatetraveldc-demo-source/demo-source.db` is consequently **frozen at its 2026-08-14 10:21 promotion** (mtime confirmed; ~1.88 GB).~~

~~The bridge from staging to the sovereign demo file is therefore manual today: the file only changes when someone runs `scripts/scrub-demo-source.py` by hand. Note which direction this errs in — a frozen demo file is the *conservative* failure mode for data sovereignty (it cannot leak anything newer than 2026-08-14, and the two-layer scrub still gated everything in it), so this is a freshness and accuracy defect, not a containment breach. It is recorded here because a compliance document that claims an automated control which has never executed is making a false statement regardless of which way the failure leans.~~

> ~~**RESOLVED 2026-08-24, re-verified 2026-09-03 — the three-way disagreement below is settled.** The runner-demo Quadlet now sets `Environment=DEMO_MODE=true` and `DEMO_SESSION_SECRET` explicitly (its header comment records the 2026-08-2x decision), the 2026-08-15→24 crash loop was fixed by mounting a dedicated `/var/lib/corporatetraveldc-demo` state dir (commit `0a7f643`), and live state confirms: unit `active (running)`, `NRestarts=0`, `:8005/healthz` → `ok`, and the public vhost serves 200. The password gate, signal sanitization, and ntfy suppression are therefore **armed**, and the exposure model is the operator-accepted public-demo-on-sanitized-data (2026-08-20 directive; no CF Access policy fronts the hostname). The section below is kept as the record of the pre-resolution state and of how the three authorities disagreed.~~

~~**Demo runner exposure and `DEMO_MODE` — three sources disagree, and the disagreement is not resolved here (2026-08-19). [SUPERSEDED — see the resolution note above.]** The Data Sovereignty table above previously described the demo runner as internal-only. Live state is contradictory, and each of the three authorities says something different:~~

1. ~~**The Quadlet says internal-only.** `corporatetraveldc-runner-demo.container`'s header comment reads "Internal-only for now (127.0.0.1 + Tailscale IP only, port 8005). No nginx vhost, no Cloudflare Tunnel ingress, no public DNS -- exposing this to the public `dispatch-runner.example.com` hostname is a separate, deliberate step ... and should get an explicit go-ahead rather than being silently wired." The repo copy and the live `~/.config/containers/systemd/` copy are **byte-identical**.~~
2. ~~**nginx says otherwise.** `/etc/nginx/conf.d/dispatch-runner.example.com.conf` **does exist** (dated 2026-08-01) and proxies both `/` and `/api/demo/login` to `127.0.0.1:8005`, with rate-limit zones on each. Its own header comment asserts "`DEMO_MODE=true` ... Password-gated at the app layer (runner main.py's `proxy_dispatch()` hard gate + `/api/demo/login`), not here — this vhost is deliberately open over the tunnel exactly like `ops.`".~~
3. ~~**The app says the gate is off.** `DEMO_MODE` **is set nowhere**: not in the Quadlet (neither copy), not in `/etc/corporatetraveldc/dispatch.env`, not in `/etc/corporatetraveldc/dispatch-secrets.env`. `src/runner/main.py:47` reads `os.getenv("DEMO_MODE", "false")`, so it evaluates **false**. Every demo protection is conditioned on that flag and is therefore **inert**: the `proxy_dispatch()` hard gate (`main.py:1612`), the login/session handlers (`:347` in `/api/demo/login`, `:381` in `/api/demo/status`), signal sanitization (`_should_sanitize_signals()`, `:724`), and ntfy suppression (`:1848` on `/api/demo/webhook-log`, `:1907` on `/api/ntfy/stream` via `_should_sanitize_signals()`). Line numbers drift — re-derive with `grep -n DEMO_MODE src/runner/main.py` (re-verified live 2026-08-23; an earlier revision here cited `:1548`/`:322`/`:356`/`:699`/`:1799`/`:1852`, all since moved). The nginx comment's claim that the app layer is password-gating is, as of today, false.~~

~~**Why nothing is exposed right now:** `corporatetraveldc-runner-demo.service` is crash-looping — `sqlite3.OperationalError: unable to open database file` on startup, first seen 2026-08-15 14:30, `NRestarts` 49,589 as of 2026-08-23 13:5x EDT (~49,000 an hour earlier — roughly one crash every 9 s), still climbing (re-query with `systemctl --user show corporatetraveldc-runner-demo.service -p NRestarts -p SubState` rather than citing this figure; a `podman ps` snapshot can catch the container in a momentary `Up N seconds` between crashes — trust `SubState`, which reads `auto-restart`/`start`, not the snapshot). Port 8005 has **no listener at all** — corrected 2026-08-23, an earlier revision here said "has a listener but refuses connections", which is not what the box shows: `ss -ltnp | grep 8005` returns nothing and `curl http://127.0.0.1:8005/` fails to connect (exit 7, HTTP code `000`), because the process never gets far enough to bind. Either way the vhost 502s. **The only thing preventing an ungated public demo surface is a crash.** That is not a control.~~

<del>~~**NEEDS OPERATOR DECISION:** DEMO_MODE is unset everywhere…~~ — **RESOLVED 2026-08-24 (see the note at the top of this block):** the crash was fixed *and* `DEMO_MODE=true` was set explicitly in the same window, so the ungated-surface scenario this decision guarded against did not occur. The 2026-08-20 exposure acceptance stands (intentionally-public demo over sanitized data). The 2026-08-23 CF Access listing is kept as history: eight apps existed (`dispatch`, `dispatch-approval-resolve-bypass`, `dispatch-vault-research`, `dispatch-robots-bypass`, `dispatch-board-public-bypass`, `openwebui`, `pihole`, `ollama`) and none covered `dispatch-runner.example.com` — the demo's gate is the app-layer password session, not Access.</del>

~~Two secondary points fall out of the same decision and should be settled with it rather than separately: whether the Quadlet's "no nginx vhost" comment or the nginx vhost itself is the mistake (they cannot both be right), and whether `DEMO_SESSION_SECRET` — which `main.py` requires to match what `src/demo/profiles.py` signs tokens with — is correctly populated, since the password gate has never actually run in this deployment and so has never been exercised end-to-end.~~

~~Housekeeping note found while grounding this section: several code comments elsewhere in the repo cite a "Signed Manifest Integrity" section of this document that doesn't exist here under that exact heading -- the mechanism itself (`scripts/sign-manifest.sh`/`verify-manifest.sh`, `_verify_before_inference()` in `src/common/llm.py`, the self-check every privileged script in this section runs before proceeding) is real and live; the cross-reference is drift, not a missing control. Flagging here rather than silently fixing every comment, since some of those comments may be intentionally pointing at a future consolidated section.~~

**~~Signed-manifest coverage: what is actually enforced, and what is not~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 2. Data Sovereignty & Isolation › Signed-manifest coverage: what is actually enforced, and what is not

~~Added 2026-08-19, because "the self-check every privileged script runs before proceeding" above is true of the *scripts* but is easily read as covering the running services, and it does not. Verified against the Containerfiles and against the live images.~~

~~**Genuinely enforced — three mechanisms, all real:**~~

* ~~**The timer-triggered skill/fetcher quadlets** (**33** as of 2026-08-23 17:3x — this count grows with every new skill, so re-derive it rather than trusting the figure), which invoke `scripts/verified-exec.sh` as their `Exec=` wrapper (e.g. `Exec=scripts/verified-exec.sh python3 src/poller/skills/aam_daily_watch.py`). Verify with `grep -l verified-exec ~/.config/containers/systemd/*.container | wc -l`. These genuinely refuse to run against a stale manifest.~~
* ~~**`src/common/llm.py` before every inference** — `_verify_before_inference()` (`llm.py:116`) runs `scripts/verify-manifest.sh` (`_VERIFY_SCRIPT`, `llm.py:73`) from the generate path (`llm.py:1053` — line numbers drift, `grep -n _verify_before_inference src/common/llm.py`). This is the control that matters most for the data-sovereignty claims in this section, and it holds.~~
* ~~**The 15-minute `corporatetraveldc-integrity-sweep` timer** (`OnUnitActiveSec=15min`, enabled and active), which re-verifies the whole tracked tree out of band.~~

~~Privileged host-side scripts also self-verify individually — including `scripts/scrub-demo-source.py`, which shells out to `verify-manifest.sh` before promoting anything to the demo file, exactly as §2 describes.~~

~~**Not enforced — the four long-running core containers.** `web`, `poller`, `pusher`, and `ingest` all `COPY` `verify-manifest.sh` into the image but never invoke it: each `Containerfile` ends in a bare `CMD` that launches the application directly, with no `ENTRYPOINT` wrapper.~~

*Superseded block:*
```text superseded
Containerfile.web      CMD ["uvicorn", "web.main:app", ...]
Containerfile.poller   CMD ["python3", "-m", "poller.main"]
Containerfile.pusher   CMD ["python3", "-m", "pusher.main"]
Containerfile.ingest   CMD ["python3", "-m", "ingest.main"]
```

~~Confirmed on the live images, which report an empty entrypoint:~~

*Superseded block:*
```text superseded
$ podman inspect systemd-corporatetraveldc-web --format '{{.Config.Entrypoint}} {{.Config.Cmd}}'
[] [uvicorn web.main:app --host 0.0.0.0 --port 8000 --proxy-headers ...]
```

~~`Containerfile.runner` is a step further out: it does not even `COPY` `verify-manifest.sh` into the image, so the runner has no local means of checking the manifest at all.~~

~~**What this means in practice.** A statement that "container entrypoints run `verify-manifest.sh` before executing" is accurate for the timer-triggered skill containers and inaccurate for the long-running services. The residual protection for those four is real but different in kind and in timing: the integrity sweep detects tampering within 15 minutes *after the fact*, and `llm.py` blocks the inference path specifically — but a modified `web.main` or `ingest.main` would start and serve traffic in the interim. This is a detection control on those services, not a prevention control, and the distinction belongs in a compliance datasheet.~~

~~**NEEDS OPERATOR DECISION:** the four long-running core containers (web, poller, pusher, ingest) launch their application directly via `CMD` and never run `verify-manifest.sh`, despite having it in the image; `Containerfile.runner` does not ship it at all. Either add a verifying entrypoint wrapper to those five images, or state explicitly in this datasheet that manifest enforcement for long-running services is after-the-fact detection via the 15-minute integrity sweep rather than start-time prevention. Do not leave the two described as equivalent.~~


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 2. Data Sovereignty & Isolation › The public mirror is self-verifying (2026-10-03)

~~Until 2026-10-03 the public mirror shipped with **no manifest at all**: the scrubber (correctly) dropped the private `MANIFEST.sha256(.asc)` -- it hashes private bytes the scrub rewrites, and its paths carry strings the scrub substitutes -- and nothing replaced it. The repository that exists to showcase this integrity model did not carry it. `scripts/push-public.sh` now builds, inside the scrubbed tree and before `git commit-tree`, via `scripts/lib/public-manifest.sh`:~~

| ~~File in the public tree~~ | ~~What it is~~ |
|---|---|
| ~~`MANIFEST.sha256`, `MANIFEST.sha256.asc`~~ | ~~Hashes of exactly the public files (same format, ordering and exclusions as `sign-manifest.sh`), detached-signed by the operator key.~~ |
| ~~`PRIVATE-TREE-MANIFEST.sha256`, `.asc`~~ | ~~The private tree's manifest with every **path replaced by `sha256(path)`**, freshly signed by the operator key. Verbatim was rejected: the private paths include the real domain in 14 nginx vhost filenames and the operator's name in two key filenames. Blinding keeps the attestation without disclosing private-only names. Covered by the public manifest.~~ |
| ~~`security/signing.env`, `security/trusted-signing-key.pub.asc`~~ | ~~Re-injected from the private tree so the pinned-fingerprint check works. Both are public-by-nature (a fingerprint; a public key the operator confirmed is deliberately public).~~ |

~~Gates, in order: scrub -> `verify_scrubbed()` on the scrubbed tree -> blind + sign the private manifest -> inject signing material -> generate + sign the public manifest -> `scrub-public-tree.py --verify-only` on the **final** tree (the injected files get the same secret-value / literal / email / UUID / IPv4 scan) -> extract the final tree and run **its own** `scripts/verify-manifest.sh` exactly as a stranger would (signature, key pin, full-tree coverage, every hash) -> only then `commit-tree` and push. `--dry-run` performs everything except the two GPG calls and the push, verifies with `verify-manifest.sh --unsigned-dry-run` (a flag that exists only for this path and prints a banner; no production gate ever uses it), and leaves the extracted tree on disk for inspection.~~

~~Side effect found by the first build: the public manifest lists paths, the final-tree scan read it as content, and flagged `example.com` in nginx vhost **filenames** that had been reaching the public mirror unrenamed all along (the scrubber rewrote blob bytes, never entry names). `scrub_tree()` now runs the same substitution + regex pipeline over entry names, with a collision guard.~~

~~**What a stranger can verify from a public clone**~~

*Superseded block:*
```text superseded
git clone <public-url> ctdi-dispatch && cd ctdi-dispatch
scripts/verify-manifest.sh            # signature by the pinned key, every file, full coverage
gpg --import security/trusted-signing-key.pub.asc
gpg --verify PRIVATE-TREE-MANIFEST.sha256.asc PRIVATE-TREE-MANIFEST.sha256
# provenance link: any public file whose bytes the scrub left untouched
# appears in the private attestation under sha256(path):
sha256sum README.md; printf 'README.md' | sha256sum   # look the second hash up in PRIVATE-TREE-MANIFEST.sha256
```

~~On the first build 785 of 1028 public files were byte-identical to signed private entries. The remaining 243 are the scrubbed ones (domain / identity substitutions), which by construction cannot match.~~

**~~3. Audit Logging (Real, As-Built)~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 3. Audit Logging (Real, As-Built)

> ~~**Correction, 2026-08-19 — the headline claim in this section was false.** This section previously opened by stating that the audit log records "every admin action taken through the platform's API". It does not record **any** admin action. This is a compliance claim, so it is corrected in place rather than softened: the mechanism below is real, the table is real, the append-only property is real, and the egress hook is real — but admin-action coverage was documented as satisfied when no code path implements it.~~

**~~What is actually audited~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 3. Audit Logging (Real, As-Built) › What is actually audited

~~Verified 2026-08-19 against `src/web/main.py` and the live database:~~

* ~~**Tier-2 CUI reads.** The **only** literal `db.audit()` call in the entire web layer is at `src/web/main.py:1776` (line numbers drift — `grep -n "db.audit(" src/web/main.py src/web/routes/*.py`), on the Tier-2 `/api/v1/cui/status` read. This one is genuine and works as described. Note this is now a statement about *direct* call sites only: since the `require_admin` factory landed (see RESOLVED below), every admin route also writes an audit row, but it does so through the shared dependency in `src/auth/auth.py`, not through a `db.audit()` call in the route file.~~
* ~~**SR-1 / SR-2 skill-runtime events.** The guardrail layer writes `SR1_ALLOWED`, `SR1_INTERCEPT`, `SR2_ROUTE` and `SR2_BLOCK` rows. These are real and are the substantive audit content today.~~
* ~~**`board_refresh`.** Rotation activity on the research board, written via `audit()` from `src/common/db.py` — the real call sites are inside `board_refresh_token()` (`grep -n '"board_refresh"' src/common/db.py`; four call sites as of 2026-09-03 — the 120s grace-relay retry path added a fourth, audited with `"relayed": true`).~~

**~~What was not audited (pre-2026-08-19)~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 3. Audit Logging (Real, As-Built) › What was not audited (pre-2026-08-19)

~~This describes the state before the `require_admin` factory landed; see RESOLVED below.~~

~~**No `/admin/*` endpoint calls `db.audit()`.** `src/web/main.py` declares **23 endpoints** carrying `Depends(require_admin)`, and not one of them writes an audit row. Unaudited admin endpoints include, among others:~~

* ~~`POST /admin/push-alert` — sends an operator-authored push alert~~
* ~~`POST /admin/vip` and `DELETE /admin/vip` — mutate the VIP watch set~~
* ~~`POST /admin/bandwidth-priority` and `DELETE /admin/bandwidth-priority` — suspend and resume live SWIM feed ingestion~~
* ~~every `/admin/force-*` recompute trigger~~

~~Note that `/admin/audit` (`main.py:2049`) *reads* the log; reading it is not the same as populating it, and its presence has probably helped this gap go unnoticed.~~

**~~Live evidence~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 3. Audit Logging (Real, As-Built) › Live evidence

~~The database now shows the gap closed. Snapshot taken 2026-08-23 13:46 EDT — this table grows continuously (it moved 1,547 → 1,809 in the ~50 minutes between two passes on this same date), so **re-derive rather than cite it**:~~

*Superseded block:*
```text superseded
# Postgres since the 2026-09-20 cutover (docs/POSTGRES_MIGRATION.md) --
# audit_log moved with the write path; the sqlite3 form below no longer
# reaches live data.
psql -h /var/run/postgresql -U dispatch -d corporatetraveldc -c \
  "SELECT count(*) FROM audit_log;" -c \
  "SELECT action, count(*) FROM audit_log GROUP BY action ORDER BY 2 DESC;"
```

~~At that moment `audit_log` held **1,809 rows** (earliest 2026-08-16 17:46 UTC, newest 2026-08-23 17:46 UTC), dominated by rows written through the `require_admin` factory:~~

| ~~action~~ | ~~rows~~ |
|---|---|
| ~~`admin.approval_request.get`~~ | ~~1,688~~ |
| ~~`admin.alert.push`~~ | ~~37~~ |
| ~~`agent_sign_manifest`~~ | ~~23~~ |
| ~~`admin.approval_request.create`~~ | ~~23~~ |
| ~~`admin.approval_request.list`~~ | ~~13~~ |
| ~~`watchlist.flight.add`~~ | ~~8~~ |
| ~~`board_refresh`~~ | ~~8~~ |

~~plus `session_grant_created`/`session_grant_revoked` (2 each) and SR-1/SR-2 rows (`SR2_ROUTE` 2, `SR2_BLOCK`/`SR1_INTERCEPT`/`SR1_ALLOWED` 1 each). The "zero admin actions" gap documented above is closed and observable in the data. Note the distribution: `admin.approval_request.get` is the wrapper script's own status polling and accounts for ~93% of all rows, so raw row count is a poor proxy for "how much admin activity happened." `/healthz` reports the useful rolling figure instead (`audit_count_24h`, 1,401 at this same reading).~~

~~**Correction (2026-08-19) — this is narrower than "no audit trail exists."** An earlier pass through this file characterized admin-action audit logging as largely fabricated; that overstated it. A separate, real, always-on mechanism does capture every admin call: `Containerfile.web`'s `uvicorn` invocation runs with `--proxy-headers --forwarded-allow-ips=* --log-level info` and no `--no-access-log`, so uvicorn's default access log fires for every request and lands in journald via `journalctl --user -u corporatetraveldc-web`. Verified live 2026-08-19: every `GET`/`POST`/etc. is logged as `INFO: <client-ip>:<port> - "<METHOD> <path> HTTP/1.1" <status>`, going back at least to 2026-08-15 (4+ days, current journal disk usage 3.9G, no `MaxRetentionSec`/`SystemMaxUse` configured in `/etc/systemd/journald.conf` — it doesn't exist on this box — so retention is whatever fits the default size-based journald budget, not a date guarantee). This means an `/admin/push-alert` or `/admin/vip` call **is** logged somewhere, with source IP, method, path, and status.~~

~~That said, the gap below is real and this correction does not close it:~~

- ~~The access log has no `db.audit()`-style **actor identity** — no token/tier/token_prefix, only the connecting IP (which `--proxy-headers` resolves from `X-Forwarded-For` when present, so it can be the real client behind nginx/Tailscale, but there's no code guarantee of that per-route).~~
- ~~It has no **request payload** — you can see *that* `POST /admin/bandwidth-priority` happened, not *what* priority value or feed was set.~~
- ~~It's an undifferentiated **access log**, not a queryable, admin-scoped audit trail — `/admin/audit` (`main.py:2049`) reads `audit_log`, not journald, so today's admin UI genuinely shows zero admin history even though the raw fact of the call is sitting in journald.~~
- ~~journald retention is disk-budget-based, not date-based; `audit_log` is time-bounded at 90 days by the daily prune skill.~~

~~**RESOLVED (2026-08-19).** The gap above is closed in code. `require_admin` (`src/auth/auth.py`) is now a dependency factory — every call site passes its own hand-picked action name (`Depends(require_admin("admin.vip.add"))`, etc.) instead of the old bare `Depends(require_admin)` — so authorization and audit logging share one code path and cannot drift apart. It resolves the caller's `token_prefix` from the same bearer token already used for the tier check, captures the request body as `detail` for POST/PUT/PATCH/DELETE (falls back to query params when there's no body), and writes the row via the existing `db.audit()` before the route handler runs.~~

~~**Coverage: 32 endpoints, not 23.** The original finding scoped to `src/web/main.py` alone; `src/web/routes/watchlist.py` (8 endpoints) and `src/web/routes/remember.py` (1 endpoint) also depend on `require_admin` and were undercounted the same way the earlier "23" figure was — all 32 now write audit rows. Full action-name map:~~

| ~~Route file~~ | ~~Action names~~ |
|---|---|
| ~~`main.py` (23)~~ | ~~`osint.scope.{create,update,delete}`, `admin.healthz`, `admin.feeds.list`, `admin.audit.list`, `admin.tokens.list`, `admin.version`, `admin.triggers.list`, `admin.feed.refresh`, `admin.cps.force_recompute`, `admin.opsplan.force_snapshot`, `admin.osint.force_scrape`, `admin.alert.push`, `admin.vip.{list,add,remove}`, `admin.bandwidth_priority.{set,clear}`, `admin.approval_request.{create,get,list}`, `admin.watchdog.status`~~ |
| ~~`routes/watchlist.py` (8)~~ | ~~`watchlist.flight.add`, `watchlist.train.add`, `watchlist.vessel.add`, `watchlist.batch_remove`, `watchlist.entry.remove`, `watchlist.flight.add_batch`, `watchlist.train.add_batch`, `watchlist.permanent.add_batch`~~ |
| ~~`routes/remember.py` (1)~~ | ~~`vault.remember`~~ |

~~**One deliberate exception:** `GET /admin/approval-requests/{request_id}/resolve` carries no auth dependency by design (UUID4 request id as a single-use magic link; Cloudflare strips Authorization in transit). A dedicated Cloudflare Access bypass application, `dispatch-approval-resolve-bypass` (2026-08-20, same shape as `dispatch-robots-bypass` / `dispatch-board-public-bypass`), scopes exactly that path. Re-confirmed live 2026-08-23 against the Cloudflare Access API: the app exists with domain `dispatch.example.com/admin/approval-requests/*/resolve`. It is the only `/admin/*` route not gated by `require_admin`, and it writes no audit row of its own — the related `admin.approval_request.{create,get,list}` routes are audited.~~

~~**Retention.** `db.prune_audit_log(days=90)` (`src/common/db.py`) plus a new daily poller skill, `poller/skills/audit_log_prune.py`, registered in `SKILL_SCHEDULE` (`src/poller/main.py`) at the same 86400s interval as the existing `flight-cleanup` skill it's modeled on. This makes the doc's long-standing "90-day retention" claim true instead of removing it.~~

~~**Verified live 2026-08-19** — exercised the real `web.main:app` object in-process (FastAPI `TestClient`, real DB, a throwaway admin token) against `GET /admin/healthz`, `POST /admin/vip`, `DELETE /admin/vip/{entry}`: all three wrote correct rows (right action name, right `token_prefix`, `detail` captured exactly for the POST body, `None` for the bodyless GET/DELETE). Full test suite run clean afterward (171 passed, 16 pre-existing failures confirmed unrelated via `git stash` bisection — none in auth/web/db — 1 pre-existing failure in unrelated marine-parsing tests deselected). Current state (2026-08-23 17:3x, re-run this pass): 199 tests, 198 pass, 1 known pre-existing failure (`test_smes_parser_basic`) — the 16 pre-existing failures noted here were all root-caused and fixed 2026-08-20. Test token revoked and test-only audit rows deleted after verification; no residue left in `audit_log`.~~

~~**Deployed** — the `require_admin` factory is live in the running `corporatetraveldc-web` image; audit rows with the new action names are written continuously (`/healthz` → `audit_count_24h`).~~

**~~The mechanism itself (accurate as written)~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 3. Audit Logging (Real, As-Built) › The mechanism itself (accurate as written)

~~CTDI maintains a genuine append-only audit log (`audit_log` table, local database — SQLite or local Postgres per `DISPATCH_DB_BACKEND`, see `docs/POSTGRES_MIGRATION.md` — never leaves the device). Its schema (SQLite DDL as written below):~~

*Superseded block:*
```text superseded
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

~~This is the platform's actual, current audit mechanism. The data lives locally in this table first and always; it can be queried directly (`/admin/audit`, token-gated) whether or not anything below is ever turned on. Read it together with "What is not audited" above: the mechanism is sound and the storage guarantees hold — the defect is call-site coverage, not design.~~

**~~Token expiry — implemented, enforced, and never used~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 3. Audit Logging (Real, As-Built) › Token expiry — implemented, enforced, and never used

~~Recorded here because it is an access-control hygiene gap adjacent to the audit gap above, and both were found the same way (asking what the data actually shows rather than what the doc asserts). Verified 2026-08-19 against the live `auth_tokens` table:~~

* ~~**All 19 rows have `expires_at IS NULL`** (re-verified 2026-08-23 12:5x EDT; up from 15 on 2026-08-19 — re-query with (Postgres since the 2026-09-20 cutover, docs/POSTGRES_MIGRATION.md; the sqlite3 form of this command no longer reaches live data) `psql -h /var/run/postgresql -U dispatch -d corporatetraveldc -c "SELECT token_prefix,tier,device_label,expires_at,revoked_at FROM auth_tokens;"` rather than trusting either figure). Not one token has ever been issued with an expiry, including the four minted since 2026-08-19.~~
* ~~**Six are currently active** (`revoked_at IS NULL`), spanning admin, cert, and shares tiers: `ctdc_admin_` (admin), `ctdc_runner_` (cert), `ctdc_corporatetraveler_` (admin, `mobile-browser`), `ctdc_demo_recorder_` (cert), `ctdc_cowork_` (shares, `cowork-research-v2`), `ctdc__ontime-test_` (admin). Four of the six are admin-tier.~~
* ~~Among the active six is `ctdc_admin_` / tier `admin` / `mcpo-corporatetraveldc-dispatch-mcp` — a **never-expiring admin-tier token belonging to the MCP bridge that was retired on 2026-08-18**, still active as of 2026-08-23 despite that retirement. The bridge is gone; the credential that could drive it is not.~~

~~The expiry mechanism is not missing — it is fully built and enforced. `db.lookup_token()` (`src/common/db.py`) selects with `AND (expires_at IS NULL OR expires_at > unixepoch())`, so an expired token simply does not resolve; `resolve_tier()` and `require_admin()` (`src/auth/auth.py`) both go through that one lookup on the request path, so there is no route that skips the check. (An earlier revision of this paragraph cited `db.py:844` and `auth.py:153` — both line numbers have since drifted onto unrelated code; grep the symbols instead.) The `auth_tokens` schema has carried the column from the start (`expires_at REAL, -- NULL = no expiry`). It is simply never populated at mint time, so a working control sits permanently disengaged. Combined with the §3 finding above, the practical position today is: long-lived admin credentials, and no record of what they did.~~

~~**NEEDS OPERATOR DECISION:** revoke the retired mcpo admin token and decide a default token TTL — expiry is implemented and enforced but no token has ever been issued with one.~~

~~As of 2026-08-03, CTDI also ships a real, disabled-by-default outbound egress hook for operators whose own recordkeeping platform (Global Relay, Smarsh, an internal SIEM, or anything else) needs a copy of this audit trail pushed out rather than pulled. It is off unless an operator explicitly configures it:~~

*Superseded block:*
```text superseded
COMPLIANCE_HOOK_ENABLED=false        # default -- module no-ops immediately if false
COMPLIANCE_TARGET_URL=               # operator's own endpoint; unset = no-op even if enabled
COMPLIANCE_TARGET_AUTH_HEADER=       # optional bearer/auth header for that endpoint
COMPLIANCE_RETRY_LIMIT=5             # attempts before a row is marked failed_permanent
```

~~`COMPLIANCE_TARGET_URL` and `COMPLIANCE_TARGET_AUTH_HEADER` are operator secrets (they can reveal or grant access to an internal endpoint) and belong in `dispatch-secrets.env`, never in the tracked non-secret config.~~

~~Mechanically: a 5-minute systemd timer (`corporatetraveldc-compliance-egress-push.timer`) runs `common.compliance_egress.push_pending_audit_events()` inside the poller container. It checks `COMPLIANCE_HOOK_ENABLED` and `COMPLIANCE_TARGET_URL` first and returns immediately (no DB read, no network call) if either is unset. When both are set, it batches unshipped `audit_log` rows (tracked via `egress_status`/`egress_attempts`/`egress_last_error` columns on that same table -- no second table, no duplicated log) and POSTs a fixed envelope: `record_id, event_time_utc, source_node, action, tier, token_prefix, remote_addr, detail`. That is the entire payload shape -- there is no PNR, reservation, or trip-level field in it, because none of that lives in `audit_log` to begin with. Rows that fail are retried up to `COMPLIANCE_RETRY_LIMIT` times, then marked `failed_permanent` so a persistently unreachable target degrades to "stop trying" rather than retrying forever.~~

**~~4. Alignment with ISO/IEC 42001 (AI Management System Standard)~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 4. Alignment with ISO/IEC 42001 (AI Management System Standard)

~~**Status, stated plainly: CTDI is not ISO/IEC 42001 certified.** Certification requires an accredited third-party certification body to run a two-stage audit (documentation review, then an operational-effectiveness evaluation with staff interviews and evidence collection) followed by annual surveillance audits. No such audit has been performed on this platform or on [operator LLC], LLC. Any claim to the contrary would be inaccurate and is not made anywhere in this document.~~

~~What *is* true, and is the actual basis for this section: CTDI's core design principles were built around several of the same control areas ISO/IEC 42001's Annex A asks an AI management system to address, independent of and prior to this document being written. That is a genuine architectural fact, not a marketing gloss -- the table below maps specific, existing platform behavior to the relevant control area.~~

| ~~ISO/IEC 42001 Annex A control area~~ | ~~What CTDI actually does~~ |
| :--- | :--- |
| ~~A.7 -- Data for AI systems~~ | ~~No operator query or model input/output is sent to any external party. Local-only inference (host llama.cpp since 2026-08-27; previously Ollama) is a hard default (`DESIGN-PRINCIPLES.md` §2); CUI-classified radio data is handled under an explicit, non-negotiable ruleset (never in code, configs, or exports).~~ |
| ~~A.9 -- Responsible use of the AI system~~ | ~~Deterministic fallback is required when local inference is unavailable -- the system does not silently fail over to a cloud provider. Any cloud LLM integration must be an explicit, operator-controlled opt-in, never a default.~~ |
| ~~A.10 -- Third-party / supplier relationships~~ | ~~Local-only inference removes the AI supply-chain risk (model-vendor data handling, training-data exposure, vendor outage dependency) that ISO/IEC 42001 is partly designed to help organizations manage.~~ |
| ~~A.4 -- Resources for AI systems~~ | ~~Real, evidenced resource guardrails (network, memory, CPU, thermal) exist for every AI-adjacent process, each backed by dated incident data and telemetry, not a theoretical worst case (`GUARDRAILS_JUSTIFICATION.md`).~~ |

~~What is genuinely missing, and would need to be built before a real certification audit could be attempted: a formal, top-management-owned AI policy document (as opposed to the informal, code-enforced rules in `DESIGN-PRINCIPLES.md`), defined AI-governance roles and responsibilities (this is presently a single-operator business), a documented AI risk/impact-assessment methodology, and a management-review cadence. None of these are difficult to build on top of what already exists -- the underlying practices they'd formalize are already there -- but they are real gaps, not paperwork technicalities, and no pitch material should describe them as already closed.~~

~~**Net position for a client or partner conversation:** CTDI is not certified, and does not claim to be. It was built using ISO/IEC 42001's control areas as design guardrails from early in its development, which means an operator adopting it starts substantively closer to a certifiable posture than a platform built without that framework in mind -- "compliance-adjacent from day one" in the sense that the hard technical work (data handling, vendor isolation, resource governance) is already done, not in the sense that a certificate exists. A full standalone treatment of this, including the honest gap list, lives in `ISO_42001_ALIGNMENT.md`.~~

**~~5. Host Integration & Hardening Guidelines~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 5. Host Integration & Hardening Guidelines

* ~~**SELinux Support:** The system includes ready-to-use Type Enforcement modules (`.te`) that authorize the background systemd service layers to function within targeted enforcement contexts.~~
* ~~**Process Priority:** The platform isolates worker processes inside rootless Podman containers, keeping system resources strictly ringfenced away from host operations.~~
* ~~**Thermal Resource Caps:** Every model definition pins the inference thread count below the physical core count (`PARAMETER num_thread 2` on a 4-core Pi 5), leaving cores unclaimed by Ollama for core components such as Pi-hole or the local network router. See the correction immediately below for what this does and does not guarantee.~~

~~**Correction to "Thermal Resource Caps" (2026-08-19).** Two things in the previous wording were wrong, one factual and one about strength of guarantee:~~

* ~~**The value was wrong.** This bullet read `PARAMETER num_thread 3`. All **21** Modelfiles in the repo set **`PARAMETER num_thread 2`** — verified by `grep -h num_thread corporatetraveldc.*`, which returns 21 identical `PARAMETER num_thread 2` lines and 21 copies of an accompanying comment noting that `num_thread=2` is load-bearing (see the 2026-08-14 smoke-test rationale carried in the Modelfiles themselves). Nothing in the tree sets 3.~~
* ~~**"Dedicated computing headroom" overstates it.** `num_thread` is a *self-restraint* setting inside Ollama, not an enforced reservation. The kernel does not hold cores back for Pi-hole; Ollama simply declines to spawn more than two inference threads. Any other process — including Ollama's own non-inference work and all 30-plus containers — competes freely for the remainder.~~

~~**Host-level CPU/memory governance was installed 2026-08-19** (`ollama.service.d/20-resource-limits.conf`: `CPUWeight=500`, `CPUQuota=300%`, `MemoryLow/High/Max=4850M/6050M/7250M`, `MemorySwapMax=0`, `OLLAMA_KEEP_ALIVE=10m`, `LLAMA_ARG_CACHE_RAM=0`). **Superseded 2026-08-27 at the llama.cpp cutover:** `ollama.service` and that drop-in are gone; the enforced cgroup layer now lives inside each per-tier `corporatetraveldc-llama-*` user unit (e.g. llama-hot: `CPUWeight=9000`, `MemoryMax=4608M`), with the same hard-ceiling/no-swap-escape philosophy. The Modelfiles' `num_thread` parameters ceased to be live config at the same cutover — thread counts are `llama-server` command-line arguments in those unit files, and the Modelfiles survive only as manifest-verified canonical source text for `src/common/personas.py`.~~

**~~SELinux Grant Policy: Scoped Labels Over Domain-Wide Booleans~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 5. Host Integration & Hardening Guidelines › SELinux Grant Policy: Scoped Labels Over Domain-Wide Booleans

~~Every SELinux exception this platform requires is granted at the narrowest scope that resolves it -- a specific `.te` allow rule or a specific `semanage port` label -- rather than a domain-wide boolean such as `container_use_devices` or `httpd_can_network_connect`. A boolean grants a whole security domain (every container, or `nginx` itself) blanket reach to a whole permission class; a scoped label or rule grants exactly the one resource one process needs, and nothing else.~~

~~This keeps `semanage port -l` and `semodule -l` a complete, self-explaining audit trail: every entry maps to one named module or one documented backend, not an open-ended grant whose blast radius has to be inferred. Concretely:~~

* ~~`selinux/corporatetraveldc-sdr-usb.te` grants `container_t` access to the RTL-SDR USB devices only (`usb_device_t` chr_file) -- not the broader `container_use_devices` boolean, which would apply to every container on the host, present or future.~~
* ~~`selinux/label-nginx-backend-ports.sh` labels each nginx `proxy_pass` target individually as `http_port_t`, and `selinux/corporatetraveldc-nginx-proxy.te` grants `httpd_t` name_connect to that type specifically -- not the `httpd_can_network_connect` boolean, whose scope is every TCP port class in the base policy, not just the ones this repo defines. Each labeled port traces to exactly one vhost in `nginx/conf.d/`.~~
* ~~`selinux/corporatetraveldc-fail2ban-lockdown.te` grants `fail2ban_t` the specific search/read/write/ioctl/getattr/name_connect permissions `scripts/lockdown.sh`/`restore-network.sh` actually use -- discovered across three rounds (2026-07-10, then 2026-08-09) as fail2ban's actionban/actionunban exercised more of the scripts' real behavior: reach the quadlets under `/home/corporatetraveldc`, run `systemctl daemon-reload`/ `restart` (including stat-ing the `systemctl` binary itself), edit `ollama.service.d/10-binding.conf` in place via `sed -i`'s temp-file+rename sequence, and send the ntfy incident notification. No broader domain transition or unconfined exec for fail2ban -- just the grants the scripts actually exercise, added as each round of enforcing surfaced the next one.~~
* ~~`selinux/corporatetraveldc-fail2ban-cf-egress.te` grants `fail2ban_t` one thing -- name_connect on `pihole_port_t` -- so `scripts/cf-honeypot-ban.sh` (the honeypot's Cloudflare-edge IP Access Rule ban/unban action, see `docs/HONEYPOT_FAIL2BAN.md`) can reach `api.cloudflare.com` over HTTPS. Port 443 is labeled `pihole_port_t` on this box rather than the stock `http_port_t` (pre-existing Pi-hole customization, not introduced here), so any fail2ban action making an outbound HTTPS call needs this grant regardless of destination. `audit2allow`'s first pass over the same denials also proposed `self:process execmem` (for `grep -oP`'s PCRE JIT compiler) -- deliberately not granted; the script was rewritten to use `sed` instead, avoiding the permission rather than carrying it as a standing exception.~~

~~**Adding a new network-facing service:** add its `proxy_pass` target to `nginx/conf.d/`, add one line to the `PORTS` list in `selinux/label-nginx-backend-ports.sh`, and re-run `selinux/apply-selinux-policy.sh`. Commit both changes together so the port grant and the vhost that needs it are reviewed as one auditable unit.~~

**~~Container Network Isolation: Air-Gapped by Default~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 5. Host Integration & Hardening Guidelines › Container Network Isolation: Air-Gapped by Default

~~The same scoped-grant principle applies to container-to-host networking. Podman's rootless networking (`pasta`) is air-gapped from the host by default on this platform -- a container cannot reach anything bound to the host's own interfaces unless it explicitly opts in. The *principle* is intact and the opt-in remains explicit and per-container; the **stack has roughly tripled since the counts below were written**, and the majority no longer sits on the no-host-access side.~~

~~**Count corrected 2026-08-19.** This paragraph previously read "As of 2026-07-10, 15 of 18 containers in this stack need no host access at all and have none." Live state:~~

| ~~Measure~~ | ~~Value~~ |
|---|---|
| ~~Quadlet files in `~/.config/containers/systemd/`~~ | ~~68 (of which 64 are `.container` units) — as of 2026-08-23 17:3x~~ |
| ~~Containers actually running (`podman ps`)~~ | ~~36 as of 2026-08-23 13:5x EDT — read 33 an hour earlier. Fluctuates in both directions: the thermal guard sheds and restores containers on a 2-minute cadence (see CLAUDE.md's "Ingest load-shedding"), and the timer-triggered skill containers are short-lived oneshots that exist only while running, so overlapping timers push the count above the long-running baseline~~ |
| ~~`.container` units carrying `Network=pasta:--map-gw`~~ | ~~**26**~~ |

~~So the honest statement is: **of 64 container units, 26 opt in via `Network=pasta:--map-gw` and 38 do not** — a little *over* 40% (26/64 = 40.6%). Note the direction of that characterization flipped on 2026-08-23 when the count moved 25/63 → 26/64; an earlier revision said "a little under 40%", which was correct at 25/63 (39.7%) and is not now. Reproduce with:~~

*Superseded block:*
```text superseded
ls ~/.config/containers/systemd/*.container | wc -l                      # 63
grep -l 'Network=pasta:--map-gw' ~/.config/containers/systemd/*.container | wc -l   # 25
```

~~Two things about that 25 are worth stating plainly rather than leaving to be inferred:~~

* ~~**It includes the primary service containers**, not just peripheral ones: `corporatetraveldc-web`, `corporatetraveldc-runner`, and `corporatetraveldc-pusher` all carry the flag. The original phrasing implied the exceptions were a small tail; they are not, and they include the public-facing web API.~~
* ~~**The bulk of the rest are the timer-triggered skill containers** (the `*-daily-watch`, `*-weekly-watch`, digest, memo, and second-brain units), which reach `host.containers.internal` for ntfy and Ollama. That is a legitimate and individually-declared need, but "air-gapped by default" now describes the default of the mechanism rather than the posture of the deployed fleet.~~

~~Neither point weakens the scoped-grant argument that follows — each of the 25 opted in on its own quadlet, which is exactly the per-container scoping this section advocates, and none of them took `Network=host`. The correction is to the count and to the impression the old sentence created, not to the design.~~

~~The LLM inference layer stays true to the "None (Air-gapped compatible)" row in the Data Sovereignty & Isolation Matrix above -- the `llama-server` tier units listen on this host's own Tailscale IP only (100.x.x.x:8093/8094/8095), never on the public-WiFi-facing interface or any container-default-reachable address (same binding discipline the retired Ollama daemon followed).~~

~~**Three opt-in mechanisms, chosen by network mode and by what the target service itself binds to, all scoped to the one container or the one capability that needs them:**~~

* ~~**Pasta-mode containers reaching a `0.0.0.0`-bound host service** (Podman's default, no `Network=` line or an explicit `Network=pasta`) opt in individually with `Network=pasta:--map-gw` on that container's own quadlet. This restores the `host.containers.internal` alias for that container only. See `corporatetraveldc-pusher.container` (reaching `ntfy`) for the pattern -- it carries a comment explaining what it needs to reach and why.~~
* ~~**Pasta-mode containers reaching a service bound to a specific non-loopback host IP** (e.g. Ollama on its Tailscale address) address that IP directly instead -- `Network=pasta:--map-gw` doesn't apply here and isn't needed: the kernel refuses externally-arriving traffic destined for `127.0.0.0/8` regardless of pasta's routing (anti-spoofing), so `host.containers.internal` can never reach a strictly loopback-bound service under any pasta flag, while a real routable IP is reachable via normal outbound NAT with no opt-in at all. See `openwebui.container` (reaching Ollama) and `corporatetraveldc-runner.container` (reaching `dispatch`/`ultrafeeder`) for the pattern.~~
* ~~**Bridge-mode containers** (`Network=<name>.network`, e.g. `corporatetraveldc-acarshub` on `acars-net.network`) have no per-container equivalent for the `host.containers.internal` alias: rootless Podman's bridge networking is itself tunneled through one shared `rootless-netns` pasta process, so that opt-in is host-wide -- `pasta_options = ["--map-gw"]` in `.config/containers/containers.conf`. This is the one case where Podman's current architecture doesn't allow per-container scoping; it's documented there as affecting every bridge-networked container on the host, not just the one that needed it. (This mechanism only works for host services bound beyond loopback, same constraint as above.)~~

~~**`Network=host` requires a comment justifying it, the same way `host.containers.internal` usage does.** It grants a container the host's full network stack with no isolation boundary at all -- broader than either opt-in mechanism above. `corporatetraveldc-runner.container` ran with `Network=host` undocumented until 2026-07-10; it was removed in favor of the same IP-scoped `PublishPort=` pattern `corporatetraveldc-web.container` already used, since nothing about that service actually required full host networking.~~

~~**Adding host-reach to a new container:** default to no `Network=` line at all. If it needs to reach a host-bound service, use `Network=pasta:--map-gw` and comment why. Reach for the bridge-mode host-wide setting or `Network=host` only if the per-container mechanism genuinely doesn't apply, and say so in a comment either way.~~

**~~External API Action Safety Pattern: Verify Success, Verify Identity~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 5. Host Integration & Hardening Guidelines › External API Action Safety Pattern: Verify Success, Verify Identity

~~Standing rule for any script that calls an external API to enforce a consequential, state-mutating action (bans, deletes, anything a monitoring or security control fires automatically and unattended) -- established 2026-08-09 fixing `scripts/cf-honeypot-ban.sh` twice in one day, each round catching a failure mode the previous round's fix didn't cover:~~

1. ~~**Check the response body, not just the HTTP status code.** Some APIs (Cloudflare's included) return `200 OK` with `{"success": false, ...}` for validation failures -- an HTTP-status-only check treats that as success. Every call must check both: 2xx status AND the body's own success/status field.~~
2. ~~**A lookup-then-mutate flow must verify the looked-up object's identity before acting on it, every time** -- never assume a filtered query actually filtered. Concretely: `cf-honeypot-ban.sh`'s unban path does `GET .../rules?configuration.value=<ip>` then `DELETE` on the first result; if that GET's filter silently fails to apply (see #3) and returns the full unfiltered list instead, blindly deleting `.result[0]` deletes an unrelated rule -- a different IP's legitimate ban lifted by accident, not the one being unbanned. Confirm `result[0]`'s own identity field matches the intended target before the mutating call, or abort loudly.~~
3. ~~**`curl -X GET --data-urlencode ...` without `-G` sends the params as a request body, not a URL query string** -- many APIs (Cloudflare included) silently ignore a GET request's body and just return everything unfiltered. This is exactly the bug that made #2 a real risk rather than a theoretical one. Always pass `-G` when using `--data`/`--data-urlencode` with `-X GET`.~~
4. ~~**Plain `curl -s` alone is not sufficient error detection** (see the `docs/HONEYPOT_FAIL2BAN.md` postmortem) -- it only signals non-zero on connection-level failure, never on an HTTP error response. Combine with #1's body check, always.~~

~~None of these are Cloudflare-specific despite the pattern being discovered there -- apply all four to any future external-API action a jail, timer, or watchdog fires unattended.~~

**~~6. Request Trust Model: Network-Layer ACL vs. Application-Layer Tier Checks~~** *(former heading)*


### CTDI Dispatch: On-Premises Architecture & Compliance Datasheet › 6. Request Trust Model: Network-Layer ACL vs. Application-Layer Tier Checks

~~Two independent, non-interchangeable enforcement layers, each covering a traffic path the other structurally cannot see. This section exists because the two were briefly conflated (2026-08-05 investigation, below) -- the short version is that neither is redundant with the other, and assuming otherwise is what left a real hole open.~~

~~**Network layer -- Tailscale ACL, tag-scoped grants (`tailscale/policy.hujson`).** Governs one thing: which devices can even open a connection to a port bound on the tailnet interface (100.x.x.x) at all. Default posture is explicit-allow -- an untagged or improperly-tagged device gets nothing, enforced by Tailscale's control plane before a packet reaches this box. This is what actually protects the admin runner today: `runner/main.py` (port 8001) is reachable only via `tailscale-dispatch-runner.conf`, a Tailscale-cert HTTPS vhost bound to the tailnet IP. A device that isn't tagged `tag:corporatetraveldc-server` (owner's own devices, via `autogroup:self`) cannot reach that port to begin with -- there's no header to forge, because there's no connection to forge it over.~~

~~**Application layer -- the `X-CTDI-Public` marker (`auth.py::resolve_tier`).** Governs a completely different question: for a request that already reached this app's shared backend process, did it arrive through the public Cloudflare Tunnel or not. This exists because `dispatch.example.com` (port 8000, the web API) is deliberately public -- Cloudflare Tunnel traffic terminates at a local nginx listener and is proxied to the exact same FastAPI process a tailnet request would reach. **Tailscale ACLs have zero visibility into this path** -- tunnel traffic never touches the tailnet interface, so no tag, grant, or ACL rule ever evaluates it. The only thing standing between an anonymous internet request and an elevated tier is whatever the app itself decides to trust, which is why this specific check has to be correct on its own, independent of how good the tailnet ACL is.~~

~~**2026-08-05 finding: the previous app-layer check was spoofable, and the ACL work does not cover the gap.** `auth.py` previously trusted `Tailscale-User-Login` and an `X-Forwarded-For` prefix of `"100."` as proof of tailnet origin. The live public vhost forwarded `X-Forwarded-For` via nginx's `$proxy_add_x_forwarded_for`, which *appends* the connecting peer's address rather than replacing the header -- so a plain internet client sending `X-Forwarded-For: 100.64.0.1` reached the app as `"100.64.0.1, 127.0.0.1"`, which still satisfied a naive `.startswith("100.")` check. Verified exploitable against the live `dispatch.example.com` endpoint (which gates 7 Tier-1 API routes) with no token at all. The tailnet rebuild and ACL/tag hardening done the same night do not touch this: that traffic never reached the tailnet in the first place, so no amount of ACL correctness closes an application-layer header-trust bug on a path the ACL never sees.~~

~~**Fix**: nginx now sets `X-CTDI-Public: 1` via a literal `proxy_set_header` on every location block in `dispatch.example.com.conf` that proxies to port 8000. `resolve_tier()` forces Tier 0 whenever that marker is present, before any token lookup runs -- so even a *valid* bearer token presented through the tunnel cannot elevate. This is safe specifically because `proxy_set_header` **replaces** the header for the proxied request regardless of what the client sent (unlike `$proxy_add_x_forwarded_for`'s append semantics above) -- empirically verified in an isolated test harness: a client sending `X-CTDI-Public: 0` still reached a test backend as `"1"`. The corollary risk this creates -- a location block that forgets the directive silently lets the client's own value straight through -- was verified the same way and is why every location block proxying to a public-facing port must carry it explicitly; nginx location blocks that define any `proxy_set_header` of their own do not inherit server-level ones.~~

~~Verified against the live (rebuilt) `corporatetraveldc-web` container after the fix: a spoofed `X-Forwarded-For: 100.x.x.x` with no token is rejected (403); a request carrying `X-CTDI-Public: 1` **and a real, valid cert-tier bearer token** is still rejected (403) -- the marker overrides a genuinely valid credential, which is the actual property this fix needed to have; a request with no marker and a valid token still succeeds (200), confirming genuine tailnet/direct access is unaffected.~~

~~**`runner/main.py`'s CF-Connecting-IP check (`_is_trusted`) -- documented past work, effectively superseded by the ACL for its one remaining live path.** This function predates the marker approach (2026-07-21 bugfix for the same class of problem: `ops.example.com`, then a public vhost in front of the runner, was intermittently trusting a Cloudflare-tunnel loopback hop as if it were a LAN origin). `ops.example.com` was retired as a public endpoint 2026-08-02/03 -- confirmed no matching nginx vhost exists for it today, so its CF-Connecting-IP branch is currently unreachable in practice. The runner's only remaining live front door is the tailnet-only vhost above, where reachability is already gated by tag-scoped ACL grants before the request arrives -- making this specific check close to true network/app-layer redundancy for the path that's actually live. Left as-is (not rewritten to the marker model) since it isn't exploitable today and the stale `ops.example.com` Cloudflare ingress rule that used to front it has been removed outright (not just left dead) -- see `cloudflared/config.yml`. If the runner is ever re-exposed publicly, it needs the same `X-CTDI-Public` treatment `auth.py` now has, not a revival of IP-header trust.~~
