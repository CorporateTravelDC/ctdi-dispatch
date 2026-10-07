# CHANGES -- security & governance docs

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.
14 docs rewritten. No sudo was run; root-only files (`/etc/sudoers.d/*`,
`/var/log/fail2ban.log`, `/var/log/nginx/honeypot.log`, audit log) and the
Postgres tables were not read. No secret values were read or written.

## SECURITY.md

| claim as written | evidence | fix |
|---|---|---|
| `CC1509BD…0B37` "is the key that actually signs MANIFEST.sha256.asc today" | `gpg --verify MANIFEST.sha256.asc MANIFEST.sha256` (isolated keyring) -> "using EDDSA key 419A864C…3159", primary `3B29752D…1631`, made 2026-10-06 17:42Z | HEAD manifest is signed by the operator key's signing subkey; CC15… is the `--agent` delegate, also pinned |
| `ABD3976F…EFB2` "previous key, still valid for verification" (no location given) | `git ls-files` -> `ABD3976FCC006E0F3FE559177286B3118BA4EFB2.gpg`, `CorporateTravelDC.gpg` at repo root carry it | location stated; noted that no control pins it |
| Long-running core containers "do not run the check" (verified 2026-08-19) | `Containerfile.web` CMD; `podman inspect` entrypoint `[]` | kept; added the stack-refresh in-image gate on running containers (`scripts/stack-refresh.sh` ~L287) |
| Secrets live in `dispatch-secrets.env` (implied: what containers read) | `grep -hE '^EnvironmentFile=' ~/.config/containers/systemd/*.container` -> 0 quadlets read it; 9 `svc/<svc>.env` files | per-service scoped env described |
| (missing) root-run installed copies, signing pin, approvals, allowed_actions, audit chain | `/usr/local/libexec/ctdc/`, `/etc/corporatetraveldc/signing-pin`, `src/common/governance.py`, migrations 0062/0070 | added |
| (missing) trust boundary | operator uid owns checkout + passphrase-less board key, in `wheel` (`id`) | stated plainly |

## docs/COMPLIANCE_SECURITY.md

| claim as written | evidence | fix |
|---|---|---|
| Audit log "90-day retention via `poller/skills/audit_log_prune.py` (`db.prune_audit_log(days=90)`)" (§2 table, §3) | `src/poller/skills/audit_log_prune.py` docstring "RETIRED 2026-09-22. Does not prune."; `db.prune_audit_log()` raises (db.py:1307); `src/poller/main.py:142` schedules `audit-log-archive` | replaced with the signed archive + stub design (0062 + `audit_log_archive.py`) |
| `audit_log` "append-only"; no tamper evidence mentioned | `src/common/pg_schema/0062_audit_tamper_evidence.sql` (hash-chain trigger, checkpoints, stubs; explicitly no UPDATE/DELETE block, no role split) | documented incl. limits; no chain verifier exists (`grep -rln row_hash` -> only the migration, the archive skill, migration scripts) |
| require_admin coverage "32 endpoints" (main 23, watchlist 8, remember 1) | `grep -c 'require_admin("'` -> main 23, watchlist 8, sectors 5, remember 1 = 37 | 37; sectors actions added |
| Only Tier-2 read and SR/board rows audited; denied attempts not mentioned | `src/auth/auth.py` require_admin: denied + out-of-scope rows (2026-08-25, 0070) | added |
| (missing) governance surfaces' audit | no `audit()` calls in governance.py / agent_gateway.py / es_invites.py / console.py; evidence in `approval_requests.resolved_by/resolution_sig` (0069), `es_invite_events` (0072), etc. | new table "what does not write audit_log" |
| uvicorn `--forwarded-allow-ips=*` | `Containerfile.web` CMD -> `--forwarded-allow-ips=127.0.0.1` | corrected |
| Token expiry "never populated at mint time" | `src/ctdc_token/cli.py:209` `--expires` default 365 | corrected; live row state [UNVERIFIED] |
| 33 verified-exec quadlets | `grep -l verified-exec ~/.config/containers/systemd/*.container \| wc -l` -> 39 | 39 |
| LLM: "per-tier `llama-server` units ... 100.x.x.x:8093/8094/8095", llama-hot `CPUWeight=9000`, `MemoryMax=4608M` | `systemctl --user list-units 'corporatetraveldc-llama*'` -> one `corporatetraveldc-llama.service`; `show` -> CPUQuota 200%, MemoryMax 8448M; `ss -ltn` -> only :8093 | single server documented |
| Quadlet counts 68 files / 64 `.container`, 26 with `--map-gw` | `ls *.container \| wc -l` -> 74; map-gw -> 29; named network -> 6 | updated |
| Demo three-way disagreement + crash-loop narrative (marked SUPERSEDED) | runner-demo `active/running`, `NRestarts=0`, `DEMO_MODE=true` in quadlet, `:8005/healthz` ok | history cut; current state stated |
| demo-source refresh timer disabled, never fired, file frozen 2026-08-14 | `is-enabled` -> disabled; `LastTriggerUSec=` empty; mtime 2026-08-14 14:21Z | kept (still true) |
| Thermal caps paragraph: `PARAMETER num_thread 2` limits Ollama; `ollama.service.d` drop-in | 21 Modelfiles still say it; Ollama retired | stated as vestigial |
| `selinux/...fail2ban-lockdown.te` edits `ollama.service.d/10-binding.conf` | `scripts/lockdown.sh:13` "Ollama bind-revert step ... removed" (2026-08-30) | dropped |
| CF Access app list "re-confirmed 2026-08-23" (8 apps) | not re-listable; `curl` on 2026-10-06: dispatch host 302 to Access for `/api/v1/tfr`, `/console`, `POST /api/v1/approvals/*/resolve`; 200 `/robots.txt`, `/api/v1/board/health`; 404 (app) on `/admin/approval-requests/*/resolve` | replaced with observed behaviour, list [UNVERIFIED] |
| §6 "`ops.` ingress removed"; runner `_is_trusted` | `cloudflared/config.yml` has no `ops.`; still lists `ollama.example.com` | finding (below) |
| (missing) secrets SELinux caveat | `ls -Z /etc/corporatetraveldc/cf-honeypot.token` -> `container_file_t` | added |
| "Housekeeping note: comments cite a 'Signed Manifest Integrity' section" | still cited by `verify-manifest.sh`, `verified-exec.sh` headers | not re-added as a section; cross-reference drift noted below |

