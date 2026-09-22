# CTDI Dispatch Platform — Blind Adversarial Security Audit & Penetration Test

| | |
|---|---|
| **Target** | Corporate Travel Dispatch Intelligence (CTDI) — `/opt/corporatetraveldc/private/ctdi-dispatch-internal` |
| **Environment** | Live production. Raspberry Pi 5, Fedora, rootless Podman + systemd quadlets, Cloudflare Tunnel + Cloudflare Access, Tailscale, nginx, local Ollama inference, real FAA SWIM/NWWS feeds, real operational data, single-operator business. |
| **Date of assessment** | 2026-08-26 |
| **Auditor** | Opus 5 — independent blind adversarial review (lead + four parallel specialist subagents; all findings re-verified live by the lead before inclusion) |
| **Assessment type** | White-box source review + live grey-box penetration test against the running production system |
| **Report status** | Final |

---

## 1. Provenance / method

This was a **fully blind** pass. Deliberate constraints, held throughout:

- **No prior-art reading.** `CLAUDE.md`, everything under `docs/investor-materials/v1.5/research/`, and every file whose name contained `AUDIT`, `REVIEW`, `DRIFT`, `LIVE_STATE`, `PENTEST`, or `FINDINGS` was excluded from reading by me and by every subagent, by explicit instruction. Prior findings exist in this repository; none of them informed this report. Where a source file's own inline comment referenced a past finding, that comment is treated as *source code I read*, not as an audit document — and every such claim was re-verified live rather than accepted.
- **Structure.** I personally audited the auth/web/runner/demo request path and the network edge, and ran every live probe. Four specialist subagents fanned out over (a) `scripts/` + the signed-manifest integrity chain, (b) container/quadlet/nginx/deploy configuration, (c) ingest/poller/parsers/LLM, (d) db/second-brain/token. **Every high-severity subagent finding below was independently re-verified by me** against the live system before it entered this report — the verification commands shown are ones I ran myself, not relayed.
- **Live-verified, not theoretical.** Every finding carries a verification step executed against the running system.
- **Non-destructive discipline.** Read-only operations only. 1–2 requests per endpoint. No fuzzing, no brute force, no `DELETE`, no `POST` that mutates live state (route reachability was proven with schema-validation probes that return `422` before handler logic runs). Two findings (**C-1** alerting-outage trigger, **M-5** rate-limit exhaustion) were deliberately **not** fired because doing so would have degraded the live system; they are evidenced from configuration and composition and flagged as such.
- **No secret values appear anywhere in this report**, including partially. Credentials, tokens, GPS coordinates, key material, and PII are described by kind, shape, and location only. Fingerprints are shown by their last 8 hex digits at most.

---

## 2. Overall assessment

> ### ⚠ Read this first
> **Thirteen live production credentials — all six FAA SWIM NEMS username/password pairs and the NOAA NWWS-OI account JID — were published to a world-readable GitHub repository and are still the values in use today.** I verified this by SHA-256 comparison (values never printed) between the former public mirror's tree and the live secrets file. They were public for roughly 65 days, and a force-push does **not** remove them from GitHub. A partial rotation happened — `NWWS_PASSWORD`, `NTFY_TOKEN`, `DISPATCH_ADMIN_TOKEN`, and both Jumpseat keys were changed — but the SWIM set was missed. **Rotating the FAA SWIM NEMS password is the single highest-value action available and should precede everything else in this report.** See **C-0**.

**The application layer of this platform is genuinely well-built and, in most respects, well-defended. The trust foundation beneath it is not — and that inverts the risk picture.**

Start with the good, because it is real and most of it was verified working against the live box. The tier model in `src/auth/auth.py` is coherent and enforced: probing the public surface, admin routes returned `403`, Tier-1 routes `403`, the non-public board thread `403`, vault-research routes `401` — every application gate held. Cloudflare Access is genuinely enforced (not assumed) on `dispatch.`, `ollama.`, `openwebui.`, `pihole.` — all four returned a real CF Access `302` to an unauthenticated request. The self-hosted ntfy server is `deny-all` and returned `403` to every anonymous topic read. The SSRF guard rejected loopback and link-local. Path-traversal defences (multi-round percent-decoding, `realpath`-prefix checks) are correct. Board nonces/tokens use CSPRNG values, stored hashed, consumed by an atomic conditional `UPDATE`. Webhooks fail closed and use constant-time comparison. Admin authorization audits denials with redaction. `openapi.json` is genuinely gone. The `FedoraWorkstation` firewalld zone has had its stock `1025–65535` blanket-open stripped, which single-handedly keeps roughly eight `0.0.0.0`-bound services off two untrusted LANs — a genuinely good call. This is a serious posture and it deserves to be stated plainly.

**But underneath it, the signed-manifest integrity chain — the mechanism the entire system uses to decide that code is trustworthy before running it — does not survive contact with the one adversary its own comments repeatedly name: "someone with code execution as this user."** I verified four independent facts, each sufficient on its own, that collapse it:

1. **The manifest signing key has no passphrase and sits in the box's own GnuPG store.** `verify-manifest.sh` accepts a signature from either the operator's fingerprint or an "agent" fingerprint (`…19390B37`). I confirmed by reading the private-key store that the key with keygrip `…D7A5E6`, which is that agent fingerprint, is stored **unprotected** — no passphrase — while every other key on the box is protected. I then confirmed that **the manifest currently deployed was signed by exactly that unprotected key.** So anyone (or any prompt-injected agent) with a shell as `corporatetraveldc` runs one `gpg --detach-sign` and produces a manifest that every downstream gate — `verified-exec.sh`, the integrity sweep, `lockdown.sh`, `push-public.sh` — accepts as authentic. The ntfy approval round-trip and session-grant machinery are speed bumps inside one wrapper script, not properties of the key.
2. **`verify-manifest.sh` `source`s a world-/user-writable config as bash *before* it verifies anything.** `security/signing.env` is mode **0644** and writable by this user; line 77 `source`s it, ahead of the gpg verify and the checksum pass. `source` executes the file. And `lockdown.sh`/`restore-network.sh` — which call `verify-manifest.sh` — are wired as **fail2ban ban/unban actions**, and fail2ban runs as **root** (I confirmed it active, `User=` unset). So two lines written into a 0644 file execute as root at the next ban. This composes directly with C-1 below, which lets an unauthenticated stranger *trigger* a ban on demand.
3. **Verification is blind to added files.** `verify-manifest.sh` never enumerates the filesystem (`grep -c ls-files/find/comm` → 0); `sha256sum -c` only walks the manifest's own list. I proved in an isolated scratch dir that a new, unlisted file passes cleanly. Because every container puts `/app/src` on `PYTHONPATH` and no container is read-only (0 of 64), dropping `/app/src/sitecustomize.py` yields arbitrary code in every Python process while the sweep still reports OK. Python's `sitecustomize` auto-import makes this a one-file, zero-privilege persistence mechanism the integrity chain cannot see.
4. **The public mirror is leaking the operator's real identity right now, through channels neither scrubber layer inspects.** I confirmed on the live `public` ref: three `.gpg`-named files whose OpenPGP user-ID packets bind the operator's real name and business domain (`.gpg` is armored base64, invisible to byte-level scrubbing); eight nginx config **filenames** containing the forbidden business domain (the scrubber rewrites content, never paths); and four commit-author records carrying that same domain.

Alongside those, the deployment layer has its own Critical: **the full ~95-key secrets file is injected into every third-party container.** I confirmed by inspection that `ntfy`, `ultrafeeder`, `acarshub`, `fr24feed`, and `dumpvdl2` — none of them CTDI code, all of them parsing untrusted network/RF input — each carry 42 secret-shaped environment variables including the Cloudflare account-management token, the Tailscale API key, the FAA SWIM and NWWS passwords, and the passphrase for the very signing key from point (1). Any RCE in any of those images reads `/proc/self/environ` and takes the whole credential set — and can then re-sign a tampered manifest. The correct pattern (a scoped `demo-secrets.env` with a single key) already exists and is applied to the two demo containers; it simply was not applied to the nine feeders that run other people's code.

Then there is the **network edge**, where the application's careful controls are undone by exposure they do not cover. The operator's **physical receiver coordinates are published to the open internet, unauthenticated** (C-2): `adsb.example.com/data/receiver.json` returns lat/lon at metre precision with no Cloudflare Access. This is the value the codebase treats as its single most protected secret — there is a long comment about it having reached the public GitHub mirror, the scrubber was extended to catch it, and the app serves a *decoy* lat/lon to untrusted callers — all undone by a receiver web UI on a sibling hostname. And a **remotely-triggerable one-hour alerting outage** (C-1): because the nginx rate limiter keys on cloudflared's loopback address, a fail2ban jail with `maxretry=1` and no loopback exemption, wired to a stack-wide lockdown action, means roughly five HTTP requests per second from any stranger severs the operator's LLM and alert-push pipeline for an hour — and, via fact (2) above, that same ban is a root-code-execution trigger.

**And the alerting pipeline itself — the product — has its own class of failure (§4b).** Three patterns recur there, each verified live. *Empty is not distinguished from failed*: a 200-OK non-CSV response deletes the entire 519,991-row OpenSky registry and returns `{"ok": True}` (C-7), and five poller fetchers mark themselves healthy after fetching nothing (H-12). *Heartbeats prove a socket, not a data path*: SWIM stamps healthy through a 100% parse failure so REST failover never engages (H-8), and the ACARS watcher has been **silently dead for ~40 hours** — 477 HTTP 403s, zero alerts fired, `systemctl` reporting `active`, and no heartbeat written at all (C-8). *Untrusted text is treated as trusted structure*: anyone who can get a page indexed by Google News can author the body, influence the title, and choose the tap-through URL of a priority-4/5 push on the operator's phone (C-9), with `javascript:` and `data:` URIs passing the only validation there is. The corrective pattern for the first class already exists in this codebase — `db.faa_upsert_ladd()` refuses an empty replacement with a comment describing exactly this failure — and was never generalised to the two sweep functions 47 lines away.

**Two systemic patterns explain nearly everything else.** First, **controls written correctly for the case in front of the author, never revisited when a second consumer arrived**: wildcard CORS still on the runner after the web API's was fixed (H-1); the demo password gate bypassable from the LAN because RFC1918 is blanket-trusted and nginx:80 is LAN-open (H-2); scoped secrets applied to demo containers but not feeders (C-4); the empty-input guard applied to LADD but not to the two registry sweeps (C-7). Second, **fail-silent operational tooling on a platform whose entire product is not failing silently**: 2 of 34 user services have an `OnFailure=` notifier, eight skills swallow every exception and exit 0, `freshness_audit` writes its verdict to a file nothing reads, both feed-silence watchdogs are disabled, and a real service failed eight hours before this audit with nobody told.

**Bottom line, honestly calibrated.** This is not a careless system — the opposite. The application code shows rare adversarial self-awareness, and many of the findings below are things the codebase's own comments already worry about in prose. The scrubbing pipeline, the tier model, the CF Access posture, and the firewalld hardening are all genuinely good work, and the current public mirror is clean. But an integrity chain a local shell can forge is not an integrity chain; a receiver map on the open internet is a physical-safety leak for an executive-protection business run from a residence; and a set of government aviation credentials that sat public for 65 days and was never rotated is an open door that no amount of downstream hardening closes.

**Order of work: rotate the SWIM/NWWS credentials (C-0) today — before anything else.** Then restore the dead ACARS watcher and guard the registry sweeps (C-8, C-7) — those are live functional failures, not latent risks, and one of them is one bad upstream response away from destroying half a million rows. Then the signing key (C-3), the `signing.env` root-exec path (C-5), the alerting-outage trigger (C-1), and the location leak (C-2). Then the secret fanout (C-4), the notification injection path (C-9), and the mirror de-anonymization (C-6).

Nothing in this report indicates the system is currently compromised. Two things in it are *currently broken in production* (C-8, H-11), one is *currently leaking* (C-0, C-2, C-6), and the rest is about how cheaply the remainder could be broken, and by whom.

---

## 3. Severity summary

| # | Severity | Finding | Verified by |
|---|---|---|---|
| **C-0** | **Critical** | **13 live credentials leaked to the public GitHub mirror and never rotated** — all 6 FAA SWIM NEMS user/pass pairs + NWWS-OI JID; ~65 days world-readable; still SHA-retrievable after force-push | SHA-256 hash comparison (no values) of the former public HEAD's tree vs the live secrets file: 13 of 24 comparable keys identical |
| **C-1** | **Critical** | Unauthenticated remote 1-hour alerting outage: tunnel-loopback rate-limit keying + `maxretry=1` fail2ban jail wired to a stack-wide lockdown — and (via C-5) a root-code-exec trigger | Live config chain + live proof the trigger hostname is internet-open with no CF Access. **Not fired.** |
| **C-2** | **Critical** | Operator's physical receiver location + live ADS-B/ACARS feeds published to the open internet, no auth, no Cloudflare Access | Live external `curl`: `receiver.json` 200 with metre-precision lat/lon; tar1090 + ACARS Hub UIs 200 |
| **C-3** | **Critical** | Signed-manifest chain is self-forgeable: the agent signing key has **no passphrase**, lives in the box's keystore, and signed the live manifest | Read private-key store (protection flag only); confirmed live manifest's `VALIDSIG` fp = the unprotected key |
| **C-4** | **Critical** | Entire ~95-key secrets file injected into every third-party container (ntfy, SDR feeders); one RCE there yields CF/Tailscale/SWIM creds + the signing-key passphrase | `podman inspect` env-var **names** on 5 third-party containers: 42 secret-shaped vars each, incl. all high-value keys |
| **C-5** | **Critical** | `verify-manifest.sh` `source`s a 0644 user-writable config as bash before verifying → root RCE via the fail2ban ban path (root) | `stat` mode 0644 + writable; read `:69-79`; confirmed fail2ban active/root and calls `lockdown.sh`→`verify-manifest.sh` |
| **C-6** | **Critical** | Public git mirror leaks operator identity: 3 `.gpg` files with real name/domain in UID packets, 8 filenames with the business domain, 4 commit-author records | Live `public` ref: `gpg --list-packets`, `git ls-tree`, `git log --format=%ae` |
| **H-1** | **High** | Wildcard CORS on the runner turns server-side Tier-1 token injection into cross-origin exfiltration of the live EP watchlist and private chat | Live: `Origin: evil` → `ACAO: *` + real watchlist/chat body (same path direct to web API = `403`) |
| **H-2** | **High** | Demo password gate + signal sanitisation bypassable from either LAN (box is dual-homed on two untrusted networks) via forged `Host` to nginx:80; RFC1918 blanket-trusted | Live through nginx:80: `{"trusted_origin":true}` / `{"tailnet":true}`; `ip addr` + firewalld zone |
| **H-3** | **High** | Manifest verification blind to **added** files + no read-only containers + `/app/src` on `PYTHONPATH` → `sitecustomize.py` persistence invisible to the sweep | Live: `verify-manifest.sh` never enumerates FS; scratch-dir proof; 0/64 read-only; live container PYTHONPATH |
| **H-4** | **High** | `POST /api/ask` — unauthenticated internet-reachable LLM inference with caller-chosen model on a Pi already at load 26 | Live `422` schema probe on internet-facing `:8005` and `:8001`; live `uptime`/`ps` |
| **H-5** | **High** | Nextcloud vault host internet-exposed: no CF Access, **zero** edge rate limiting, exact version disclosed | Live external: `status.php` 200 w/ version; `grep -c limit_req` = 0 on both vault vhosts |
| **H-6** | **High** | `public`-push scrub guard bypassed by 4 URL spellings + an env-var override; pre-push secret scanner defeated by any line containing `<…>`/`example`/`...` | Subagent ran the exact hook pipeline on variants; lead confirmed match-logic in `scripts/pre-push` |
| **H-7** | **High** | `dumpvdl2` runs SELinux-unconfined (`spc_t`, label disabled) with the full secret set — the one SDR container the documented hardening skipped | `podman inspect` SecurityOpt `[label=disable]`; `/proc/<pid>/attr/current` = `spc_t` vs `container_t` elsewhere |
| **M-1** | Medium | `_is_trusted()` remains X-Forwarded-For-spoofable when `CF-Connecting-IP` absent / Host not allowlisted | Live: header permutations, `tailnet:true` for the spoofable cases |
| **M-2** | Medium | NOPASSWD sudo grants (`dnf remove *`, `semanage port -a *`, fan-daemon mask) convert user code-exec into root destruction / thermal DoS | Live `sudo -n -l` |
| **M-3** | Medium | Fail-silent ops: 2 of 34 user services have `OnFailure=`; a service failed 8h pre-audit unnoticed | Live `systemctl` + `grep -l OnFailure` + journal |
| **M-4** | Medium | Deployed nginx/quadlet config diverged from tracked/signed source — live-only files (incl. the internet-facing vhost) outside the integrity chain; a redeploy would break nginx | Live file diff both directions |
| **M-5** | Medium | Global (not per-caller) in-process rate limiters are self-DoS levers; the unauthenticated one is remotely trippable | Source + live anonymous reachability of `/api/v1/whoami-token`; not exhausted |
| **M-6** | Medium | Manifest rollback/replay unmitigated (no counter/timestamp in signed body); every old signed pair verifies forever | Source review of `verify-manifest.sh` / manifest format |
| **M-7** | Medium | `populate-secrets.sh` writes secrets via `sudo sed` argv → plaintext into the sudo/authpriv log; unescaped replacement corrupts some values | Source `:53`; confirmed sudo default logging + argv visibility |
| **M-8** | Medium | `Containerfile.web` still ships `--forwarded-allow-ips=*` → audit-log IP forgery for the admin audit trail | `Containerfile.web` CMD; source of `remote_addr` persistence in audit rows |
| **L-1 … L-9** | Low | Recon leakage; demo ADS-B unsanitised; `mcp.` open-but-dead; RingCentral token echo; prefix-only revocation; world-readable secret files (2); base-tag image pinning; FlightAware UUID in a tracked file; stale artefacts | Live probes / source review (enumerated in §4) |

Additional findings from the git-history sweep, folded into the tail below: **M-9** (tag `v1.0.0` on `origin` still carries both secret files — detailed under C-0), **M-10** (real live infrastructure values published in `config/dispatch.env`), **L-10** (`.gitignore` lacks generic secret-file patterns).

### Ingest / poller / alerting-path findings (§4b)

| # | Severity | Finding | Verified by |
|---|---|---|---|
| **C-7** | **Critical** | Mark-and-sweep registry wipes: a 200-OK non-CSV/valid-but-empty parse deletes the **entire** OpenSky (519,991 rows) and FAA (316,222 rows) registries, returns `ok:True`, and poisons the freshness probe so it never self-heals | Read both `*_sweep_removed()` — bare `DELETE`, no empty guard; caller only catches exceptions; live row counts queried read-only |
| **C-8** | **Critical** | The ACARS watcher has been **silently non-functional for ~40 hours**, fires zero alerts, and reports `active` — revoked token → HTTP 403 → empty watchlist, and it writes **no heartbeat at all** | Live: `is-active`=active, **477** HTTP 403s in 7d (most recent minutes before this audit), last good watchlist load 2026-08-24 17:04, `grep -c` heartbeat/feed_state = **0** |
| **C-9** | **Critical** | Prompt injection from any Google-News-indexed page → attacker-authored ntfy body, attacker-influenced title, and **attacker-chosen tap-through URL** (no scheme validation) on a priority-4/5 channel | Live: `urldefrag('javascript:…')` and `data:` pass through unchanged; `click_url=item["url"]` at `osint_monitor.py:516`; both central sanitizers verified no-ops against prose |
| **H-8** | **High** | SWIM heartbeat proves the socket, not the data path — a 100% parse failure stamps healthy, so REST failover never engages; `push:fns`/`push:tfms` unmapped in the backstop | Source + the backstop's own map; live: both NOTAM paths simultaneously dead with `consecutive_failures=0` |
| **H-9** | **High** | Squawk **7700** (universal ICAO emergency) is classified as Marine One → undeduped priority-5 "POTUS MOVEMENT" push; distance gate skipped on one source | `MARINE_ONE_SQUAWKS = frozenset({"7700","5000","5001"})` at `fdps_parser.py:180` **and** `local_airspace.py:74`; `EMERGENCY_SQUAWKS` at `:75` overlaps on `7700` |
| **H-10** | **High** | The Marine One / squawk-7700 ntfy path never checks the HTTP response, then records `ntfy_fired=1` unconditionally — 50 live rows all claim delivery, none confirmed | Read `local_airspace.py:110-127`: no `raise_for_status`, no status check, return value unused; least-hardened of three parallel senders |
| **H-11** | **High** | `flight-cleanup` inherits a 120s timeout and is SIGKILLed on every run; 30-day retention is currently non-functional and the DB is **23.3 GB** with `auto_vacuum=0` | Live: `flight_events` = 840,271 rows; DB file 23,256 MB; `PRAGMA auto_vacuum` = 0 |
| **H-12** | **High** | Poller fetchers mark themselves healthy after fetching nothing (`error=None`, `failures=0`) — a total FAA NOTAM API outage reports as a clean poll | Subagent reproduced on a temp DB across 5 fetchers; `upsert_feed` resets both fields |
| **H-13** | **High** | Spoofed over-the-air ACARS is attacker-controlled alert content **and** terminates a real watchlist session (bypasses ADS-B corroboration) | Source: `pusher/main.py:326-340`, `:446-465`; reachable via VHF or any tailnet peer (UDP 5005 / TCP 9080 in firewalld `trusted`) |
| **M-11+** | Medium | ~18 further items: 30 kt wind fires a Pushover **Emergency siren** every 30s; blocking SQLite in async loops (I hit `database is locked` three times during this audit); 10+ tables with no prune path; ITWS alerts never expire and unparseable hazard fields render as "clear"; `freshness_audit` notifies nobody; 8 skills swallow all exceptions and exit 0; scrub gate bypassed on the Ollama-down fallback path in 8 skills; LLM-guessed domains auto-subscribed and fetched with no SSRF guard (**2 already live**); two concurrent Amtrak writers | Enumerated in §4b |

### `common/` / `shared/` findings (§4c)