## docs/GUARDRAILS_JUSTIFICATION.md

| claim as written | evidence | fix |
|---|---|---|
| Current network regime 19-64 GiB/day since 08-19 | `vnstat -m`: Sep 4.97 TiB (highest month); `vnstat -d` 2026-09-30..10-05: 239-269 GiB/day | corrected; the "de-facto guardrail" no longer holds |
| CPUQuota tbfm 80%, tfms 90%, stdds 120% | quadlets: tbfm 100%, tfms 110%, stdds 140% | corrected |
| "Every container" has Memory=/CPUQuota= | Memory= missing on execstandard-verifier; CPUQuota= missing on 36 quadlets | qualified |
| Ollama drop-in `CPUQuota=300%`/`CPUWeight=500`; per-tier llama units; llama-restart 03:00 ET (07:00Z) | single `corporatetraveldc-llama.service`: CPUQuota 200%, CPUWeight 10000, MemoryHigh/Max 7168M/8448M, swap 0; restart timer description "19:45 ET" (23:45Z) | replaced |
| Restore requires load1 < 15 | `scripts/thermal-ingest-guard.py:631-632` resume = 40 x 0.5 = 20 (2026-10-03) | corrected |
| LOCKDOWN log sample "restored ... ollama.service" | guard no longer touches LLM services (script docstring 2026-08-27) | history trimmed |
| §5 sudo grant for `ollama.service restart` motivates gate | `ollama.service` gone | rewritten to current signed-approval model |
| (missing) watchdog tuning diagnostic | `zz-ctdc-watchdog.conf`, stall-monitor active, watchdog-tune timer next 2026-10-12 00:00Z | added |

## docs/SUDO_JUSTIFICATION_PROPOSAL.md

| claim as written | evidence | fix |
|---|---|---|
| Framed as proposal + Allow/Deny tap | `sudo-approval-gate.sh:121-141` deny-only push; `db.py:4569` ApprovalNeedsSignature | rewritten as implemented state |
| `/etc/sudoers.d/` is `drwx------` | `ls -ld` -> `drwxr-x--- root root` | corrected (still unreadable) |
| Effective grants (2026-08-23 `sudo -n -l`) | not re-run (no sudo) | kept as last-known, [UNVERIFIED]; ollama/governor entries flagged dead |
| `scripts/ollama-wedged-detector.sh` first DR caller | file absent at HEAD | removed |
| (missing) agent accounts have no sudo; operator->agent sudoers | `id -nG` per account; `plan.sh:268-270` writes `50-<account>` | added |
| (missing) signed-approval server checks | `governance.py::resolve_signed` | added (human kind, not requester, not a board key, single resolution, 20/min) |
| Skill audit section (four `sudo` spots, stale skill) | marked RESOLVED/HISTORICAL in the old doc; repo now has `skills/corporatetraveldc-dispatch-ops/SKILL.md` | cut |
| "Repo `skills/corporatetraveldc-dispatch-ops/` has no SKILL.md" | `ls` -> `ACARS-addendum.md SKILL.md` | cut (false now) |