| # | Severity | Finding | Verified by |
|---|---|---|---|
| **M-11** | Medium | Unauthenticated-*within-app* route drains a billable FlightAware API, uncached and unthrottled (`/api/v1/fids/BWI/arrivals`) — **severity corrected down from the agent's High**: the path is CF-Access gated, so the attacker is an authenticated/tailnet caller, not the open internet | `grep -c` auth deps in `fids.py` = 0; live external probe with an *invalid* airport (no billable call) → CF Access `302`; no cache/throttle in `flight_resolver.py` |
| **M-12** | Medium | Model-hallucinated entities drive a live unguarded fetch-and-persist chain — `entityname` (the prompt's own placeholder) stored **398 times across all 6 categories**; 2 auto-discovered feeds already live | Read the live state files: 828 entity records / 1.63 MB; `user_rss_feeds.json` shows 2 `"discovered": true` entries |
| **M-13** | Medium | `feed_resolve.resolve_source` SSRF — no `ssrf_guard`, `follow_redirects=True`, reachable at Tier 1; sibling `rss_custom()` was hardened, this path was missed | `grep`: 2× `follow_redirects=True`, 0 guard matches |
| **M-14** | Medium | Signal-sanitisation exempts the `dispatch` body — masking tail numbers on a zone topic still leaks them on the topic the operator's phone subscribes to | Source `sector_coalesce.py:563-568`; latent (no flags set live) |
| **L (5)** | Low | `ollama_lock` HOT_MARKER has no PID-liveness check (permanent starvation after a SIGKILL — and there were 33 in 7 days); `guardrails.mutation_gate` persists unredacted URLs for 90 days; watchlist idents not URL-encoded into third-party paths; `push_dedup` check/record outside the flock; one more non-atomic state write | Source review |

**Totals: 10 Critical, 13 High, ~32 Medium, ~35 Low.**

---

## 4. Findings in detail

> Findings C-3, C-4, C-5, C-6, H-3, H-6, H-7, M-6, M-7 originate from specialist subagents and were **re-verified live by the lead**; the verification commands shown are the lead's own re-runs.

### C-0 — Critical — Thirteen live production credentials were published to the public GitHub mirror and have never been rotated

**Artefacts:** `dispatch-secrets.env` (repo root, on the former public lineage) · `src/acars_watcher/secrets.env` · former public HEAD `0d4f677` · tag `refs/tags/v1.0.0` (`92dd5b6`, present on `origin`)

**What happened.** The public mirror `github.com/CorporateTravelDC/ctdi-dispatch` previously carried a 531-commit lineage (`0d4f677`) whose tree included the real, populated `dispatch-secrets.env`. That lineage was force-replaced on 2026-08-24 18:51 with the current 4-commit orphan lineage (`e66dd46`), which is clean. But a force-push does not delete anything from GitHub: unreachable objects remain addressable by SHA on the public repository indefinitely, and are served to anyone who knows or brute-forces the hash (and are typically already in forks, clones, and third-party mirroring caches).

**Verification I ran** — SHA-256 digests only; **no credential value was read, printed, or logged at any point**:

```
# the leaked lineage was genuinely a public remote HEAD
$ git reflog show refs/remotes/public/main
  …
  0d4f677 refs/remotes/public/main@{4}: fetch public: fast-forward     ← real former remote HEAD
  ee34104 refs/remotes/public/main@{3}: update by push                 ← the force-replace
$ git merge-base origin/main refs/remotes/public/main → (empty; orphan lineages)

# how long the secrets file sat in that public tree
  commits whose TREE contains dispatch-secrets.env: 91 / 531
  first: 80f5153  2026-06-20 22:07 EDT
  last : 976d9e0  2026-07-01 09:00 EDT
  force-replaced on public: 2026-08-24 18:51 EDT   → ~65 days world-readable

# are the leaked values still the live values? (hash comparison, values never emitted)
  leaked-file populated keys: 26 | live-file populated keys: 78
  SWIM_NMS_USER_FDPS   : *** STILL LIVE — UNROTATED ***
  SWIM_NMS_PASS_FDPS   : *** STILL LIVE — UNROTATED ***
  SWIM_NMS_PASS_STDDS  : *** STILL LIVE — UNROTATED ***
  SWIM_NMS_PASS_TFMS   : *** STILL LIVE — UNROTATED ***
  SWIM_NMS_PASS_AIM    : *** STILL LIVE — UNROTATED ***
  SWIM_NMS_PASS_TBFM   : *** STILL LIVE — UNROTATED ***
  SWIM_NMS_PASS_ITWS   : *** STILL LIVE — UNROTATED ***
  NWWS_JID             : *** STILL LIVE — UNROTATED ***
  NWWS_PASSWORD        : rotated
  NTFY_TOKEN           : rotated
  DISPATCH_ADMIN_TOKEN : rotated
  JUMPSEAT_API_KEY     : rotated
  ACARSDRAMA_JUMPSEAT_TOKEN : rotated

  OVERALL: 13 of 24 comparable keys remain UNROTATED
  unrotated: NWWS_JID, SWIM_NMS_{USER,PASS}_{FDPS,STDDS,TFMS,AIM,TBFM,ITWS}
```

**What is exposed.** The six `SWIM_NMS_USER_*` entries all hold one FAA NEMS account name, and the six `SWIM_NMS_PASS_*` entries all hold one password — so a single credential pair authenticates all six production SWIM feeds (FDPS, STDDS, TFMS, AIM, TBFM, ITWS). `NWWS_JID` is the NOAA NWWS-OI XMPP account identifier. The SWIM account name and the JID local-part share a `first.last` shape, so this is simultaneously **real-name PII and a government account identifier**.

**Impact.** An attacker holding these can authenticate to the operator's FAA SWIM subscriptions: consume the feeds under the operator's identity, exhaust or disrupt the subscription, or attribute activity to the operator's registered FAA account. Because one pair covers all six feeds, there is no partial blast radius — and equally, one rotation fixes all six. The credentials are for a government aviation data service tied to a named registered subscriber, which raises this above an ordinary API-key leak.

**Mitigating facts, stated plainly.** The *current* public mirror is clean — I confirmed `dispatch-secrets.env` is absent from `refs/remotes/public/main`. The scrubbing pipeline works today; this is historical residue the force-push did not erase. A partial rotation clearly did happen (five other leaked secrets show as rotated), so the response to the original incident was real — the SWIM set was simply missed. And the two values in `src/acars_watcher/secrets.env` (leaked across 50 commits of the same lineage) are **both rotated** — verified, 0 of 2 still live.

**Secondary exposure — tag `v1.0.0` on `origin`.** `git ls-remote --tags origin` confirms `v1.0.0` exists on the private remote, and its tree still contains **both** `dispatch-secrets.env` and `src/acars_watcher/secrets.env`. Every other ref (`main`, `origin/main`, both public lineages, `ops/session`, `v1.0.0-clean`) has dropped them. Private-repo scope, so exposure is limited to collaborators — but deleting the tag is trivial and should follow rotation.

**Fix, in strict order:**
1. **Rotate the FAA SWIM NEMS password and re-issue/rotate the NWWS-OI account.** Assume compromised — ~65 days public, still SHA-retrievable. This is the highest-value single action in this report.
2. Ask GitHub Support to purge unreachable objects on `CorporateTravelDC/ctdi-dispatch`, or delete and recreate the public repository. A force-push alone did not remove them.
3. Delete tag `v1.0.0` locally and on `origin` — **after** step 1.
4. Treat the operator's real name (recoverable from the SWIM account name and the JID) as disclosed, and factor that into the C-6 de-anonymization picture.

---

### C-1 — Critical — Unauthenticated remote one-hour alerting outage (and root-exec trigger) via rate-limit / fail2ban / lockdown chain

**Files:** `nginx/conf.d/00-rate-limit-corporatetraveldc.conf:19` (+ `:23` deployed) · `fail2ban/jail.d/nginx-limit-req-corporatetraveldc.conf:16-24` · `/etc/fail2ban/jail.conf:92` · `/etc/fail2ban/filter.d/nginx-limit-req.conf:38,49` · `scripts/lockdown.sh:121-178`

Five individually-correct decisions compose into a remote kill switch:

1. **Wrong key.** The limiter uses `$binary_remote_addr`, but every tunnel request reaches nginx from cloudflared over loopback (`cloudflared/config.yml` routes every hostname to `http://127.0.0.1:80`). So the "per-source-IP" limit is one global bucket for the entire internet. The sibling `00-honeypot.conf` documents this exact problem and builds a `$honeypot_addr` map to fix it — the rate-limit file never got the same treatment.
2. **No loopback exemption on the jail.** `maxretry = 1`, `findtime = 60`, `bantime = 3600`, and **no `ignoreip`** — while its sibling honeypot jail explicitly exempts loopback with the comment *"Never ban ourselves."*
3. **No inherited default.** `/etc/fail2ban/jail.conf:92` is `#ignoreip = …` (commented). *Caveat: `jail.local` is root-only; I could not read it, so a `[DEFAULT] ignoreip` there cannot be ruled out. The operator should settle this first with `sudo fail2ban-client get nginx-limit-req-corporatetraveldc ignoreip`.*
4. **The filter matches both zones** (`ngx_limit_req_zones = [^"]+`), so the tighter 1r/s demo-login zone trips the same jail.
5. **The action is a stack-wide lockdown, not a ban.** `scripts/lockdown.sh` rebinds Ollama loopback-only and restarts it (killing every LLM-backed feature — I confirmed all runner/poller containers reach Ollama at the tailnet address), disables the pusher's host-reach (severing alert delivery), and cycles the acars-net containers — all for `bantime = 3600`.

**Exploit.** ~5 req/s to `POST https://dispatch-runner.example.com/api/demo/login` from anywhere. nginx logs the excess as `client: 127.0.0.1`; fail2ban bans loopback in firewalld and runs `lockdown.sh` as root. Alerting and inference go down for an hour; the firewalld ban targets the address cloudflared/nginx/Nextcloud/contact-API all use. Repeatable indefinitely, free, silent. **Additionally — see C-5 — that root-run `lockdown.sh`→`verify-manifest.sh` path is itself a root-code-execution primitive**, so this same trigger is not merely a DoS.

**Verification I ran.** `curl https://dispatch-runner.example.com/healthz` → **200, no CF Access** (contrast: `dispatch.`/`ollama.`/`openwebui.`/`pihole.` all CF-Access `302`). Deployed jail == tracked. `jail.conf:92` commented. Filter regex `[^"]+`. Read `lockdown.sh:121-178` and cross-checked Ollama URLs in live `podman inspect`. **Trigger not fired.**

**Fix:** add `ignoreip = 127.0.0.1/8 ::1 100.64.0.0/10 10.x.x.x/8 192.168.x.x/16` (matches the honeypot jail); re-key the limiter to `$http_cf_connecting_ip` via a map; and drop the `corporatetraveldc-lockdown` action from this jail (keep it for the honeypot jail, where a single hit is genuinely malicious) or raise `maxretry`.

---

### C-2 — Critical — Operator's physical receiver location and live feeds published to the open internet

**Files:** `nginx/conf.d/adsb.example.com.conf` · `nginx/conf.d/acars.example.com.conf` · `cloudflared/config.yml` ingress. Severity context: `src/runner/main.py:119-152, 2329-2333`.

Both hostnames tunnel to nginx and proxy straight to the ADS-B/ACARS containers with **no application auth and — unlike `dispatch.`/`ollama.`/`openwebui.`/`pihole.` — no Cloudflare Access.** Unauthenticated, from the open internet:

- `https://adsb.example.com/data/receiver.json` → `200` with **`lat` (5 dp) and `lon` (4 dp)** — metre-level receiver position, i.e. the operator's premises. *(Value not reproduced.)* `config.js` exposes the same as `DefaultCenterLat/Lon`.
- `/data/aircraft.json` → live feed (24 aircraft, 22.8M messages). tar1090 UI and ACARS Hub (`/search`, `/api/…`) both `200`.

**Why Critical.** This coordinate is the single most protected value in the codebase: `src/runner/main.py:119-152` records both current and former residence coordinates having reached the public GitHub mirror and instructs treating them as burned; the scrubber was extended to read the real `ULTRAFEEDER_LAT/LON` and catch them; and `frontend_config()` (`:2329-2333`) serves a **decoy** lat/lon to untrusted callers (I confirmed the decoy pair is returned to an untrusted request). All of that is undone by a sibling hostname publishing the true value to anyone. For an executive-protection business run from a residence, physical-address disclosure plus a live activity feed is the highest-consequence leak in scope. Both vhost files also carry a **stale** comment ("Backend not currently deployed… no acarshub container found") — `podman ps` shows both backends up 7 days; the vhosts were written when the backends were dead and never revisited.

**Verification I ran.** External `curl` from outside the tunnel: `receiver.json` 200 with lat/lon (5/4 dp, redacted), tar1090 `<title>` 200, `aircraft.json` 200 (24 aircraft), ACARS Hub `<title>` 200. Same-batch CF-Access `302`s on the four gated hostnames prove Access is configured elsewhere and simply absent here.

**Fix:** attach Cloudflare Access to `adsb.`/`acars.` or delete their tunnel routes (operator-only diagnostic UIs). Treat the coordinates as disclosed. Add a standing check asserting every tunnel hostname is either CF-Access-gated or explicitly intentional-public.

---

### C-3 — Critical — The signed-manifest chain is self-forgeable: unprotected agent signing key, on the box, signed the live manifest

**Files:** `security/signing.env` (both pinned fingerprints) · `scripts/verify-manifest.sh:104-118` · `scripts/sign-manifest.sh` · `~/.gnupg/private-keys-v1.d/`

`verify-manifest.sh` pins the signing key to **either** `SIGNING_KEY_FINGERPRINT` (operator, ends `…D96C1631`) **or** `AGENT_SIGNING_KEY_FINGERPRINT` (agent, ends `…19390B37`). The agent key's private half is stored **without a passphrase**.

**Verification I ran.**
```
# protection flag only — no key material touched
~/.gnupg/private-keys-v1.d $ for f in *.key; do grep -qa protected-private-key "$f" \
    && echo "${f%.key}: PROTECTED" || echo "${f%.key}: UNPROTECTED"; done
  5461E0DE…D7A5E6 : UNPROTECTED (no passphrase)      ← keygrip of agent fp …19390B37
  (all six other on-box keys: PROTECTED)

# which fp owns that unprotected keygrip?
$ gpg --list-secret-keys --with-keygrip --with-colons | awk -F: …
  UNPROTECTED key fingerprint ends …19390B37   == signing.env AGENT_SIGNING_KEY_FINGERPRINT

# who signed the manifest that is deployed right now?
$ gpg --homedir <fresh> --import security/trusted-signing-key.pub.asc
$ gpg --homedir <fresh> --status-fd 1 --verify MANIFEST.sha256.asc MANIFEST.sha256 | grep VALIDSIG
  VALIDSIG … fp ending …19390B37     ← the unprotected key
```

**Exploit.** Any shell as `corporatetraveldc` (or a prompt-injected agent — and note H-4 hands unauthenticated internet callers LLM inference on this very box) modifies any file and runs `gpg --local-user <agent-fp> --detach-sign` directly. Every downstream gate — `verified-exec.sh`, `scheduled-integrity-sweep.sh`, `lockdown.sh`, `push-public.sh`, and the per-LLM-call hook — then reports OK. The ntfy approval round-trip and `grant-agent-session.sh` are logic inside `sign-manifest.sh`, which nothing compels an attacker to use; the key itself carries no such gate. The root of trust is additionally circular: `signing.env` and `trusted-signing-key.pub.asc` are themselves tracked files inside the verified tree, so an attacker who can write tracked files can repoint both to their own key — the exact "attacker who can write tracked files" the pin comment names.

**Fix (nothing else in the chain matters until this is closed):** put a passphrase on the agent key and hold it out of the tree (at minimum in `dispatch-secrets.env`), or move signing to a hardware token / a uid the agent cannot reach. Reconsider whether an unattended "agent" key should exist at all.

---

### C-4 — Critical — The entire secrets file is injected into every third-party container

**Files:** `/etc/corporatetraveldc/dispatch-secrets.env` (~95 keys, mode 0600) referenced by `EnvironmentFile=` in `ntfy.container`, `corporatetraveldc-ultrafeeder.container`, `corporatetraveldc-acarshub.container`, `corporatetraveldc-fr24feed.container`, `corporatetraveldc-dumpvdl2.container`, `…-acarsrouter/piaware/planefinder/airnavradar.container`.

Every third-party image receives the full secrets set in its process environment. **Verification I ran (env-var NAMES only, no values):**
```
$ podman inspect ntfy … | grep -c '(TOKEN|KEY|SECRET|PASS)='            → 42
  high-value present: CF_AGENT_SIGNING_KEY_PASSPHRASE  CF_MANAGEMENT_API_TOKEN
                      TAILSCALE_API_KEY  SWIM_PASSWORD  NWWS_PASSWORD
                      NEXTCLOUD_APP_PASSWORD  DISPATCH_ADMIN_TOKEN  BOARD_KEY
  same 42 + same high-value set confirmed for ultrafeeder, acarshub, fr24feed, dumpvdl2
```
`ntfy` is a push daemon; the SDR containers (`ghcr.io/sdr-enthusiasts/*:latest`, several with `AutoUpdate=registry`) parse untrusted internet and RF input. Any RCE in any of them reads `/proc/self/environ` and obtains, in one shot: FAA SWIM operational credentials, the Cloudflare **account-management** token (can rewrite tunnel ingress, DNS, Access policies), the Tailscale API key, and — critically — **`CF_AGENT_SIGNING_KEY_PASSPHRASE`**, the passphrase for the signing key in C-3. That last one means a feeder compromise can re-sign a tampered manifest and defeat the integrity sweep from inside a third-party container.

The right pattern already exists: `demo-api` and `runner-demo` were correctly given a scoped `demo-secrets.env` with a single key (I verified the internet-facing `runner-demo` carries exactly one secret-shaped var vs 43 on the tailnet runner). It simply was not applied to the feeders.

**Fix:** per-container scoped env files — the SDR feeders need `FEEDER_ID`/`SHARING_KEY`/`FR24KEY`/`LAT`/`LON`; ntfy needs `NTFY_*` only. Rotate `CF_AGENT_SIGNING_KEY_PASSPHRASE`, `CF_MANAGEMENT_API_TOKEN`, `TAILSCALE_API_KEY`, and the SWIM/NWWS passwords afterward — they have been readable from third-party process memory for some time.

---

### C-5 — Critical — `verify-manifest.sh` executes a user-writable config before verifying → root RCE via the fail2ban path

**Files:** `scripts/verify-manifest.sh:67-79` · `/etc/fail2ban/action.d/corporatetraveldc-lockdown.conf` · `scripts/lockdown.sh:28`, `scripts/restore-network.sh:13`

Line 77 `source "${SIGNING_ENV}"` runs `security/signing.env` **as bash**, ahead of the gpg verify (line ~100) and `sha256sum -c` (line 119). **Verification I ran:**
```
$ stat -c '%a' security/signing.env → 644 ;  [ -w security/signing.env ] → writable by me
$ grep -n 'source\|gpg --status\|sha256sum -c' scripts/verify-manifest.sh
  77: source "${SIGNING_ENV}"          ← executes first
  ~100: gpg … --status-fd
  119: sha256sum -c
$ systemctl is-active fail2ban → active ;  systemctl show fail2ban -p User → (root)
$ cat /etc/fail2ban/action.d/corporatetraveldc-lockdown.conf
  actionban = …/verify-manifest.sh "scripts/lockdown.sh" && …/lockdown.sh …
```
**Exploit.** Write two lines (`FINGERPRINT` assignments plus any command) into the 0644 `signing.env`; the next fail2ban ban runs them **as root**, no password, no audit. C-1 lets an unauthenticated stranger *cause* that ban on demand — so C-1 + C-5 together are a remote-triggered local-root chain, gated only on the attacker also having write to `signing.env` (which any code-exec-as-user has). The same unsafe `source`-a-user-writable-env pattern recurs in six other root-run scripts (`watchdog.sh:141` — fires every 90s as root — `renew-tailscale-cert.sh:41`, `lockdown.sh:65`, `restore-network.sh:50`, `restart-stack.sh:47`, `threat-*.sh`); the repo already has a safe `read_env_var()` grep helper it did not apply here.

**Fix:** replace every `source` of a user-writable env in a root path with `read_env_var`; chmod `signing.env` to 0600 and root-own the root-executed scripts; move the `source` after verification.

---

### C-6 — Critical — The public git mirror leaks the operator's real identity through three channels neither scrubber layer inspects

**Files:** `CorporateTravelDC.gpg`, `419A864C….gpg`, `ABD3976F….gpg` (repo root) · `nginx/conf.d/*.example.com.conf` (paths) · `scripts/push-public.sh` (commit identity)

The scrubber rewrites blob **content** and `verify_scrubbed()` scans blob **content**; it inspects neither armored-binary payloads, nor filenames, nor commit metadata. **Verification I ran against the live `public` ref:**
```
# (a) .gpg files are armored base64 → real name/domain in the UID packet, invisible to content scrub
$ for f in CorporateTravelDC.gpg 419A864C….gpg ABD3976F….gpg; do
    git cat-file -e refs/remotes/public/main:$f && \
    git cat-file blob refs/remotes/public/main:$f | gpg --list-packets | grep -c csexecutiveservices
  done
  → all three: present on public, UID packet contains the real business domain

# (b) filenames carrying the forbidden domain (scrubber never touches paths)
$ git ls-tree -r --name-only refs/remotes/public/main | grep -c csexecutiveservices → 8

# (c) commit-author identity
$ git log --format='%ae' refs/remotes/public/main | grep -c csexecutiveservices → 4  (of 4 commits)
```
`example.com` is on the scrubber's own `FORBIDDEN_LITERALS` list — its own definition of "must never ship" — yet it is live on the public mirror in all three forms. This is the C-30 (OOXML) bug class generalized: opaque encoding, plus filename and metadata channels, defeat a content-only pipeline. Positive control: a full-tree base64 sweep found only these three `.gpg` files (plus the correctly-dropped `trusted-signing-key.pub.asc`), and 743 plain-text public blobs scanned clean — the content layers do work; these are the channels they never look at.

**Fix:** add `.gpg`/`.asc` to the binary-skip/armor-decode handling; run `FORBIDDEN_LITERALS`/`SUBSTITUTIONS` over the **path** as well as the blob in both scrub and verify; set `GIT_AUTHOR_*`/`GIT_COMMITTER_*` to a scrubbed identity in `push-public.sh`. Then force-push a corrected mirror. (The keys are public by design; the leak is the *identity binding* and the domain, which a history rewrite cannot fully purge from forks/caches — treat as disclosed.)

---

### H-1 — High — Wildcard CORS on the runner turns server-side token injection into cross-origin exfiltration

**Files:** `src/runner/main.py:162-163` (`allow_origins=["*"]`) · `:1649-1685`, `:1596-1612` (token injection) · `:200-274` (`_is_trusted`, IP-based). Contrast: `src/web/main.py:90-99` (already fixed there).

The runner trusts callers by IP, injects its own cert-tier `RUNNER_ENRICHED_TOKEN` for `_TIER1_PATHS`, and runs `allow_origins=["*"]`. Any web page, any origin, can make an unauthenticated cross-origin request, have the runner authenticate it Tier-1 upstream, and read the response.

**Verification I ran (live, production tailnet runner `:8001`):**
```
$ curl -H 'Origin: https://evil.example.com' http://127.0.0.1:8001/api/dispatch/api/v1/watchlist
  200 | access-control-allow-origin: *  | {"entries":[{"id":"perm-flight-ba293",…"identifier":"BA293","origin":"EGLL"…
$ curl http://127.0.0.1:8000/api/v1/tfr-enriched          # same path, no runner
  403 {"detail":"This endpoint requires tier tier1"}
$ curl -X OPTIONS -H 'Origin: evil' -H 'Access-Control-Request-Method: DELETE' \
       http://127.0.0.1:8001/api/chat/history
  200 | access-control-allow-methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
$ curl -H 'Origin: evil' http://127.0.0.1:8001/api/chat/history
  200 | access-control-allow-origin: * | {"messages":[{"role":"user","content":"Any Amtrak NEC delays…
```
The `403`-vs-`200` contrast is the finding: the runner manufactures the auth and wildcard CORS hands the result — the live EP watchlist and the operator's private dispatch chat — to an arbitrary attacker origin. Wildcard methods also approve drive-by `DELETE` (chat wipe) and `POST` (`/api/ask`).

**Honest mitigation:** HTTPS pages can't `fetch` an `http://` tailnet origin (mixed content) and Chromium PNA increasingly blocks public→private — but a plain-HTTP attacker page, an older/non-Chromium browser, an extension, or any non-browser tailnet client bypasses those. Rated High; the data class argues upward.

**Fix:** replace `allow_origins=["*"]` with the two-origin allowlist already used in `web/main.py:90-99`; narrow methods/headers.

---

### H-2 — High — Demo gate and signal sanitisation bypassable from either LAN

**Files:** `/etc/nginx/conf.d/dispatch-runner.example.com.conf` · `src/runner/main.py:191-197` (`_TRUSTED_NETS` includes all RFC1918) · `:264-272` (`_is_trusted` fallback) · firewalld `FedoraWorkstation` zone

**The box is dual-homed on two networks it does not own.** `ip addr`: `wld0 10.0.0.x/24` (a WiFi uplink) and `enu1 192.168.1.x/24`, plus a globally-routable IPv6 on `wld0`. nginx listens `0.0.0.0:80` and firewalld's `FedoraWorkstation` zone (bound to both LAN interfaces) allows `80/tcp`. The public demo vhost proxies to `:8005` passing `Host $host` with no check the request came via cloudflared, and `_is_trusted()` blanket-trusts all of RFC1918 — so a LAN peer's own `10.0.0.x`/`192.168.1.x` is already "trusted," no spoofing required.

**Verification I ran (through nginx:80 with a forged public Host):**
```
$ curl -H 'Host: dispatch-runner.example.com' http://127.0.0.1:80/api/demo/status
  {"demo_mode":true,"authenticated":true,"trusted_origin":true}
$ curl -H 'Host: dispatch-runner.example.com' http://127.0.0.1:80/api/whoami
  {"tailnet":true}
```
`trusted_origin:true` means (`src/runner/main.py:809`) ACARS/VDL2/HFDL callsign+text **sanitisation is off**, real ntfy is reachable rather than the demo stub, and `proxy_dispatch` injects the cert-tier service token upstream. Any other device on the (operator-uncontrolled) WiFi LAN or the wired LAN gets the un-password-gated, un-sanitised demo plus the ability to drive real alerting, with one forged-Host request. The 2026-08-25 `_CLOUDFLARE_FRONTED_HOSTNAMES` fix closed only the `CF-Connecting-IP` arm; this is the direct-IP/XFF arm.

**Fix (any one closes it):** drop `80/tcp` from the FedoraWorkstation zone (cloudflared reaches nginx on loopback; nothing else needs LAN:80); or `set_real_ip_from` / `X-Forwarded-For $remote_addr` on this vhost as the tailnet vhost already does; or remove `10.x.x.x/8` and `192.168.x.x/16` from `_TRUSTED_NETS`.

---

### H-3 — High — Manifest verification is blind to added files; no read-only containers; `sitecustomize.py` persistence

**Files:** `scripts/verify-manifest.sh:110-118` · all `Containerfile.*` (`ENV PYTHONPATH=/app/src`) · all `*.container` (no `ReadOnly=true`)

**Verification I ran.**
```
$ grep -cE 'ls-files|find |comm |diff ' scripts/verify-manifest.sh → 0   # never enumerates the FS
# scratch-dir proof that sha256sum -c ignores unlisted files:
$ sha256sum src/a.py > M ; echo evil > src/sitecustomize.py ; sha256sum -c M --quiet && echo PASS
  PASS   ← integrity check passes despite an unlisted new file
$ grep -l '^ReadOnly=true' ~/.config/containers/systemd/*.container | wc -l → 0   (of 64)
$ podman exec systemd-corporatetraveldc-poller python3 -c "import sys;print([p for p in sys.path if '/app' in p])"
  ['/app/src']     # and /app/src/sitecustomize.py does not exist → free to create
```
Python auto-imports `sitecustomize` from `sys.path` at interpreter startup. Dropping `/app/src/sitecustomize.py` in any container yields arbitrary code in every Python process there, permanently, while `verified-exec.sh` and the sweep keep reporting OK — because verification only walks the manifest's own list, never the filesystem. `sign-manifest.sh` *does* enumerate untracked files (`git ls-files --cached --others`) at sign time; the fix was applied to sign but never to verify.

**Fix:** have `verify-manifest.sh` re-run the sign-time enumeration and fail on any path not in the manifest; set `ReadOnly=true` (+ tmpfs) on the application containers.

---

### H-4 — High — `POST /api/ask` is unauthenticated internet-reachable LLM inference with caller-chosen model

**Files:** `src/runner/main.py:1430-1431` (no `Request` param → cannot gate), `:1457-1462` (caller-chosen model), `:1124-1153` (context fetch), `:1512/1526/1542` (persistence)

The handler takes only `req: AskRequest` — no `Request` — so it cannot call `_is_trusted()` or check `DEMO_MODE`, and no middleware covers `/api/ask`. It is reachable by anyone. **Verification I ran** (schema probe proves reachability without invoking the model or writing a row):
```
$ curl -X POST -H 'Content-Type: application/json' -d '{}' \
       -H 'Host: dispatch-runner…' -H 'CF-Connecting-IP: 203.0.113.55' http://127.0.0.1:8005/api/ask
  422 {"detail":[{"type":"missing","loc":["body","message"]…}]}   # route reached, no gate
$ curl -X POST … -d '{}' http://127.0.0.1:8001/api/ask → 422       # same on production runner
```
`https://dispatch-runner…/healthz` is `200` with no CF Access, so this is internet-reachable. Impact: (1) **availability** — every call schedules inference on a Pi 5 measured at load 26.39 / `llama-server` 143% / 65.5 °C during this audit (the repo's own thermal guard, Ollama governor, wedged-detector, and swap-alert are evidence this contention is a known operational problem); (2) **arbitrary model selection** via `req.model` or a `/model <name>` prefix → memory/swap exhaustion + model enumeration; (3) on `:8001` the streamed context is the operator's real live dispatch state, readable cross-origin when combined with H-1; (4) every exchange is persisted and reloaded as context, so an anonymous caller can prompt-inject what later sessions/demo viewers see, with no size bound.

**Fix:** give the handler a `Request` and apply the same gate its neighbours (`chat_history`, `put_user_config`) use; constrain `effective_model` to an allowlist; bound the chat table.

---

### H-5 — High — Nextcloud vault host internet-exposed: no CF Access, zero edge rate limiting, version disclosed

**Files:** `nginx/conf.d/cloud.example.com.conf`, `nginx/conf.d/dav.example.com.conf`, `cloudflared/config.yml`

**Verification I ran (external).** `https://dav…/` → `302` to the Nextcloud login flow (a real login page, not CF Access). `https://cloud…/status.php` → `200` `{"version":"33.0.6.2","versionstring":"33.0.6","productname":"Nextcloud",…}`. `grep -c limit_req` on both deployed vhosts → **0** (every other public vhost carries `limit_req zone=corporatetraveldc_lr burst=20`). This is the operator's second-brain vault — the same content the app gates behind `X-Board-Key` and a CUI/PII scrub gate. An attacker gets the exact patch level free and an unthrottled channel for credential-stuffing/WebDAV app-password guessing against a known-named account, with Nextcloud's own throttling the only remaining control. `client_max_body_size 10G` on both makes them the cheapest resource-exhaustion target on the box.

**Fix:** add `limit_req` to both (after C-1's re-keying); drop/blank the `status.php` version; consider CF Access on `dav.` with a service-token bypass for mobile clients.

---

### H-6 — High — Public-push scrub guard and pre-commit secret scanner are both bypassable

**Files:** `scripts/pre-push:44-64, 100-108` · `scripts/pre-commit:56`

The `public`-remote guard compares `$1`/`$2` against `git remote get-url public` by **exact string**. A subagent ran the actual hook on variants and I confirmed the match logic: `https://github.com/CorporateTravelDC/ctdi-dispatch` (no `.git`), the `git@github.com:…` SSH form, a trailing `/`, and a credentials-embedded URL all resolve to the same repo but **miss** the exact-string check — each would push the unscrubbed private tree (652-commit history) to the public repo. The `CTDI_PUSH_PUBLIC_INTERNAL=1` escape hatch is a plain env var that overrides even the named remote.

The pre-push/pre-commit credential scanner applies its placeholder exemptions as **whole-line `grep -v`** *after* the match, so any line also containing `<…>` (any HTML tag or `<placeholder>`), `example`, or `...` suppresses a real hit on that line — and the env-var fallback pattern misses any value containing `-`/`_` (i.e. most real tokens), indented or YAML-style assignments, and PEM/`xox`/`AKIA`/`AIza` shapes entirely.

**Fix:** normalize both URL sides (strip scheme/userinfo/trailing `/`/`.git`, canonicalize `git@`) and compare host+path; scope the escape hatch to also require the just-scrubbed commit sha; move scanner exemptions onto the matched substring and add PEM/`xox`/`AKIA`/`AIza`/high-entropy patterns.

---

### H-7 — High — `dumpvdl2` runs SELinux-unconfined with the full secret set

**Files:** `~/.config/containers/systemd/corporatetraveldc-dumpvdl2.container:14-18`

The quadlet sets `SecurityLabelDisable=true` and mounts the whole USB bus, and receives both env files. **Verification I ran:**
```
$ podman inspect corporatetraveldc-dumpvdl2 --format '{{.HostConfig.SecurityOpt}}' → [label=disable]
$ cat /proc/<dumpvdl2-pid>/attr/current  → unconfined_u:unconfined_r:spc_t:s0
$ cat /proc/<ultrafeeder-pid>/attr/current → system_u:system_r:container_t:s0:c640,c944
```
`spc_t` is the super-privileged-container domain — effectively no SELinux confinement and no per-container MCS category, so it is not even separated from other containers' labelled files — and it carries the full C-4 secret set. The sibling `ultrafeeder.container` documents removing exactly this pattern and claims "every SDR-touching container now agrees"; that claim is false — `dumpvdl2` was skipped, and `udev/99-rtlsdr-adsb.rules` already creates a scoped `/dev/rtl_sdr_acars` device the fix could use.

**Fix:** replace the bus mount + `SecurityLabelDisable` with `AddDevice=/dev/rtl_sdr_acars` + a `:ro` bus volume, matching ultrafeeder; and apply C-4's scoped env file.

---

### M-1 — Medium — `_is_trusted()` remains X-Forwarded-For-spoofable outside the one allowlisted hostname

`src/runner/main.py:200-274`, `:167-182`. When `CF-Connecting-IP` is absent or `Host` isn't the single allowlisted name, `_is_trusted()` falls through to `[request.client.host, X-Forwarded-For.split(",")[0]]`, and the public vhost forwards `X-Forwarded-For $proxy_add_x_forwarded_for` (appends client input). Live results on `/api/whoami`: with `CF-Connecting-IP` present + public Host → `tailnet:false` ✅ (the genuine internet path, which holds); with it absent, or with a bogus Host → **`tailnet:true`** ❌. The real internet path is safe because Cloudflare always sets `CF-Connecting-IP`; the residual (which overlaps H-2) is a latent trap — add a second fronted hostname or a proxy that omits that header and the fallback becomes internet-reachable. **Fix:** drop the XFF arm from the fallback; trust only `request.client.host` with `--forwarded-allow-ips` scoped to nginx; add a regression test.

### M-2 — Medium — NOPASSWD sudo grants convert code-exec-as-user into root destruction and thermal DoS

Live `sudo -n -l`: **`/usr/bin/dnf remove *`** (the `*` matches any args → `sudo dnf remove -y fail2ban firewalld audit selinux-policy openssh-server` wipes every control; RPM scriptlets run as root); **`/usr/bin/semanage port -a *`** (arbitrary SELinux port relabelling); **`systemctl mask argononed.service`** (masks the Pi 5 fan controller → persistent thermal DoS with hardware risk on a box at 65.5 °C); and unrestricted stop/kill of `ollama`/`ollama-governor`. Every container runs as host UID 1000, so this is one host-side code-exec (e.g. via H-4) from unprompted root. **Fix (operator's own sudoers):** replace the wildcards with explicit package/port lists; drop `mask` from the fan grant.

### M-3 — Medium — Fail-silent operations tooling

Live: 34 `~/.config/systemd/user/*.service`, only **2** with `OnFailure=` (the two most recently added). `corporatetraveldc-transport-pattern-digest.service` failed at 00:53 (~8h pre-audit) with `result 'timeout'` after 28 min / 828 MB — no notifier, found only by enumerating unit state. The templated `unit-failure-notify@.service` already exists and is wired to just those two units. On an alerting platform a monitoring component that can't report its own death is the highest-leverage single fix. **Fix:** apply `OnFailure=…@%n.service` via a drop-in to all `corporatetraveldc-*` services; investigate the digest timeout (consistent with H-4's contention).

### M-4 — Medium — Deployed config diverged from tracked/signed source (both directions)

Live diff `nginx/conf.d/` vs `/etc/nginx/conf.d/`: the deployed `00-rate-limit` file has an **extra** `corporatetraveldc_demo_login` zone absent from the tracked copy — and the tracked `dispatch-runner…conf` references that zone, so **a redeploy from the repo would make nginx fail to start**. Three vhosts are **live-only** (`000-default-catchall.conf`, `dispatch-runner.example.com.conf` — the only internet-open app hostname, and the H-2 vhost — and `www.example.com.conf`), so they are outside `MANIFEST.sha256` and invisible to a drift check that only walks tracked files. Same class in quadlets/units (`nextcloud-net.network` untracked but required by tracked quadlets; several live-only units). Positive: all ~70 quadlet files that exist in both places are byte-identical. **Fix:** bring the live-only files into the repo, re-sign, and add a reverse drift check (live files with no tracked counterpart).

### M-5 — Medium — Global in-process rate limiters are self-DoS levers

`src/web/main.py` — `_whoami_token_hits` (60/min), `_board_post_hits` (30/min), `_vault_research_hits` (30/min) are all **module-level, shared across all callers**. The board/vault ones run after their `X-Board-Key` check (correct — unauth callers can't drain them). `/api/v1/whoami-token` is unauthenticated by design and its limiter runs unconditionally: 60 requests in any window from anyone returns `429` to every subsequent caller — including the runner, which uses it to resolve operator identity for feed scoping. Verified anonymous reachability live (`200`, `{"tier":"tier0",…}`); budget **not** exhausted. **Fix:** key all three per-client/per-token.

### M-6 — Medium — Manifest rollback/replay unmitigated

`MANIFEST.sha256` is bare `sha256sum` output with no timestamp/version/counter, and `verify-manifest.sh` has no freshness check. Every historical manifest+`.asc` pair is committed and remains validly signed forever, so restoring an old pair alongside old file contents reverts arbitrary fixes and verifies clean. **Fix:** add a monotonic counter or ISO timestamp to the signed body; refuse anything older than last-seen.

### M-7 — Medium — `populate-secrets.sh` leaks secrets to the sudo log and corrupts some values

`scripts/populate-secrets.sh:53` `sudo sed -i "s|^${key}=.*|${key}=${val}|" …` — `sudo` logs the full `COMMAND=` argv to authpriv by default (no `!syslog`/logfile Default set), so every existing-key update writes the plaintext value into the sudo log, which `wheel` membership can read back. The `sed` replacement is also unescaped (`&`/`\`/`|` in a value corrupt the write), and there's no exit-status check so `[OK]` prints on failure. Journal retention is 5 days and no run falls in-window, so nothing is presently exposed; `/var/log/secure` (root-only) may hold older entries. **Fix:** `sudo tee` for both branches (value via stdin); check status before printing OK.

### M-8 — Medium — `Containerfile.web` ships `--forwarded-allow-ips=*` → audit-log IP forgery

`Containerfile.web` runs uvicorn with `--proxy-headers --forwarded-allow-ips=*` while `Containerfile.runner` was tightened to `127.0.0.1`. The web container is published on the tailnet IP, so any tailnet peer can set `X-Forwarded-For` and uvicorn rewrites `request.client.host`. This is **not** an auth bypass (`resolve_tier()` never consults client IP — verified) — but `remote_addr` is what the admin audit log persists (including C-9 denied-auth rows), so an attacker forges the attribution IP on every audit entry. **Fix:** `--forwarded-allow-ips=127.0.0.1` to match the runner.

---

### M-9 — Medium — Tag `v1.0.0` on `origin` still carries both leaked secret files

Detailed under **C-0**. `git ls-remote --tags origin` confirms the tag is published on the private remote; `git cat-file -e v1.0.0:dispatch-secrets.env` and `…:src/acars_watcher/secrets.env` both succeed. Every other ref has dropped them. Delete the tag locally and on `origin` — after rotation, not instead of it.

### M-10 — Medium — Real live infrastructure values published in `config/dispatch.env`

`config/dispatch.env` is byte-identical between `main` and `public/main`, and its values match the live production config: `SWIM_NMS_HOST` (the real FAA SWIM broker host:port), `NWWS_NICK` (the NWWS-OI account identifier — the *other* half of the C-0 credential pair, and a government account identifier in its own right), `NWWS_MUC`, `NTFY_URL`, `ULTRAFEEDER_URL`. Notably `TAILSCALE_DOMAIN_SUFFIX` *is* correctly sanitized in the same file, which proves the scrubber is being selective here rather than absent — these values were judged publishable. Given C-0, `NWWS_NICK` in particular should be reconsidered: publishing the account identifier alongside a historically-leaked password materially assists an attacker. Reconnaissance value otherwise (it names the exact broker endpoints an attacker would target with the C-0 credentials).

### Low

- **L-10 — `.gitignore` lacks generic secret-file coverage.** The two historically-leaked filenames are now correctly ignored (`**/dispatch-secrets.env`, `**/secrets.env`, `**/*demo-secrets.env` — all confirmed via `git check-ignore -v`). But `*.key`, `*.token`, `*.pem`, `*.env`, `.env`, `id_rsa*`, and `credentials.json` are **not** ignored — and `scripts/populate-secrets.sh` reads `~/.secrets/{name}.{token,key}`, exactly the uncovered patterns. Copying one into the tree for a test would leave it stageable by `git add -A`. No such file exists today (`git status --porcelain -uall` shows zero untracked files), so this is a latent footgun rather than a live exposure. Fix: add the patterns with explicit `!` un-ignores for the tracked `.example`/`.template` files.
- **L-1 — Tier-0 recon leakage.** `/healthz` (audit/token counts), `/api/v1/data-usage` (per-interface bandwidth), `/api/v1/demo/readiness` (DB size/retention) are Tier 0; all three sit behind CF Access on `dispatch.` (verified `302`), capping severity. `/api/v1/board/threads` is inside the board's CF-Access bypass and returns thread names/counts/timestamps to the internet (verified `200`), including for the `research` thread whose contents are correctly gated.
- **L-2 — Demo ADS-B unsanitised.** `src/runner/main.py:489-498` — `adsb_local`/`adsb_live` are outside `_should_sanitize_signals()`; verified 21 real aircraft with genuine ICAO hex returned to an untrusted-shaped demo request. Largely subsumed by C-2 but an independent gap in the demo's sanitisation contract.
- **L-3 — `mcp.example.com` internet-routed, no CF Access, backend dead.** Verified `502` (nginx, not a CF-Access redirect). Inert today; becomes an unauthenticated tool surface the moment the MCP bridge restarts. cloudflared comments flag it as live-and-undocumented.
- **L-4 — RingCentral `Validation-Token` echoed before auth** (`src/web/routes/webhooks.py`). CR/LF-rejected by the server, so a reflection/existence oracle only; the surrounding webhook design (constant-time compare, fail-closed 503) is good.
- **L-5 — Prefix-only token revocation.** `src/auth/auth.py:313-315` → `revoke_token(prefix)` uses `LIKE 'ctdc_<user>_%'`; no single-token revoke, so one compromised device token can't be revoked without dropping that user's whole set.
- **L-6 — World-readable secret files.** `~/.secrets/nwws-oi.token` and `~/.secrets/ntfy-fcm.token` are mode 644 (the other 19 are 600); `~/.secrets` and `$HOME` are 0700 so nothing reaches them cross-user today, but the NWWS-OI token is the one from the prior leak incident. Root cause `session-restore.sh:89-90` (echo-then-chmod under umask 0022).
- **L-7 — Base-image tag pinning.** All Containerfiles pin by tag (`python:3.13-slim`, `node:20-alpine`); SDR/quadlet images use `:latest`/`:main`; only `ntfy:v2.25.0` is version-pinned. `podman-auto-update.timer` is disabled, so the `AutoUpdate=registry` labels are inert — latent, not live.
- **L-8 — FlightAware feeder UUID in a tracked quadlet.** `corporatetraveldc-ultrafeeder.container:56` embeds the FR24/FlightAware feeder UUID (a credential + stable site identifier) as a literal; every *other* feeder credential in that file was moved to `dispatch-secrets.env`. The public mirror is clean (scrubber maps it to zeros) but the repo's own rule covers tracked private files too.
- **L-9 — Stale artefacts.** `cloud….conf.bak-20260807` left in `/etc/nginx/conf.d/` (not loaded); six `integrity-test`-tagged images (2 wk), `ingest:debug` (5 wk), `demo-api:latest` (3 wk) still resident — rollback hazards. Also: no Containerfile sets `USER` (all run as container-root → host UID 1000, which owns the repo, state dir, cloudflared creds, and GPG keyring); `build-images.sh` never builds `Containerfile.demo` (the only public-internet CTDI image), so a `src/demo/*` fix deployed via the documented procedure would silently not reach it.

---

## 4b. Ingest / poller / alerting-path findings

*Specialist-agent scope (~27k LOC across `src/ingest/`, `src/poller/`, `src/pusher/`, the watchers, and the LLM path). The Criticals and top Highs below were re-verified by me; figures corrected against my own measurements where they differed.*

### C-7 — Critical — Mark-and-sweep registry wipes: an empty parse deletes the whole table and reports success

**Files:** `src/common/db.py:3651-3658` (`opensky_registry_sweep_removed`) and `faa_registry_sweep_removed` · callers `src/poller/fetchers/opensky_registry.py:169-182`, `src/poller/fetchers/faa_registry.py:348-360`

Both sweeps are a bare `DELETE ... WHERE updated_at < ?` against a cutoff captured **before** the run, with **no empty-input guard**:
```
$ grep -n "def opensky_registry_sweep_removed" -A 8 src/common/db.py
3656:  "DELETE FROM opensky_aircraft_registry WHERE updated_at < ?", (cutoff_epoch,)
```
The caller's own comment claims the sweep is safe — *"Only sweeps if the registry import itself succeeded (guarded above by the try/except return) — a failed/partial download must never be allowed to look like mass deregistration."* **That guard only catches exceptions.** A 200-OK HTML maintenance page fed to `csv.DictReader`, or a structurally valid zip whose rows fail the `len(row) < 21` filter after an FAA column-layout change, yields **zero upserts and no exception** — so the sweep runs, every row predates the cutoff, and the table is emptied while the function returns `{"ok": True}`. OpenSky then writes the new `source_last_modified`, so the monthly freshness probe reports "source unchanged" forever: **the wipe never self-heals.**

**Live rows at risk (read-only query):** `opensky_aircraft_registry` = **519,991** (the only lookup path for non-US registrations), `faa_aircraft_registry` = **316,222**.

**The corrective pattern already exists 47 lines away and was never generalised.** `db.faa_upsert_ladd()` (`db.py:3007-3021`) refuses an empty replacement, with a comment describing precisely this failure mode — *"any upstream hiccup that made the caller's parse come back empty … silently wiped the entire privacy opt-out list to zero with no exception raised."* Corroborating evidence that this is not hypothetical: `faa_ladd_aircraft` currently holds **0 rows**, and the LADD endpoint now redirects to an FAA office page. The identical bug was found, fixed in one place, and left in two others.

**Fix:** add `if not <parsed>: return` (or a percentage-delta sanity bound) before both sweeps, mirroring `faa_upsert_ladd`. Add a regression test for each — neither sweep has one.

### C-8 — Critical — The ACARS watcher has been silently dead for ~40 hours and reports healthy

**Files:** `src/acars_watcher/acars_watcher.py:274-278` (empty-set fallback), `:528` (unconditional overwrite)

`get_watched_registrations()` swallows both the non-200 branch and the exception branch and **returns an empty set**; `main()` then overwrites the last known-good watchlist with it, so the membership test can never match. The container has held a bearer token in memory that the server has since revoked.

**Live verification:**
```
$ systemctl --user is-active corporatetraveldc-acars-watcher          → active
$ journalctl -u … --since -7d | grep -c 403                           → 477
  most recent 403: Aug 26 09:23  (minutes before this audit)
  last successful watchlist load: Aug 24 17:04  → ~40 hours dead
$ grep -cnE "mark_push_healthy|upsert_feed|heartbeat" acars_watcher.py → 0
```
*(Correction to the specialist agent's figure: it reported ~5 days; the journal shows the last good load at 2026-08-24 17:04, so ~40 hours. The failure is real; the duration was overstated.)*

Because the watcher writes **no heartbeat of any kind**, neither `freshness_audit` nor `feed_db_integrity_check` can see it. Meanwhile the upstream data is fine — `acarshub/messages.db` holds ~63k rows with timestamps seconds old. The data is arriving; nothing is reading it. Related: `dispatch-secrets.env` defines `ACARS_DISPATCH_ADMIN_TOKEN`, which **no code in the tree reads** — a scoped token was minted for this service and never wired up.

**Fix:** distinguish "empty watchlist" from "fetch failed" — retain the last known-good set on failure and alarm. Wire a heartbeat. Use the already-minted scoped token.

### C-9 — Critical — Prompt injection → attacker-authored push notification with an attacker-chosen tap target

**Files:** `src/poller/skills/osint_monitor.py:365-417` (prompt build), `:508-517` (`_push_item`), `:173/:197` (link handling)

18 of 22 enabled OSINT scopes query Google News RSS. **Attacker model: anyone who can get a page indexed by Google News** — no account, credential, or network position. Item `title`, `summary`, and `source_name` are concatenated into the prompt as bare `Field: value` lines, undelimited and unquoted, with the untrusted block *preceding* the instruction. The output then drives three things, one of which needs no LLM cooperation at all:
```python
title_text = f"[{score}][{tag}] {scope}: {item['title'][:70]}"   # raw attacker text
body       = f"{narrative}\n\n{item['url']}"
ntfy_send(..., click_url=item["url"])                            # attacker picks the tap target
```
`urldefrag()` is the only processing applied to the link and it validates **no scheme**. Verified:
```
'javascript:alert(1)#x'           -> 'javascript:alert(1)'
'data:text/html,<h1>x'            -> unchanged
'http://127.0.0.1:11434/api/tags' -> unchanged
```
`shared.ssrf_guard.is_safe_public_url` is applied to the *feed* URL but never to item links. **Both centralised sanitizers are no-ops here** — `sanitize_prompt_text` and `sanitize_llm_response` target malformed *code* (shebangs, backticks, `$()`, `eval(`) and malfunctioning *models* (persona echo, repetition loops); neither defends against a well-formed English sentence. The channel is live: 81 `osint_items` rows already have `pushed_at IS NOT NULL`.

Net effect: a credible priority-4/5 notification on the operator's phone with attacker-authored body, attacker-influenced title, and an attacker-chosen tap-through — delivered through the channel trained to carry real airspace and executive-protection alerts. **H-14** below is the same injected text laundered into the EP threat brief at a *lower* bar (score ≥ 4, `tags="shield,rotating_light"`, priority 4).

**Fix:** delimit and length-clamp untrusted fields in the prompt; validate `item['url']` scheme against `{http, https}` before it becomes `click_url`; treat LLM output destined for a notification body as untrusted text.

### High (ingest path)

- **H-8 — SWIM heartbeat proves the socket, not the data path.** `src/ingest/swim_client.py:655` swallows handler exceptions without calling `_stamp_down`; `:678-681` stamps healthy 30s later regardless. A 100% parse failure is indistinguishable from a quiet feed, so `failover.push_is_healthy()` never trips and REST fallback never engages. The backstop (`feed_db_integrity_check.FEED_TABLE_MAP`) states in its own source that **`push:fns` and `push:tfms` are unmapped**, so a parser break in NOTAMs or GDP/ground-stops is undetectable indefinitely. Observed live: `push:fns` suspended for bandwidth priority *and* the REST `notam` fallback `awaiting_credentials` — both NOTAM paths dead at once, `consecutive_failures=0` on both, no alarm possible.
- **H-9 — Squawk 7700 classified as Marine One.** Verified in two files: `fdps_parser.py:180` and `local_airspace.py:74` both declare `MARINE_ONE_SQUAWKS = frozenset({"7700", "5000", "5001"})`, while `local_airspace.py:75` declares `EMERGENCY_SQUAWKS = frozenset({"7700", "7500", "7600"})` — the sets overlap on 7700. An aircraft squawking the universal ICAO emergency code fires a priority-5 "POTUS MOVEMENT" alert; locally it fires *both* that and the emergency alert. `check_marine_one` has **no dedup** (one p5 push per message) even though `_FDPS_PROX_DEDUP` exists in the same file for the lower-priority proximity alert, and on one source the distance gate is skipped entirely while the body still hardcodes a "within 50nm of DCA" claim. Honest calibration: all 33 live `marine_one_local` rows to date were callsign-triggered, so this has not false-fired yet.
- **H-10 — The Marine One / squawk-7700 sender is the least hardened of three.** `local_airspace.py:110-127`: the `requests.post` has no `raise_for_status`, no status check, and the return value is unused — only a transport exception is logged. The next statement writes `ntfy_fired=1` unconditionally, so all 50 live local-airspace alert rows claim delivery that was never confirmed. The two sibling implementations (`shared/watchlist.py::_fire_ntfy_dual`, `common/ntfy_push.py::send`) both check status and retry; the path carrying emergency squawks does not.
- **H-11 — Retention is currently non-functional and the database is 23.3 GB.** `flight-cleanup` is registered without a timeout override so it inherits the 120s non-LLM default while archiving ~36 MB batches to WebDAV; the journal shows repeated `Skill flight-cleanup timed out` and zero completed batches. Live: `flight_events` = **840,271 rows**, DB file **23,256 MB**, `PRAGMA auto_vacuum` = **0** (space is never reclaimed even after deletes). `flight_events.raw_json` stores the full decoded FIXM XML per flight, uncapped. Compounding this, **H-14b**: the watchlist-protection filter that is supposed to exclude actively-watched flights from archival resolves to **0 of 13** watched identifiers (ICAO-vs-IATA mismatch, plus hex idents and military-style tails that match no pattern) — so once the timeout is fixed, actively-watched flights get archived and deleted.
- **H-12 — Fetchers report healthy after fetching nothing.** `db.upsert_feed(..., error=None)` resets both `error` and `consecutive_failures`, and `freshness_audit`'s verdict derives only from those. Reproduced across `notam.py`, `metar.py`, `nas.py`, `tfr.py`, `eurocontrol.py`: total upstream outage, empty body, or zero-child XML all yield `error=None, failures=0`. `eurocontrol.py:69-72` hardcodes `records = []` behind a `TODO: parse`, so it will report "OK — 0 records" forever once credentials land. No poller fetcher appears in the integrity-check map. Same shape in **H-13b** (`airport_fids`): DCA/IAD serve an unbounded stale cache on failure and report a fresh healthy fetch with an unchanged `payload_hash`.
- **H-13 — Spoofed ACARS is attacker-controlled alert content and cancels real monitoring.** ACARS/VDL2 is unauthenticated over-the-air. Beyond injecting raw `msg_text` into priority-4 alert bodies, `pusher/main.py:326-340` treats a spoofed ON/IN as authoritative "landed", bypassing ADS-B corroboration, and `:446-465` then calls `db.terminate_watchlist_session()` — **the operator stops monitoring a real in-flight aircraft.** Reachable from a cheap VHF transmitter, from the two upstream aggregators, or from any tailnet peer (UDP 5005 / TCP 9080 sit in firewalld's `trusted`/ACCEPT zone). Not LAN- or internet-reachable.
- **H-14 — Injected OSINT text is laundered into the EP threat brief at a lower bar.** `ep_advance_brief.py:773-825` pulls `min_score=4` items and inlines title+body undelimited, shipping at priority 4 with `tags="shield,rotating_light"` — framed as protective intelligence about the principal. Live corroboration that the score bar is not discriminating: current qualifying items include a "CRITICAL"-scored piece about a local celebrity and a HIGH-scored guide to sports-team mascots.

### Medium / Low (ingest path, condensed)

**Alerting correctness:** a sustained 30 kt wind fires a Pushover **Emergency siren every 30 seconds** (the hot-push branch `continue`s before the dedup the docstring promises — the identical bug the same file documents fixing for VIP TFRs one function away). ITWS alerts hardcode `expires_time=None`, have no time filter on read and no prune, so a severity-6 TORNADO ALERT stays queryable as current indefinitely; every ITWS hazard handler renders an unparseable or missing field as the *quiet* value ("unknown" published as "clear"). Ceiling 0 ft / visibility 0 SM — the worst weather possible — scores as clear via `(x or 9999)` truthiness, an exact inversion at the worst end of the scale; `VV###` (the standard fog ceiling report) parses to `None`. One unparseable ACARS timestamp defaults to `time.time()` and therefore always outranks every genuine message in the OOOI state machine. ~47% of real ACARS messages exceed the 200-char API limit and are dropped with only a log warning — and the priority-5 medical/smoke/mechanical categories are precisely the long ones.

**Observability:** `freshness_audit` writes a file nothing reads and, despite its docstring, never pushes to ntfy. Eight skills swallow every exception and exit 0, so a permanently broken skill never reaches `--failed` and the new `OnFailure=` notifier can never fire — two of them are watchdogs. Both feed-silence watchdog timers are disabled and the heartbeats they would consume are written every 30s into a dead end.

**Resource / availability:** no message-size cap anywhere in the ingest path — measured ~12.5 MB RSS and ~1.75 s CPU per MB of XML, superlinear, against container caps as low as 256 MB, so a single large message OOM-kills a container. Unbounded ACARS line buffer with no `MAX_LINE` guard behind a 256 MB cap, reachable by any tailnet peer. Blocking SQLite calls inside async event loops against a 23 GB file (**I hit `database is locked` on plain read-only queries three times during this audit**). A thundering herd at poller start causes real skill failures, and the poller has restarted 56 times in 7 days. 10+ tables have no prune path; `prune_wpc_discussions` is written but has **zero callers**.

**Injection / trust:** LLM-guessed domains become permanently subscribed feeds fetched with **no SSRF guard** — two such entries (`"discovered": true`) are already live in `user_rss_feeds.json`, and the guard exists three lines away in a sibling module. The CUI/PII scrub gate is applied only to the Ollama result, never to the raw-headline fallback that 8 skills write to the vault when Ollama is down — and Ollama-down is a routine state (the current live ops-brief is in deterministic-fallback mode). A single planted headline is a deterministic kill-switch on the daily second-brain rollup, silently blocking the weekly compile too, with no alert.

**Data integrity:** two concurrent Amtrak writers produce contradictory rows into one table ~3 minutes apart, both stamping healthy, despite a docstring asserting only one runs. Altitude 0 ft is written as NULL (truthiness). ETA timezone offsets are dropped rather than converted, a 5-hour error. A combined label-31 OOOI record always resolves to "out" — aircraft at the gate, platform reports pushback. The UDP path cannot parse the envelope the live router actually sends.

### Checked and clean (ingest path — negative results worth stating)

- **TLS is correct everywhere.** Zero hits for `CERT_NONE`, `check_hostname=False`, `verify=False`, or `_create_unverified_context` across the entire scope — I re-ran this sweep myself and confirmed zero matches. SWIM uses explicit certificate validation with a trust store; NWWS-OI inherits slixmpp defaults verified `CERT_REQUIRED` inside the running container. **Real FAA and NWS credentials are not MITM-exposed.**
- **XXE is not exploitable.** No `defusedxml`, but verified inside the actual ingest container: external entity → `ParseError: undefined entity`; external DTD not fetched; billion-laughs → `ParseError: limit on input amplification factor breached` (libexpat's built-in ~8 MiB amplification cap). This is a runtime-default dependency rather than an explicit control, and it stacks with the missing size cap above, but it is not an exploitable file-read or SSRF path today. *(This supersedes the preliminary concern I flagged in §8 of the earlier edition.)*
- **No injection sink anywhere in scope** — no `shell=True`, `os.system`, `eval`, `exec`, `pickle`, or unsafe `yaml.load`; the three `subprocess` uses are `create_subprocess_exec` with fixed argv; no zip extraction anywhere (no zipslip); all SQL parameterised.
- **No secret is hardcoded, logged, or placed in a URL** in this scope; every one of 41 outbound HTTP call sites has an explicit timeout; no ReDoS (every parser regex timed against adversarial input, all linear); UDP intake is bounded.
- **No parser can trigger a delete** — all six parser write paths are `INSERT … ON CONFLICT DO UPDATE`; the only reachable delete is time-scoped and guarded on empty input.

---

## 4c. `common/` and `shared/` module findings

*Final specialist pass over `src/common/`, `src/shared/`, `src/audit/`, `src/ctdc_token/`. Two of its claims were **corrected downward** by my own verification; both corrections are recorded below, because a calibration error in an audit is itself a finding.*

### M-11 — Medium — Unauthenticated-within-app route drains a billable third-party API (scope corrected)

**Files:** `src/web/routes/fids.py:61-93` (zero auth dependencies — `grep -c "Depends|require_tier|require_admin"` → **0**) · `src/common/flight_resolver.py:303-370` · `src/ingest/parsers/fdps_parser.py:800`

`GET /api/v1/fids/{airport}/arrivals` is Tier 0 and layers three sources: SWIM → MWAA website → FlightAware AeroAPI. Two of the three are unavailable for one airport:
- The SWIM tier is dead for **all** airports because `fdps_parser.py:800` writes `arrival_time=None` unconditionally on every FDPS event.
- The website tier is DCA/IAD only — `flight_resolver.py:368` states plainly that AeroAPI *"is BWI's only data path — MWAA doesn't operate it."*

So every `GET /api/v1/fids/BWI/arrivals` becomes a billable AeroAPI call. `FLIGHTAWARE_API_KEY` is confirmed present in the live secrets file, and a grep of `flight_resolver.py` for `cache|ttl|throttle|rate` returns **nothing** — the call is uncached and unthrottled. A caller looping this runs up real billing and exhausts the quota that genuine dispatch lookups depend on.

**Correction to the specialist agent's rating.** It rated this High on the reasoning that *"anyone reaching the web container's Tier-0 surface"* can trigger it. That conflates "Tier 0 in the application" with "reachable from the internet" — they are not the same here, because Cloudflare Access sits in front. I verified externally, using an **invalid** airport code so the request returns before any billable lookup:
```
$ curl https://dispatch.example.com/api/v1/fids/ZZZZ/arrivals   → 302  <CF Access redirect>
$ curl -H 'X-CTDI-Public: 1' http://127.0.0.1:8000/api/v1/fids/ZZZZ/arrivals
    400 {"detail":"airport must be one of: DCA, IAD, BWI"}
```
The path is CF-Access gated, and the surface is bounded to three airports with the route validating before any lookup. The realistic attacker is therefore a **CF-Access-authenticated user, a tailnet peer, or anything with code execution in a container that can reach `:8000`** — not the open internet. **Rated Medium.** *(I deliberately did not exercise the BWI path: it spends the operator's money.)*

**Fix:** cache AeroAPI responses with a TTL, add a per-caller throttle, and gate the AeroAPI tier behind Tier 1+ rather than Tier 0.

### M-12 — Medium — Model-hallucinated entities drive a live, unguarded network-fetch-and-persist chain

**Files:** `src/common/entity_tracking.py:278-331` (`_guess_domain`), `:453-481` (`_verify_feed`), `:484-500`, `:521-545`

Attacker-influenced RSS text → `extract_entities()` → `_guess_domain()` embeds the entity name in a second prompt and accepts the reply as a hostname (only filter: `^[a-z0-9.\-]+\.[a-z]{2,}$`, which admits `metadata.google.internal`, `host.containers.internal`, `router.lan`) → `_verify_feed()` does a bare `requests.get()` across 10 path candidates with **no `is_safe_public_url()`** → `_add_discovered_feed()` persists the result permanently.

**This is firing in production today.** I verified the live state file directly:
```
$ python3 … /var/lib/corporatetraveldc/rss_entity_tracker.json
  size: 1.63 MB | total entity records across all 6 categories: 828
  placeholder-named records ('entityname' — the prompt's own format example, echoed back by the model):
    advanced_air_mobility: 75   concierge_luxury_travel: 84   trains_yachts: 86
    gig_economy: 50             aviation: 53                  executive_protection: 50
                                                              → 398 mentions total
$ python3 … /var/lib/corporatetraveldc/user_rss_feeds.json
  total feeds: 3 | auto-discovered: 2
    robbreport.com/feed/    | created_by: entity_tracking (auto)
    fireapparatus.com/feed/ | created_by: entity_tracking (auto)
```
`entityname` appearing in **all six** categories is direct evidence that raw model output reaches persistent storage with no validation — the model echoed the prompt's placeholder and the pipeline stored it 398 times. The two `"discovered": true` feeds confirm the fetch-and-persist half of the chain is live, not latent. The state file is append-only and growing ~170 KB/day.

*(Self-correction: my first verification pass reported "6 entities, no placeholder found" — I had counted only the six top-level category keys. Re-inspecting the nested structure confirmed the specialist agent's figures exactly. Recording this because a too-quick negative is a worse audit failure than a false positive.)*

**Fix:** apply `is_safe_public_url()` in `_verify_feed()` (the guard exists and is used in `osint_monitor.py`); reject entity names matching the prompt's own placeholder vocabulary; bound the state file.

### M-13 — Medium — `feed_resolve.resolve_source` SSRF: no guard, redirects followed, Tier 1

`src/shared/feed_resolve.py:112,170` — both `client.get(...)` calls use `follow_redirects=True` with **no `ssrf_guard`** (verified: `grep -n "ssrf_guard|is_safe_public_url" src/shared/feed_resolve.py` → no matches, while `follow_redirects=True` appears twice). Reached from `runner/main.py:2478` (`POST /api/rss/resolve-source`) at Tier 1 — not admin. Contrast `rss_custom()` in the same file, which was correctly hardened with the guard *and* `follow_redirects=False`; this sibling path was missed. Also: `_is_youtube()` uses substring matching (`"youtube.com" in host`), so `youtube.com.attacker.tld` takes the "trusted" branch — no privilege gain (both branches fetch) but the check does not do what it reads as.

### M-14 — Medium — Signal-sanitisation control silently exempts the topic the operator actually watches

`src/shared/sector_coalesce.py:563-568` sanitises `title` and `detail` before firing, but passes `dispatch` — the body posted to the shared `"dispatch"` ntfy topic — through **unsanitised**. Enabling "mask tail numbers / hex" on a zone topic therefore still leaks those identifiers on the topic the operator's phone subscribes to, and on anything mirrored into a demo. Currently latent (no sanitise flags set live), but the control does not do what its docstring promises.

### Low (this pass)

- `src/common/ollama_lock.py:89-96` — `HOT_MARKER` has no PID-liveness check, unlike the sibling `report-waiters/` mechanism which correctly self-heals via `os.kill(pid, 0)`. A SIGKILL'd hot-priority skill leaves the marker forever, permanently starving `priority="report"` callers. Given the 33 SIGKILLs in 7 days recorded in §4b, this is closer to live than latent. No stray marker on disk right now.
- `src/shared/guardrails.py:66-79` — `mutation_gate()` persists the full request URL to `audit_log` with no redaction; a caller passing `?api_key=...` would leak it for 90 days. No live callers yet — cheap to fix before the first one lands. (Note `auth.py` already has `_redact_audit_url()` for exactly this; it was not reused here.)
- `src/shared/watchlist.py:267-288,668` — watchlist identifiers are upper-cased and space-stripped but never URL-encoded before interpolation into third-party API paths; path-injection confined to those third parties, and the input is admin-gated. Fix is `quote(ident, safe="")`.
- `src/common/push_dedup.py:138-155` — `should_push()` and `record()` are separate calls outside the otherwise-correct `flock`, leaving a narrow cross-container double-fire window the module's own docstring says it was built to close.
- `sector_coalesce._save_silence_state()` — non-atomic truncate-then-write, the same pattern already flagged for `rss_catalog.py`, while `push_dedup._merge_write()` and `entity_tracking.save_state()` in the same codebase are textbook atomic implementations that were never propagated.

### Checked and clean (this pass)

**No SQL injection anywhere in scope** — every dynamic-SQL f-string traced to module-level constants, never caller data. Zero `subprocess`, `shell=True`, or `eval`. MD5 traced to 18 call sites, all dedup/idempotency, **never security-bearing**. `fids.py` and `sectors.py` correctly use sync `def` so blocking calls get threadpooled by Starlette — which makes the async watchlist router's blocking call the outlier rather than the norm.

---

## 5. What is genuinely well-built

Recorded explicitly for calibration — a finding list read alone would misrepresent this system.

- **Cloudflare Access is real, not assumed** (verified `302` on four hostnames), and several code comments carefully distinguish "gated" from "assumed gated."
- **Firewalld is properly hardened** — stripping the stock `1025-65535` blanket-open from the `FedoraWorkstation` zone is the single most consequential control on the box; it keeps ~eight `0.0.0.0`-bound services off two untrusted LANs. LAN surface is exactly `22/tcp` + `80/tcp`.
- **Secret scoping is genuinely least-privilege where it was applied** — the internet-facing `runner-demo` carries one secret-shaped var; the tailnet `runner` carries 43. The demo/feeder split is the right pattern (C-4 is that it wasn't finished).
- **ntfy is locked down** (`deny-all`; anonymous topic reads `403`).
- **Tier enforcement holds under probing** (admin/T1/T2/board-research `403`, vault `401`, `openapi.json`+`/docs` `404`).
- **Board credential design is strong** — 192/240-bit CSPRNG values, stored hashed, atomic-conditional-`UPDATE` consumption closing the double-mint race, `compare_digest`, and an in-code footgun warning on the scope-blind validity check.
- **SSRF guard works** (loopback + link-local rejected, redirects disabled on the unauth preview path).
- **Traversal defences are correct** (multi-round percent-decode; `realpath`+`os.sep` prefix on the SPA handler).
- **Auditing is real** (denied attempts recorded, sensitive keys/URL userinfo redacted; 2,135 rows in 24h — active, not merely wired).
- **Demo credential handling is sound** (PBKDF2-HMAC-SHA256, 200k iters, per-profile salt, `compare_digest`, plaintext returned once).
- **Web-layer CORS was correctly fixed** to an explicit allowlist (which is exactly why the runner's `["*"]` stands out).
- **Integrity chain good parts:** isolated per-run `GNUPGHOME`, atomic sign (temp+rename, single trap), `PYTHONDONTWRITEBYTECODE=1` in all 7 Containerfiles, complete manifest coverage of the git-visible tree, correct `$?`-propagation in `verified-exec.sh`, public history genuinely orphaned from private (no common ancestor), OOXML scrubbing works, 743 public plain-text blobs scanned clean, `board-mint-nonce.py`/`sudo-approval-gate.sh` fail closed. The *architecture* is sound; C-3/C-5/H-3/C-6 are about the key, the config permission, the missing enumeration, and the encoding/metadata channels — not the design.
- **fail2ban integrity chaining** — ban actions verify the target script from the action config before executing, on the reasoning that a script can't neutralise a check outside itself. Genuinely thoughtful (C-5 is that the verifier it calls has its own flaw).
- **No private key material has ever been committed.** A full object-store sweep (3,086 blobs / 67 MB, all refs) found zero PEM/SSH/PGP private keys and zero cloud-provider or SaaS API keys of any recognised format. Every secrets template is genuinely 100% placeholders — no template shipped a real value, which is a common and damaging mistake this repo avoided.
- **The public-mirror orphan-lineage design works.** `public/main` shares no ancestry with `origin/main`, so private history is not merely filtered but structurally absent. The current mirror is clean. C-0 is residue from *before* that design was in place, and C-6 is about three channels the content scrubber never inspects — neither undermines the architecture.
- **A real rotation was performed after the original leak.** Five of the leaked secrets (`NWWS_PASSWORD`, `NTFY_TOKEN`, `DISPATCH_ADMIN_TOKEN`, and both Jumpseat keys) verify as changed, as do both values in the leaked `acars_watcher/secrets.env`. The response was genuine; the SWIM set was an omission, not neglect.
- **The codebase argues with itself in writing** — many comments record an assumption, the live test that disproved it, and the correction. That habit is why the application layer is in the shape it is.

---

## 6. Live system state (independently observed)

Observed directly against the running system on 2026-08-26, ~08:50–09:15 EDT.

**Host.** Raspberry Pi 5, Fedora, kernel `6.18.36-1.rpi5`. Uptime 7d 11h. **Load 26.39 / 22.07 / 15.11** on 4 cores. SoC 65.5 °C. Root `nvme0n1p2` 238 GB, 51% used. Top CPU: `llama-server` 143%. **Dual-homed** on `wld0 10.0.0.x/24` (WiFi) and `enu1 192.168.1.x/24`, plus a globally-routable IPv6 on `wld0`.

**Containers.** 34 running (rootless Podman). CTDI app images built 4–8h prior — current with `HEAD` (`b894b3d`). Also resident: six `integrity-test` images (2wk), `ingest:debug` (5wk), `demo-api:latest` (3wk). No container is read-only; all run as host UID 1000; no `--privileged`/`--cap-add` anywhere.

**Network binding (`ss -tlnp`).** `0.0.0.0`: `:80 :443 :22 :53 :8080 :3000 :2586 :8754 :9080 :30005 :8091 :5005/udp`. Tailnet-IP-only: `:8000 :8001 :8004 :8005 :11434 :3001 :1025 :8085`. Loopback-only: `:8090 :9081 :8002 :30003 :30053 :5335 :20241`. Dispatch app ports are correctly off the LAN; the ADS-B/ntfy/OpenWebUI family on `0.0.0.0` is the LAN surface, and `adsb`/`acars` are additionally tunnelled to the internet (C-2). Firewalld keeps LAN exposure to `22`+`80`.

**Internet-facing hostname posture (verified externally):**

| Hostname | Result |
|---|---|
| `dispatch.` / `ollama.` / `openwebui.` / `pihole.` | CF Access `302` — gated ✅ (dispatch's `/api/v1/board*` + `/vault/research*` bypass then enforce app-layer auth correctly) |
| `ntfy.` | `200` open, ntfy itself `deny-all`; anonymous reads `403` ✅ |
| `dispatch-runner.` | **`200`, no CF Access** — intentional demo, and the trigger surface for C-1/H-2/H-4 |
| `adsb.` / `acars.` | **`200`, no CF Access, no auth** — C-2 |
| `cloud.` | **`200`, no CF Access, no rate limit**, version disclosed — H-5 |
| `dav.` | **`302` Nextcloud login, no CF Access, no rate limit** — H-5 |
| `mcp.` | **`502`, no CF Access** — latent, L-3 |

**Integrity chain (live).** `MANIFEST.sha256` + detached `.asc` present, mode 0600. Signature verifies clean. **But** the signing fingerprint is the passphrase-less agent key (`…19390B37`); `security/signing.env` is 0644 and user-writable; `verify-manifest.sh` never enumerates the filesystem; the public mirror carries real-identity `.gpg` files, filenames, and commit authors. (C-3/C-5/H-3/C-6.)

**systemd.** 128 `corporatetraveldc-*` units loaded; 34 user `.service` files, 2 with `OnFailure=`. Two failed at audit time (`docs-drift-weekly`, `transport-pattern-digest` — the latter unreported, M-3). Timers firing on a ~2-min cadence.

**Application behaviour.** Board `coord` thread: 21 real messages, anonymously internet-readable by design; `research`: 6, correctly `403` to anonymous. `audit_log`: 2,135 rows/24h. 5 active auth tokens. Demo archive: 44,742 snapshots, 2,687 MB, 364-day retention.

**Git.** Two remotes: `origin` (private, 639 commits) and `public` (`github.com/CorporateTravelDC/ctdi-dispatch`). Current `public/main` = `e66dd46`, a **4-commit orphan lineage** with no common ancestor with `origin/main` — the isolation design works, and the current mirror is clean of `dispatch-secrets.env`. However the reflog shows `public/main@{4} = 0d4f677`, a **former 531-commit public HEAD force-replaced on 2026-08-24 18:51**, 91 of whose commits carried the populated secrets file (C-0). Superseded public HEADs `fccf34a`, `9cd9689`, `68fa63e`, `8765e9d`, `a053769`, `e02c134`, `0d4f677` all still exist locally. `refs/tags/v1.0.0` is published on `origin` and its tree still contains both secret files (M-9). `HEAD` `b894b3d`.

**Object-store sweep (3,086 blobs / 67 MB, all refs).** Zero PEM/SSH/PGP **private** keys anywhere — the only PGP material is 1,304 signatures and 6 public key blocks, and all three root `.gpg` files plus `security/*.asc` were confirmed by `gpg --list-packets` packet type to be public keys/signatures, not private. Zero AWS, GitHub (`ghp_`/`github_pat_`), Anthropic, OpenAI, Slack, Google, GitLab, npm, SendGrid, Stripe, or Twilio keys; zero JWTs. The four Basic-auth-URL matches are all `@example.com`-style documentation placeholders. `dispatch-secrets.env.template` (61 keys), `dispatch-secrets.env.example`, `config/demo-secrets.env.example`, and `signing.env.example` are 100% placeholders — verified, no template shipped a real value. Git config is clean: `gh auth git-credential` helper (no plaintext store), no credentials embedded in either remote URL, no `hooksPath` override, no `push.default = matching`. Working tree has zero untracked files. Both secret stores sit outside the repo (`~/.secrets` 0700, `/etc/corporatetraveldc` 0750).

---

## 7. Prioritised remediation

| Priority | Action | Finding |
|---|---|---|
| **0 — NOW** | **Rotate the FAA SWIM NEMS password and re-issue the NWWS-OI account.** One pair covers all six feeds. Then ask GitHub Support to purge unreachable objects on the public repo (or delete/recreate it), and delete tag `v1.0.0`. | **C-0**, M-9 |
| **0b — today** | **Currently broken in production:** restore the ACARS watcher (revoked token → empty watchlist → zero alerts for ~40h, reporting healthy) and add an empty-input guard to both registry sweeps before the next OpenSky/FAA poll. | C-8, C-7 |
| **1 — today** | Passphrase-protect or relocate the agent signing key; nothing else in the chain is meaningful until a local shell can't mint valid signatures. | C-3 |
| **2 — today** | `chmod 0600 security/signing.env`, move its `source` after verification (or switch to `read_env_var`), and fix the same pattern in the six other root-run scripts. | C-5 |
| **3 — today** | Confirm `jail.local` `ignoreip`; if absent, add loopback + private ranges to `nginx-limit-req…conf`, re-key the limiter to the real client, and drop the stack-wide lockdown action from this jail. | C-1 |
| **4 — today** | Attach Cloudflare Access to `adsb.`/`acars.` (or remove the routes). Treat the receiver coordinates as disclosed. | C-2 |
| **5 — this week** | Per-container scoped env files for the nine feeder containers; then rotate the CF/Tailscale/SWIM/NWWS/signing-passphrase secrets. | C-4 |
| **6 — this week** | `.gpg`/`.asc` + filename + commit-identity scrubbing; force-push a corrected public mirror. | C-6 |
| **7 — this week** | Runner CORS → explicit allowlist. | H-1 |
| **8 — this week** | Drop LAN `80/tcp` (and/or narrow `_TRUSTED_NETS`, and/or `X-Forwarded-For $remote_addr` on the demo vhost). | H-2 |
| **9 — this week** | `verify-manifest.sh` enumerate-and-fail on unlisted files; set `ReadOnly=true` on app containers. | H-3 |
| **10 — this week** | Gate `POST /api/ask`; allowlist the model; bound the chat table. | H-4 |
| **11** | `limit_req` + version suppression on the Nextcloud vhosts. | H-5 |
| **12** | Normalize the pre-push URL match; move scanner exemptions off whole lines; scope the escape hatch. | H-6 |
| **13** | `dumpvdl2` scoped device + SELinux confinement + scoped env. | H-7 |
| **14** | Bring live-only nginx/quadlet/unit files into the repo, re-sign, add reverse drift check; add the demo-login zone to the tracked rate-limit file; add `demo` to `build-images.sh`. | M-4 |
| **15** | `OnFailure=` drop-in across all 34 user services; investigate the digest timeout. | M-3 |
| **16** | Remove XFF fallback in `_is_trusted()`; `--forwarded-allow-ips=127.0.0.1` in `Containerfile.web`; `sudo tee` in `populate-secrets.sh`; manifest freshness counter; per-caller rate limiters. | M-1, M-8, M-7, M-6, M-5 |
| **17** | Narrow the `dnf remove *` / `semanage port -a *` / fan-mask sudoers grants. *(Operator must edit directly.)* | M-2 |
| **18** | **Alerting-path correctness (§4b):** remove `7700` from `MARINE_ONE_SQUAWKS` in both files and add dedup to `check_marine_one`; add `raise_for_status` + retry to `local_airspace._fire_ntfy` and stop writing `ntfy_fired=1` unconditionally; give `flight-cleanup` an LLM-class timeout and fix the ICAO/IATA watchlist-protection mismatch **before** re-enabling it; validate `item['url']` scheme before it becomes `click_url`; delimit untrusted text in the OSINT and EP-brief prompts. | H-9, H-10, H-11, C-9, H-14 |
| **19** | **Observability (§4b):** wire `freshness_audit` to actually push; make the 8 exception-swallowing skills exit non-zero; re-enable the two feed-silence watchdogs; add heartbeats to the ACARS watcher and a data-path (not socket) health signal to SWIM; map `push:fns`/`push:tfms` in the integrity check. | C-8, H-8, M-11+ |
| **20** | **Capacity (§4b):** message-size cap in the ingest path; `MAX_LINE` on the ACARS buffer; per-table prune coverage; wire `prune_wpc_discussions`; plan a `VACUUM`/`auto_vacuum` strategy for the 23.3 GB database. | H-11, M-11+ |
| **21** | Low-severity tail. | L-1 … L-10 |

---

## 8. Coverage and known gaps

**All five specialist workstreams have now reported.** Covered and reported here: the auth/tier model; the web, runner, and demo request paths; the network edge (nginx tracked vs deployed, cloudflared ingress, Cloudflare Access posture, firewalld, fail2ban); container/quadlet/systemd deployment and tracked-vs-live drift; the signed-manifest integrity chain end to end; `scripts/` including hooks, signing, secrets handling, and public-mirror scrubbing; full git-history secret archaeology across both remotes and all refs; the ingest/poller/pusher/watcher/LLM path (§4b); and `common/`/`shared/`/`audit/`/`ctdc_token` (§4c).

**Residual gaps, stated honestly:**
- **`db.py` was not swept exhaustively by me.** Two independent specialist passes each traced every dynamic-SQL f-string in their scope to module-level constants and found no caller-controlled interpolation, and I verified the auth-critical queries myself (`lookup_token`, `board_consume_nonce`, `board_token_valid`, the registry sweeps). A line-by-line review of all ~5,900 lines was not performed.
- **`src/second_brain/` had no dedicated deep-dive.** Its externally-reachable surface *was* covered from the request side — the vault-research routes, `_vault_path_is_safe`'s multi-round decoding, the board thread gating, and the knowledge-graph endpoints are all in §4. Internal module behaviour (the WebDAV client's own path handling, `scrub_gate` coverage completeness, the semantic/knowledge-graph builders) was not separately audited.
- **Token CLI** (`src/ctdc_token/`): I verified the generation and validation path via `auth.py` and `db.lookup_token` — 32 chars from a 36-symbol CSPRNG alphabet (~165 bits), SHA-256 at rest (unsalted, which is fine at that entropy), and `revoked_at IS NULL AND (expires_at IS NULL OR expires_at > unixepoch())` enforced at read. The CLI's own admin surface was not exercised. Known weakness reported as L-5 (prefix-scoped revocation only).

**A note on calibration.** Two specialist claims were corrected downward against my own verification (§4c M-11's severity — the route is behind Cloudflare Access, not internet-facing; and §4b C-8's duration — ~40 hours, not five days), and one of my own quick negatives was wrong and had to be reversed (§4c M-12 — I initially counted only top-level keys and reported "no placeholder found"; the nested structure confirmed all 398 mentions). Every Critical and High in this report was re-verified by me against the live system before publication. Findings I could not verify safely are labelled as such rather than asserted.