## docs/HONEYPOT_FAIL2BAN.md

| claim as written | evidence | fix |
|---|---|---|
| `honeypot-website.conf` live-only, in no VCS (NEEDS OPERATOR DECISION) | tracked in `csexecutiveservices-website/nginx/snippets/`; `cmp` live == tracked | resolved |
| Trapped vhosts: dispatch, cloud, dav, www | live includes also `agents.`, `invite.executivestandard.` | added |
| Untrapped list (9 files) | live conf.d now also has `members.`, `invite.`, `www.`, `agents.`, `es-headers.conf`, `00-log-format-cfreal.conf` | re-derived |
| Deploy recipe `sudo cp ... /etc/fail2ban/...`; "cf-honeypot-notes.sh runs from the repo path" | actions call `/usr/local/libexec/ctdc/installed-check.sh` + libexec copies; installed by `install-root-copies.sh` | recipe replaced |
| `jail.local` holds `cftoken`/`cfzone` (re-verified 08-19/08-23) | `jail.local` 53 bytes; action uses `CFTOKEN_FILE=/etc/corporatetraveldc/cf-honeypot.token` | zone id only |
| (missing) rate-limit jail state | `fail2ban/jail.d/nginx-limit-req-corporatetraveldc.conf` action `%(action_)s`; live == tracked | added; lockdown action unused |
| Ban counts / CF edge working | root-only logs | [UNVERIFIED]; SELinux label concern added |

## docs/BOARD_SIGNING.md

| claim as written | evidence | fix |
|---|---|---|
| Tiers key/normal/high/cosign | `main.py:271-285` adds `signed` (council, council close, workspace contribute) and normal for approvals/council reads | added `signed` + route list |
| `high`: "future delete/pin/ack" | no route maps to high/cosign | stated "none yet" |
| Operator registers with `~/.ssh/cowork_ed25519.pub`; client fallback cowork | `~/.ssh/corporatetraveldc_ed25519 -> cowork_ed25519` (symlink), no passphrase; `board-sign.sh:27` order | corrected; passphrase-less admin key disclosed |
| "ctdc-agent" examples | renamed to `ctdc-agent-anthropic-claude` (`/etc/ctdc-accounts.conf`) | updated |
| Replay cache (no caveat) | `board_sign.py:76-82` in-process; single uvicorn worker | restart caveat added |
| Registry commands | `board-signer-ctl.sh` also has `rename` | added |

## docs/OPERATOR_CONSOLE.md

| claim as written | evidence | fix |
|---|---|---|
| Public vhost returns 404 for /console | nginx does; Cloudflare Access 302 answers first (curl) | both stated |
| "tailnet only" (mechanism unstated) | firewalld: 443 not open on LAN zone; `tailscale0` trusted; app `CONSOLE_HOSTS` | mechanism added |
| `agent-gateway.sh kill-all` "freezes the gateway" | `agent_gateway.kill_all()` sets a DB flag; vhost/ingress untouched | stated that the hostname stays up |
| Verifier `executivestandard-website/verifier/verify.py` (poller image) | quadlet mounts it at `/opt/es-verify.py`, `127.0.0.1:8787` | detail added |
| (missing) 90-day device idle, 120 s hand-off, 10,000-use cap | `es_invites.py:40-48` | added |

## docs/ISO_42001_ALIGNMENT.md

| claim as written | evidence | fix |
|---|---|---|
| A.3: "no segregation-of-duties structure" | separate agent uids, no sudo, requester != approver (`governance.py:243`), kill quorum | Partial |
| A.4: "network bandwidth caps" exist | GUARDRAILS §1: none | gap stated |
| A.5 row titled "Data for AI systems" (flagged) | duplicate of A.7 | retitled "Assessing impacts of AI systems" [UNVERIFIED against standard text] |
| A.7: "append-only 90-day audit log" | archive design | corrected |
| Operator's personal name in A.5 | PII hygiene | removed |
| (missing) A.10 agent gateway | `agent_gateway.py` | added |

## docs/SECRETS_NAMING_AND_ISOLATION.md

| claim as written | evidence | fix |
|---|---|---|
| `cf-honeypot.token` consumer = fail2ban, **isolated** | action reads `/etc/corporatetraveldc/cf-honeypot.token` (`container_file_t`); `~/.secrets` copy labelled `ctdc_secret_cf_honeypot_t` | finding; status corrected |
| `*.pub.asc (4 files)` and `*-signing.env` presented as files | `ls -la ~/.secrets` -> symlinks into repos | corrected |
| Containers receive credentials via `EnvironmentFile` (unspecified) | 9 `svc/*.env` + 8 third-party secret files | `/etc/corporatetraveldc` registry added |
| "31 active host services, all unconfined" | not re-derived | [UNVERIFIED] |

## docs/auth-token-proxy-pattern.md

| claim as written | evidence | fix |
|---|---|---|
| Token stored in `dispatch-secrets.env`, runner "inherits dispatch-secrets.env via EnvironmentFile" | runner quadlet: `dispatch.env` + `svc/runner.env`; `runner.allowlist:12` | corrected |
| Public `dispatch-runner.` hostname reaches the proxy with token injection | runner-demo `DISPATCH_BASE_URL=http://100.x.x.x:8004` (demo-api), not web | corrected; analysis reframed |
| `api/v1/cui/status` in `_TIER1_PATHS` makes it Ops-visible | web `require_tier(Tier.T2)` (main.py:2143); token is cert | flagged as no-op |
| Rotation: revoke `--prefix ctdc_runner_` then create | prefix is shared by all runner tokens (`auth.py::_token_prefix`) | order and caveat fixed |
| `--expires` "currently unused platform-wide" | CLI default 365 | corrected |
| Line numbers (1494, 1527, 1550...) | now 1803, 1833, 1856 | dropped (symbols only) |
| Runner `:8001` via `tailscale-dispatch-runner.conf` | confirmed; also bound `127.0.0.1` | kept |

## docs/GPG_KEYS_PUBLISHED.md

| claim as written | evidence | fix |
|---|---|---|
| Keys published on the blog (live) | `curl` blog `/keys/developer.pub` -> 302 `/welcome` | corrected |
| www serves identical bytes | sha256 of all 5 www files == repo | kept |
| UID column with personal email | PII rule | redacted |

## docs/SOVEREIGN_SKILL_SUPPLY_CHAIN.md

| claim as written | evidence | fix |
|---|---|---|
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` set | `grep -c NONESSENTIAL ~/.claude/settings.json` -> 0 | corrected |
| synced/ trashed next launch | `~/.claude/skills/synced` exists (1 entry) | corrected |
| Phase 2 blocker: tokens embedded in skills; repo inclusion pending | repo `skills/` has 8 skills, signed; only placeholders/"revoked" notes | resolved |
| 25 skills vendored | `~/.claude/skills` 26 entries | updated |
| (missing) skill grants | `skill-grants.sh`, `vendor-pins.txt` (16), root timer | added |

## docs/regulated-operator-setup.md

| claim as written | evidence | fix |
|---|---|---|
| ntfy gets `dispatch-secrets.env` via EnvironmentFile; Twilio keys there | `ntfy.container`: only `ntfy-secrets.env` | corrected (Twilio guidance now points at ntfy-secrets.env) |
| Audit log 90-day prune | archive | corrected |
| "captures all ... alert dispatches" | only `admin.alert.push`; pusher fan-out not audited | narrowed |
| "No open ingress ports" | firewalld LAN zone opens ssh + 80/tcp | corrected |
| GitHub PAT 30-day reminder via ntfy | no timer/script found | flagged |
| Setup script path `/opt/corporatetraveldc/ctdi-dispatch-internal/...` | symlink to private path | private path used |

## docs/HEADLESS_ACCESS.md

| claim as written | evidence | fix |
|---|---|---|
| SSH rule: dst `tag:headless-admin`, users `["corporatetraveldc"]`, action accept | tracked `tailscale/policy.hujson:94-100`: action `check`, dst server tag + `autogroup:self`, users `autogroup:nonroot` + operator; no headless-admin tag owner | corrected; live policy [UNVERIFIED] |
| (missing) agent accounts reachable as Tailscale SSH targets | two agents have `/bin/bash` | added |
| (missing) LAN sshd path | `ss -ltn` 0.0.0.0:22; firewalld ssh | added |
| "re-verified ... nothing needed correcting" (2026-08-23) | prefs now include auto-update, version 1.102.4 | refreshed |

## [UNVERIFIED] items carried in the docs

- Current sudoers contents (`/etc/sudoers.d` unreadable; no sudo run).
- Live tailnet ACL (admin console) vs the tracked `tailscale/policy.hujson`.
- Cloudflare Access application list (only behaviour observed).
- fail2ban ban counts and whether Cloudflare edge bans succeed since 2026-10-03.
- `auth_tokens` rows (expiry, the retired mcpo admin token, runner token).
- `board_signers` / `approval_signers` rows (liveness journal shows `key=active` for three agents).
- Whether the 0062 chain trigger is installed in live Postgres and whether any archive checkpoint exists (inferred none: rows start 2026-08-16, horizon 90 d).
- ISO/IEC 42001 Annex A titles.
- Public mirror pushes since 2026-10-03; skill snapshots in the vault.

## Findings for the operator

1. **Possible broken Cloudflare edge bans (control may not be enforcing).**
   The fail2ban action reads `/etc/corporatetraveldc/cf-honeypot.token`,
   labelled `container_file_t`; the SELinux grant for `fail2ban_t` covers only
   `ctdc_secret_cf_honeypot_t`, which labels the unused `~/.secrets` copy.
   If `fail2ban_t` cannot read `container_file_t`, every edge ban since
   2026-10-03 fails while firewalld bans still work. Check AVCs and the
   fail2ban log; relabel the `/etc` file or extend the module.
2. **NOPASSWD `dnf remove *` / `semanage port -a *` (if still installed)
   bypass the approval gate.** The gate is a convention for these; any
   operator-uid process (including agent sessions the operator starts) can
   run them unattended. The ollama/governor NOPASSWD entries point at units
   that no longer exist. Run `sudo -n -l` and prune.
3. **Audit chain has no verifier, no role split, no UPDATE/DELETE guard.**
   The `dispatch` role can rewrite `audit_log` and recompute hashes; detection
   depends on signed checkpoints that (by date) have not been produced yet,
   and nothing in the repo reads `row_hash` back. Governance events
   (approvals, gateway, invites, console) are not in `audit_log` at all.
4. **Operator's admin board key is passphrase-less**
   (`~/.ssh/corporatetraveldc_ed25519 -> cowork_ed25519`): any operator-uid
   process can sign as the admin signer (cosign, single-handed kill orders).
   Approvals are protected by the separate approval key; board admin actions
   are not. `cosign`/`high` tiers are unused at HEAD.
5. **Long-running containers still start without a manifest check** (open
   operator decision since 2026-08-19).
6. **Integrity sweep failing right now** (`corporatetraveldc-integrity-sweep.service`
   failed 18:05Z): working-tree change to `scripts/stack-refresh.sh` not in
   the signed manifest; `corporatetraveldc-skill-grants` is holding for the
   same reason. Expected while another session edits; clears on sign.
7. **Untracked live config:** nginx vhosts `acars.`, `adsb.`,
   `dispatch-runner.` and `000-default-catchall.conf`, and
   `/etc/fail2ban/jail.d/sshd-corporatetraveldc.conf`, exist in no repo.
8. **Honeypot gaps:** catch-all and 9 vhosts (incl. `members.` and the public
   `dispatch-runner.`) have no trap include.
9. **Blog-hosted GPG keys are no longer public** (302 to `/welcome`);
   `docs/GPG_KEYS_PUBLISHED.md` and any external pointers to the blog `/keys/`
   URLs should use www.
10. **`ntfy` config comment and Twilio placement drift:** `/etc/ntfy/server.yml`
    lines 25-28 say Twilio creds come from `dispatch-secrets.env`, which the
    ntfy container no longer reads.
11. **Network usage back at ~240 GiB/day** (Sep 4.97 TiB); the SWIM scoping
    fix is still the only designed answer. Relevant to COGS.
12. **Minor code/comment drift:** `require_admin` docstring
    (`src/auth/auth.py`) still says the prune skill deletes rows after 90 days;
    `verify-manifest.sh`/`verified-exec.sh` cite a "Signed Manifest Integrity"
    section that does not exist; `cloudflared/config.yml` still routes
    `ollama.example.com` (falls to the 444 catch-all);
    `tailscale/policy.hujson` still lists `tcp:11434` (Ollama);
    `/api/v1/cui/status` audit writes the first 12 raw characters of the
    bearer token as `token_prefix` (`main.py:2153`), one character past the
    `ctdc_admin_` prefix for admin tokens.
13. **`execstandard-verifier` has no `Memory=`/`CPUQuota=`** (the only
    quadlet without a memory cap).
14. **Board replay cache resets on web restart** (in-process); a signature
    captured within 300 s before a restart can be replayed once.
15. **Tailscale SSH `users: autogroup:nonroot`** makes the agent accounts with
    login shells valid SSH targets for the tailnet owner; confirm intended.
