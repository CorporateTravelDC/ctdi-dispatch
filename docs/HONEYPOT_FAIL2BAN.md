# Honeypot paths + fail2ban auto-ban

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.
`fail2ban-client status`, `/var/log/fail2ban.log`, `/var/log/nginx/honeypot.log`
and the audit log are root-only and were not read for this revision, so
current ban counts and the last successful Cloudflare-edge ban are
[UNVERIFIED].

A tripwire across the nginx-fronted stack, aimed at two kinds of client:

- **(a) AI crawlers/agents that ignore directives.** `robots.txt` and
  `llm.txt` declare decoy paths `Disallow`; fetching one means the directives
  were ignored.
- **(b) Vulnerability scanners.** `wp-admin`, `wp-login.php`, `.env`, `.git`,
  `phpMyAdmin`, `xmlrpc.php`, `/vendor/`, `/cgi-bin/`, `actuator`, etc. --
  paths this stack never serves.

## Mechanism

1. A trap snippet defines a `location` per trap path; a hit is logged to
   `/var/log/nginx/honeypot.log` with the real client IP and dropped with
   `return 444`.
2. `nginx/conf.d/00-honeypot.conf` defines the `honeypot` log format and a
   `map` that takes `CF-Connecting-IP` behind the tunnel, else the peer.
3. fail2ban jail `nginx-honeypot-corporatetraveldc` bans on the **first** hit
   (`maxretry=1`, `findtime=3600`, `bantime=604800` = 1 week, `port=http,https`,
   `ignoreip = 127.0.0.1/8 ::1 100.64.0.0/10 10.x.x.x/24`) with two actions:
   the local firewalld ban (`%(action_)s`) **and**
   `cloudflare-token-corporatetraveldc`, which creates a Cloudflare IP Access
   Rule so tunnel-routed traffic is blocked at the edge too.

## Files

| File | Tracked in | Role |
|---|---|---|
| `nginx/conf.d/00-honeypot.conf` | this repo | log format + real-IP map |
| `nginx/snippets/honeypot.conf` | this repo | trap locations + `robots.txt`/`llm.txt` |
| `nginx/snippets/honeypot-website.conf` | the website repo (`csexecutiveservices-website`) | the `www` variant: same traps, does not override the site's real `robots.txt`/`llm.txt` |
| `fail2ban/filter.d/nginx-honeypot.conf` | this repo | extracts `<HOST>` |
| `fail2ban/jail.d/nginx-honeypot-corporatetraveldc.conf` | this repo | the jail above |
| `fail2ban/action.d/cloudflare-token-corporatetraveldc.conf` | this repo | edge ban action |
| `fail2ban/jail.d/nginx-limit-req-corporatetraveldc.conf` | this repo | rate-limit jail (below) |
| `fail2ban/action.d/corporatetraveldc-lockdown.conf` | this repo | installed but **used by no jail** since the limit-req fix |
| `/etc/fail2ban/jail.local` | live only (root 0600) | `[DEFAULT] cfzone = <zone id>` only |
| `/etc/corporatetraveldc/cf-honeypot.token` | live only (root:root 0600) | the zone-scoped Cloudflare token |

Live copies of the tracked nginx files (`00-honeypot.conf`,
`snippets/honeypot.conf`, and the dispatch/cloud/dav/agents vhosts) are
byte-identical to HEAD (`cmp`, 2026-10-06), and the live
`honeypot-website.conf` is identical to the website repo's copy.

## Vhost coverage (live `/etc/nginx/conf.d`, 2026-10-06)

| Vhost | Trap include |
|---|---|
| `dispatch.example.com` | `snippets/honeypot.conf` |
| `cloud.example.com` | `snippets/honeypot.conf` |
| `dav.example.com` | `snippets/honeypot.conf` |
| `agents.example.com` | `snippets/honeypot.conf` |
| `invite.executivestandard.example.com` | `snippets/honeypot.conf` |
| `www.example.com` (+ apex, `wrangler.`) | `snippets/honeypot-website.conf` |
| `members.executivestandard.…`, `dispatch-runner.…`, `acars.…`, `adsb.…`, `mcp.…`, `ntfy.…`, `openwebui.…`, `pihole.…`, `tailscale-dispatch-runner.conf` | none |
| `000-default-catchall.conf` (`listen 80 default_server`, `return 444`) | none |

**Open gap:** unmatched-`Host` and raw-IP probes hit the catch-all and are
dropped (444) but never logged, so never banned. The catch-all file is
live-only. Same for the untrapped vhosts. Exposure is missed bans, not served
content.

**Untracked live vhosts:** `acars.`, `adsb.` and `dispatch-runner.` exist in
`/etc/nginx/conf.d/` but in no repository checked (this repo, the website
repo, the Executive Standard repo). A rebuild from repos would lose them.

## Deployment (root, never hand-copied)

fail2ban runs actions as root. Since 2026-10-04 the tracked fail2ban files
are installed only by `sudo scripts/install-root-copies.sh`, which verifies
the checkout against the signed manifest, hash-checks each file against its
manifest entry, installs into `/etc/fail2ban`, runs `fail2ban-client -t` and
reloads. The actions never run checkout scripts: each one calls
`/usr/local/libexec/ctdc/installed-check.sh "<repo path>"` (sha256 of the
installed copy vs the root-owned `.ctdc-installed`) and only then the
installed copy:

```
actionban = /usr/local/libexec/ctdc/installed-check.sh "scripts/cf-honeypot-ban.sh" && CFZONE="<cfzone>" CFTOKEN_FILE=/etc/corporatetraveldc/cf-honeypot.token /usr/local/libexec/ctdc/cf-honeypot-ban.sh ban "<ip>" "<cftarget>"
```

The installed copies in `/usr/local/libexec/ctdc/` (`cf-honeypot-ban.sh`,
`cf-honeypot-notes.sh`, `lockdown.sh`, `restore-network.sh`,
`installed-check.sh`) and the live `/etc/fail2ban/{action.d,jail.d}` files
were all written 2026-10-06 17:43Z. `fail2ban.service` is active since
2026-10-06 10:17Z.

nginx files are still copied by hand (`sudo cp` + `sudo nginx -t && sudo
systemctl reload nginx`). A change to a `limit_req_zone` key needs a restart,
not a reload (2026-10-05: a reload silently kept the old zone).

## The Cloudflare-edge action

`cf-honeypot-ban.sh` creates (or on unban deletes) a zone IP Access Rule.
Notes are classified and timestamped by `cf-honeypot-notes.sh`:

```
honeypot | 2026-08-09T19:30:57Z | ai-crawler-ignored-directive (ai-crawler-ua) | path=/ai-agents-keep-out/secret | ua=ClaudeBot/1.0 (+https://example.com/bot)
```

Category comes from which trap was hit (ground truth); the UA class is
best-effort.

Safety properties:

- **No attacker text in the action line.** `<matches>` (the log line, with an
  attacker-controlled path and UA) is never substituted into the shell
  command. The action passes only `<ip>`, which fail2ban has validated; the
  notes script re-reads the log line as data and builds JSON with
  `jq --arg`.
- **The token is a file path, never a value on the action line**
  (2026-10-03): fail2ban logs the fully expanded action command, which leaked
  the token when it was expanded from `jail.local`. `CFTOKEN_FILE` is passed
  instead.
- **Response checks** follow the External API Action Safety Pattern
  (`docs/COMPLIANCE_SECURITY.md` §5): body `success` field as well as status,
  identity check before a delete, `curl -G` on filtered GETs.

**SELinux, and an open question.** `selinux/corporatetraveldc-fail2ban-cf-egress.te`
grants `fail2ban_t` `name_connect` on `pihole_port_t` (port 443 carries that
label here) so the action can reach `api.cloudflare.com`.
`selinux/corporatetraveldc-secrets-isolation.te` grants `fail2ban_t` read on
`ctdc_secret_cf_honeypot_t` -- but that type labels `~/.secrets/cf-honeypot.token`,
and the action now reads `/etc/corporatetraveldc/cf-honeypot.token`, which is
labelled `container_file_t` (`ls -Z`, 2026-10-06). [UNVERIFIED: whether
`fail2ban_t` may read `container_file_t`; if it may not, every edge ban since
the 2026-10-03 move fails while the local firewalld ban still works. Check
with `sudo ausearch -m avc -se fail2ban_t --start 10/03/2026` and the last
`cf-honeypot-ban` lines in `/var/log/fail2ban.log`.]

### Postmortems kept for their lessons (2026-08-09)

1. **`fail2ban-client reload` did not register a newly added action** on an
   existing jail (v1.1.0). A full `systemctl restart fail2ban.service` did;
   bans survive a restart (`/var/lib/fail2ban/fail2ban.sqlite3`).
2. **SELinux silently blocked outbound HTTPS from `fail2ban_t`** (`curl`
   `http=000`). Found with `ausearch -m avc -se fail2ban_t`; fixed with the
   one-permission module above, not `audit2allow` output verbatim (which also
   asked for `execmem`; the notes script was rewritten to avoid it).

## The rate-limit jail

`nginx-limit-req-corporatetraveldc` watches `/var/log/nginx/error.log` for
`limit_req` trips: `maxretry=1`, `findtime=60`, `bantime=3600`,
`ignoreip = 127.0.0.1/8 ::1 100.64.0.0/10 10.x.x.x/8 192.168.x.x/16`,
`action = %(action_)s` (firewalld only). It no longer runs
`corporatetraveldc-lockdown` (the 2026-08-26 C-1 fix: one rate-limit trip used
to take the whole stack down for an hour). The fix was tracked in August but
sat undeployed until `install-root-copies.sh` installed it 2026-10-04.

A live-only `/etc/fail2ban/jail.d/sshd-corporatetraveldc.conf` (sshd jail,
`maxretry=5`, `bantime=3600`, `findtime=600`) also exists; it is not tracked.

## Verify (operator, root)

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://dispatch.example.com/.env   # expect 000 (dropped)
sudo tail -1 /var/log/nginx/honeypot.log
sudo fail2ban-regex /var/log/nginx/honeypot.log /etc/fail2ban/filter.d/nginx-honeypot.conf
sudo fail2ban-client status nginx-honeypot-corporatetraveldc
sudo fail2ban-client get nginx-honeypot-corporatetraveldc actions   # both actions listed
curl -s https://dispatch.example.com/robots.txt          # 200 through Cloudflare Access (2026-10-06)
```

Test from an address in `ignoreip` or you ban yourself.

## Tuning

- `bantime = -1` for permanent bans.
- Add trap paths to `nginx/snippets/honeypot.conf` as new probe patterns
  appear in `honeypot.log`, sign, and copy to `/etc/nginx/snippets/`.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-security.md`.


### Honeypot paths + fail2ban auto-ban

> ~~**Status at a glance — reconciled 2026-08-19.** The whole system is **deployed and live**, including the Cloudflare-edge ban path. An earlier revision of this file said in one place that the edge path was "blocked on a token" while saying in another that it was "confirmed working end-to-end" — the *later* statement was the true one, and the blocked-on-token text is now dated history rather than current status. All four named vhosts (`dispatch`, `cloud`, `dav`, `www`) are trapped. One thing is genuinely still open: the `000-default-catchall` vhost has no trap include, so unmatched-`Host` / raw-IP probes are dropped with `444` but never logged, and therefore never banned. See [OUTSTANDING as of 2026-08-19](#-outstanding-as-of-2026-08-19--catch-all-vhost-not-trapped).~~

~~A defensive tripwire across the nginx-fronted stack. It exists to catch and auto-ban **two explicit categories of bad actor**:~~

- ~~**(a) AI crawlers / agents that ignore directives.** We publish `robots.txt` and `llm.txt` that declare certain paths off-limits. A well-behaved crawler or agent never fetches them. Anything that *does* fetch a Disallow-ed decoy path has demonstrably ignored the directives it was given — treated as hostile.~~
- ~~**(b) Vulnerability scanners / bots.** The usual background-noise probing for `wp-admin`, `wp-login.php`, `.env`, `.git`, `phpMyAdmin`, `xmlrpc.php`, `/vendor/…`, `/cgi-bin/…`, `actuator`, etc. — paths this stack never legitimately serves, so a request for one is unambiguously a probe.~~


### Honeypot paths + fail2ban auto-ban › Mechanism

1. ~~`nginx/snippets/honeypot.conf` defines a `location` for every trap path. A hit is written to `/var/log/nginx/honeypot.log` (with the **real** client IP — see the Cloudflare note) and the connection is dropped with `return 444` (the bot gets nothing).~~
2. ~~`nginx/conf.d/00-honeypot.conf` provides the `honeypot` log_format and a `map` that resolves the real client IP (`CF-Connecting-IP` when behind the tunnel, else the direct peer).~~
3. ~~`/robots.txt` and `/llm.txt` (served by the snippet) declare the (a)-category decoy paths `Disallow`.~~
4. ~~fail2ban jail **`nginx-honeypot-corporatetraveldc`** watches the log and bans the source IP on the **first** hit (`maxretry=1`) via firewalld.~~


### Honeypot paths + fail2ban auto-ban › Files

| ~~File~~ | ~~Role~~ |
|---|---|
| ~~`nginx/conf.d/00-honeypot.conf`~~ | ~~http-context: `log_format honeypot` + real-client-IP `map`~~ |
| ~~`nginx/snippets/honeypot.conf`~~ | ~~server-context trap locations + `robots.txt`/`llm.txt`~~ |
| ~~`/etc/nginx/snippets/honeypot-website.conf`~~ | ~~**live-only, not repo-tracked** — the `www` variant of the trap locations (see below)~~ |
| ~~`fail2ban/filter.d/nginx-honeypot.conf`~~ | ~~extracts `<HOST>` from each honeypot.log line~~ |
| ~~`fail2ban/jail.d/nginx-honeypot-corporatetraveldc.conf`~~ | ~~the jail (ban on 1 hit, 1-week, firewalld)~~ |

~~**Deployment (2026-10-04).** fail2ban runs its actions as root, so every file above under `fail2ban/` is installed by `sudo scripts/install-root-copies.sh` (hash-checked against the signed manifest, `fail2ban-client -t`, reload), and the actions execute the root-owned copies in `/usr/local/libexec/ctdc/` chained by `installed-check.sh` -- never a script from the checkout. Do not hand-copy these files into `/etc/fail2ban`; the 2026-08-26 rate-limit-jail fix sat undeployed for six weeks that way.~~

**~~Vhost coverage — verified live 2026-08-19~~** *(former heading)*


### Honeypot paths + fail2ban auto-ban › Files › Vhost coverage — verified live 2026-08-19

~~There are **two** trap snippets, not one. Both write to `/var/log/nginx/honeypot.log` using the same `honeypot` log_format and both `return 444`, so the single `nginx-honeypot-corporatetraveldc` jail bans on a hit to either. The difference is only that the `www` variant does **not** serve `robots.txt`/`llm.txt` — the website serves those as real static files, so overriding them there would clobber the real ones.~~

| ~~Vhost~~ | ~~Trap include~~ | ~~Covered?~~ |
|---|---|---|
| ~~`dispatch.example.com`~~ | ~~`snippets/honeypot.conf`~~ | ~~✅~~ |
| ~~`cloud.example.com`~~ | ~~`snippets/honeypot.conf`~~ | ~~✅~~ |
| ~~`dav.example.com`~~ | ~~`snippets/honeypot.conf`~~ | ~~✅~~ |
| ~~`www.example.com` (+ apex, `wrangler.`)~~ | ~~`snippets/honeypot-website.conf`~~ | ~~✅~~ |
| ~~`000-default-catchall` (`server_name _`, `default_server`)~~ | ~~*(none)*~~ | ~~❌~~ |

~~Note the table lists the *trapped* vhosts only. Re-counted live 2026-09-03 (previously 2026-08-23): `/etc/nginx/conf.d/` carries **9** more untrapped vhost files — acars, adsb, dispatch-runner, mcp, ntfy, openwebui, pihole, `tailscale-dispatch-runner` (the tailnet-only runner vhost, missed by an earlier revision of this list), plus a `cloud…conf.bak-20260807` copy (the `ollama` vhost the 2026-08-23 count included is gone, removed with the 2026-08-27 Ollama retirement) — alongside the two non-vhost includes (`00-honeypot.conf`, `00-rate-limit-corporatetraveldc.conf`) and `000-default-catchall.conf`. Do not read the table above as the full vhost inventory.~~

*Superseded block:*
```text superseded
grep -rln "honeypot.conf" /etc/nginx/conf.d/     # note: matches BOTH snippet names
```

~~**Correction to this doc's original action item.** The line that used to sit here said the include still had to be added to `www` **and** `000-default-catchall`. **The `www` half is done** — it was closed by `honeypot-website.conf` (file dated 2026-08-09 17:05) and this doc was simply never updated to say so. Only the catch-all half is still open.~~

**~~⏳ OUTSTANDING as of 2026-08-19 — catch-all vhost not trapped~~** *(former heading)*


### Honeypot paths + fail2ban auto-ban › Files › ⏳ OUTSTANDING as of 2026-08-19 — catch-all vhost not trapped

~~`000-default-catchall.conf`'s server block is, in full, a bare drop (the file also carries a comment header explaining why the explicit `default_server` exists at all — re-read the live file, not just this excerpt, before editing it):~~

*Superseded block:*
```text superseded
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 444;
}
```

> ~~**TODO (open):** add trap coverage to the `000-default-catchall` server block so unmatched-`Host` and raw-IP probes are *banned*, not merely dropped. It is live-only, not repo-tracked, so this cannot be done by editing this repo — it needs a root edit on the box plus `nginx -t && systemctl reload nginx`.~~

~~**Scope the risk accurately.** This is a narrower gap than "no protection". A probe with an unmatched `Host` (including a raw-IP request, whose `Host` is the IP and matches no `server_name`) already gets `444` — connection closed, nothing served. What it does *not* get is a line in `honeypot.log`, so the jail never sees it and the source IP is never banned. The exposure is **missed bans on by-IP/unknown-Host scanning**, not served content.~~

~~Everything else in the "Files" table above is deployed.~~

~~**NEEDS OPERATOR DECISION:** `/etc/nginx/snippets/honeypot-website.conf` is live-only — the repo's `nginx/snippets/` contains only `honeypot.conf`, so the `www` vhost's actual trap coverage exists nowhere in version control and is absent from the "Deploy (sudo)" recipe below. It would be lost on a rebuild-from-repo. Decide whether to (1) commit it to `nginx/snippets/` and add it to the deploy recipe, or (2) fold its trap locations into the shared `honeypot.conf` behind a flag so one file serves both cases — and, either way, whether the catch-all above should reuse that same snippet.~~

**~~⚠️ Cloudflare-tunnel caveat (important, honest)~~** *(former heading)*


### Honeypot paths + fail2ban auto-ban › ⚠️ Cloudflare-tunnel caveat (important, honest)

~~Services reached through the Cloudflare tunnel arrive at nginx **from cloudflared (127.0.0.1)**, not from the real client. The trap always *logs* the real IP (via `CF-Connecting-IP`), but a **local firewalld ban is only effective for direct LAN/tailnet sources** — it cannot stop packets that arrive from cloudflared. For internet scanners hitting a CF-fronted host, the captured IP must also be blocked **Cloudflare-side** (WAF rule / IP Access Rule). Direct-exposed services (LAN `:80`, tailnet) are banned effectively by fail2ban as-is.~~

~~**This is described as a caveat, not a live gap.** It explains *why* the Cloudflare-edge ban action exists; that action is deployed and the Cloudflare-side block is created automatically per ban — no manual dashboard step. See the next section.~~

**~~Closing the Cloudflare-tunnel gap (fail2ban → Cloudflare edge)~~** *(former heading)*


### Honeypot paths + fail2ban auto-ban › Closing the Cloudflare-tunnel gap (fail2ban → Cloudflare edge)

~~**Status: CLOSED — deployed and working.** The gap described in the caveat above is no longer open. Bans now land at both layers: local firewalld (effective for direct LAN/tailnet sources) **and** a Cloudflare IP Access Rule (effective for tunnel-routed internet traffic). Confirmed end-to-end 2026-08-09 by a real honeypot hit producing a real Cloudflare rule with no manual step — see [Cloudflare-edge deploy](#cloudflare-edge-deploy-adds-the-classified-notes-ban-action) below, which is the authoritative section for this feature and carries the verification evidence, the notes-format design, and the two-bug postmortem.~~

~~Re-verified 2026-08-19 (read-only): `fail2ban.service` is `active`; `/etc/fail2ban/jail.d/nginx-honeypot-corporatetraveldc.conf` carries the second `action` line naming `cloudflare-token-corporatetraveldc`; `/etc/fail2ban/action.d/cloudflare-token-corporatetraveldc.conf` is deployed; `/etc/fail2ban/jail.local` exists mode 0600 root-only (holds `cftoken` / `cfzone`); `/var/log/nginx/honeypot.log` is live and being written. Note the deployed action is the repo's own `cloudflare-token-corporatetraveldc`, **not** fail2ban's stock `action.d/cloudflare-token.conf` — the local one adds the classified `notes` field described below.~~

~~Re-verified again 2026-08-23 (read-only, all still true): `fail2ban.service` `active` + `enabled`, version `Fail2Ban v1.1.0` (the version the restart-vs-reload postmortem below applies to); `/etc/fail2ban/jail.local` present, `-rw-------` root:root; both `action.d/cloudflare-token.conf` (stock, unused) and `action.d/cloudflare-token-corporatetraveldc.conf` (deployed, dated 2026-08-09) on disk; `/var/log/nginx/honeypot.log` present and freshly written (mtime same day). `fail2ban-client status` needs root and could not be run from this account, so **current ban counts are unverified** — that one command is the gap in this re-verification, not the config state. Jail parameters read straight off `jail.d/nginx-honeypot-corporatetraveldc.conf` and confirmed to match this doc: `maxretry=1`, `findtime=3600`, `bantime=604800` (1 week), `port=http,https`, `ignoreip = 127.0.0.1/8 ::1 100.64.0.0/10 10.x.x.x/24`.~~

~~<details><summary>History — how this was blocked, and what unblocked it (2026-08-09)</summary>~~

~~The original plan was to use fail2ban's stock `action.d/cloudflare-token.conf` (Bearer-token auth against the zone IP-Access-Rules API), and it was **blocked on a token**: the existing `~/.secrets/cloudflare.key` token was **dead** (verified: `Invalid API Token`). The recorded remedy, kept here because it is still the correct recipe if the token ever needs re-minting:~~

- ~~Dashboard → My Profile → API Tokens → **Create Token → Custom**~~
- ~~Permissions: **Zone · Firewall Services · Edit** (optionally add **Zone · Zone · Read** to look up the zone ID)~~
- ~~Zone Resources: **Include · Specific zone · `example.com`**~~
- ~~Grab the **Zone ID**: dashboard → `example.com` → Overview → Zone ID~~

~~This token can *only* edit that one zone's firewall access rules — not DNS, not tunnels, not other zones.~~

~~**Where the token lives (2026-10-03 — this changed, read it):** the token goes in a root-only FILE, never in `jail.local` and never on the action line:~~

*Superseded block:*
```text superseded
/etc/corporatetraveldc/cf-honeypot.token    # root:root 0600, the bare token, nothing else
```

~~`cf-honeypot-ban.sh` reads it from there (`CFTOKEN_FILE`, passed as a *path* by the action). The earlier recipe — `cftoken = ...` in `jail.local`, expanded into the action as `CFTOKEN="<cftoken>"` — leaked the token into `/var/log/fail2ban.log` on every ban, unban and error, because fail2ban logs the fully-expanded action command string. Only the zone id stays in `jail.local` (`[DEFAULT]`, **live-only, never the repo**):~~

*Superseded block:*
```text superseded
[DEFAULT]
cfzone  = <the zone id>
```

~~Then add Cloudflare as a **second** banaction on the honeypot jail (keeps the local firewalld ban too) — in `jail.d/nginx-honeypot-corporatetraveldc.conf`:~~

*Superseded block:*
```text superseded
action = %(action_)s
         cloudflare-token-corporatetraveldc[cfzone="%(cfzone)s"]
```

~~Then verify a test ban shows under the zone's **Security → WAF → Tools → IP Access Rules** (and clears on `bantime` expiry).~~

~~A fresh token was minted the same day and this shipped — with two substitutions the postmortem below explains: the stock action was replaced by `cloudflare-token-corporatetraveldc` (classified notes), and `fail2ban-client reload` turned out to be insufficient to register a brand-new action — a full `systemctl restart fail2ban.service` was required.~~

~~</details>~~

**~~Deploy (sudo)~~** *(former heading)*


### Honeypot paths + fail2ban auto-ban › Deploy (sudo)

*Superseded block:*
```text superseded
R=/opt/corporatetraveldc/private/ctdi-dispatch-internal
sudo cp $R/nginx/conf.d/00-honeypot.conf /etc/nginx/conf.d/
sudo mkdir -p /etc/nginx/snippets && sudo cp $R/nginx/snippets/honeypot.conf /etc/nginx/snippets/
sudo cp $R/nginx/conf.d/{dispatch,cloud,dav}.example.com.conf /etc/nginx/conf.d/   # now carry the include
sudo cp $R/fail2ban/filter.d/nginx-honeypot.conf /etc/fail2ban/filter.d/
sudo cp $R/fail2ban/jail.d/nginx-honeypot-corporatetraveldc.conf /etc/fail2ban/jail.d/
sudo touch /var/log/nginx/honeypot.log                 # so the jail starts cleanly
sudo nginx -t && sudo systemctl reload nginx.service   # (session has NOPASSWD for the reload)
sudo fail2ban-client reload
```

~~**Status (2026-08-09): deployed and confirmed live.** `fail2ban-client status nginx-honeypot-corporatetraveldc` showed a real ban land from a simulated hit (`Total failed: 1`, `Currently banned: 1`) after the deploy above plus `sudo fail2ban-client reload`.~~

**~~Cloudflare-edge deploy (adds the classified-notes ban action)~~** *(former heading)*


### Honeypot paths + fail2ban auto-ban › Cloudflare-edge deploy (adds the classified-notes ban action)

~~Beyond the base jail, `jail.d/nginx-honeypot-corporatetraveldc.conf` now carries a second `action` entry (`cloudflare-token-corporatetraveldc`) that also creates a Cloudflare IP Access Rule per ban, closing the tunnel-bypass gap described above. Its `notes` field is classified/timestamped (see `scripts/cf-honeypot-notes.sh`) instead of a bare "Fail2Ban <name>" string, so the Cloudflare dashboard's IP Access Rules list is queryable for trend analysis later: category (`ai-crawler-ignored-directive` vs `scanner-or-bot`, decided by which trap path was hit -- ground truth, not UA-guessed), a best-effort UA fingerprint (`ai-crawler-ua` / `seo-crawler-ua` / `scanner-tool-ua` / `generic-script-ua` / `unclassified`), the actual trapped path, and the raw UA string, e.g.:~~

~~**Why `<matches>` is deliberately not used:** fail2ban substitutes tags into `actionban` as raw text before bash ever parses the command line. `<matches>` is the honeypot.log line itself -- path and User-Agent are both attacker-controlled. Splicing that directly into a shell command string, even quoted, is a classic fail2ban misconfiguration: a crafted UA containing a quote or `$(...)` can break out and execute arbitrary commands as whatever user runs the action (root, on the ban path). `cf-honeypot-notes.sh` is invoked with only `<ip>` (fail2ban regex-validates this as a well-formed address before substitution -- no injection surface), re-reads the matching log line itself as plain data, and hands everything to `jq --arg` (a real argument, never shell-interpolated) to build the JSON body. Nothing attacker-controlled is ever re-embedded into a shell command.~~

*Superseded block:*
```text superseded
R=/opt/corporatetraveldc/private/ctdi-dispatch-internal
sudo cp $R/fail2ban/jail.d/nginx-honeypot-corporatetraveldc.conf /etc/fail2ban/jail.d/   # now carries the action= line
sudo cp $R/fail2ban/action.d/cloudflare-token-corporatetraveldc.conf /etc/fail2ban/action.d/
# scripts/cf-honeypot-notes.sh runs directly from the repo path -- no copy needed.

# The token is a root-only FILE (2026-10-03) -- never jail.local, never argv.
# Populate it from the operator's copy without ever printing it:
sudo sh -c 'cat /home/corporatetraveldc/.secrets/cf-honeypot.token > /etc/corporatetraveldc/cf-honeypot.token'
sudo chmod 600 /etc/corporatetraveldc/cf-honeypot.token

# jail.local is live-only, never the repo -- zone id ONLY now:
sudo tee /etc/fail2ban/jail.local >/dev/null <<'EOF'
[DEFAULT]
cfzone  = <zone ID for example.com, from the CF dashboard>
EOF
sudo chmod 600 /etc/fail2ban/jail.local   # live file is 0600 root:root -- match it
sudo fail2ban-client reload

# Verify the deprecated env path is no longer being used (count only -- the
# log may still hold old plaintext lines from before this change):
sudo grep -c "token came from the CFTOKEN env var" /var/log/fail2ban.log
```

~~**Verify:** trigger a trap hit, then check the zone's **Security -> WAF -> Tools -> IP Access Rules** in the Cloudflare dashboard for a new rule with the classified notes format above.~~

~~**Status (2026-08-09): confirmed working end-to-end, live.** A real honeypot hit (nginx trap -> fail2ban filter -> ban -> action) produced a real Cloudflare IP Access Rule with no manual step, e.g.:~~

*Superseded block:*
```text superseded
{"notes": "honeypot | 2026-08-09T20:38:42Z | scanner-or-bot (ai-crawler-ua) | path=/.aws/credentials | ua=Bytespider"}
```

**~~Postmortem: two real bugs hit getting here, both worth knowing about~~** *(former heading)*


### Honeypot paths + fail2ban auto-ban › Cloudflare-edge deploy (adds the classified-notes ban action) › Postmortem: two real bugs hit getting here, both worth knowing about

~~**1. `fail2ban-client reload` does not always rebuild a jail's live Actions set.** Adding the second `action` line to an already-running jail and reloading (not restarting) fail2ban left `cloudflare-token-corporatetraveldc` completely unregistered -- `fail2ban-client get <jail> actions` showed only the original `firewallcmd-rich-rules`, even though the on-disk config was byte-correct and `reload` returned `OK`. A real `sudo systemctl restart fail2ban.service` (not just `fail2ban-client reload`) was required to pick up the newly-added action. Active bans survived the restart (fail2ban persists them in `/var/lib/fail2ban/fail2ban.sqlite3` and reapplies on start). **Takeaway: adding a brand-new action to an existing jail needs a full restart, not a reload, on this fail2ban version (v1.1.0).**~~

~~**2. SELinux blocked all outbound HTTPS from the `fail2ban_t` domain.** Even after the action was correctly registered, every ban attempt failed with curl reporting `http=000` (a connection-level failure, not an HTTP error) -- this only showed up in `/var/log/fail2ban.log` as `ERROR ... returned 1`, invisible without reading that log directly (root-only). `ausearch -m avc -se fail2ban_t` revealed why:~~

*Superseded block:*
```text superseded
avc: denied { name_connect } for comm="curl" dest=443 scontext=system_u:system_r:fail2ban_t:s0
     tcontext=system_u:object_r:pihole_port_t:s0 tclass=tcp_socket
```

~~Port 443 on this box is SELinux-labeled `pihole_port_t` instead of the usual `http_port_t` (likely a side effect of prior Pi-hole SELinux customization) -- `fail2ban_t`'s policy has no rule permitting a connection to that type, so curl never got further than the kernel LSM check, for ANY destination on 443. The same misconfiguration was independently seen blocking `pihole_t` itself earlier the same day, so this is a standing platform issue, not something specific to this action.~~

~~`ausearch -m avc -se fail2ban_t --start today | audit2allow -M ...` was used live to *find* the exact denial (shown above) and confirm the fix, but its raw output is not what's actually deployed -- it also proposed `allow fail2ban_t self:process execmem` (for `grep -oP`'s PCRE JIT compiler), which was deliberately not granted; `cf-honeypot-notes.sh` was rewritten to extract the same fields with `sed` (POSIX BRE, never JIT-compiles) instead, avoiding the permission entirely rather than carrying it as a standing exception. The actual, final, repo-tracked fix is `selinux/corporatetraveldc-fail2ban-cf-egress.te` (one grant: `name_connect` on `pihole_port_t`), built and loaded the same way as every other module in this repo via `selinux/apply-selinux-policy.sh` (see `docs/COMPLIANCE_SECURITY.md`'s "SELinux Grant Policy" section) -- not the ad-hoc `audit2allow -M`/`semodule -i` commands used to diagnose it. The same investigation incidentally also surfaced a "round 3" gap in the separate, pre-existing `selinux/corporatetraveldc-fail2ban-lockdown.te` module (unrelated to Cloudflare -- it covers `scripts/lockdown.sh`/`restore-network.sh`), now fixed in that file too.~~

~~**Takeaway: if a fail2ban action that makes outbound network calls silently does nothing on this box (or any SELinux-enforcing box), check `ausearch -m avc -se fail2ban_t` before assuming the action/script itself is broken** -- `curl -sf` (or checking `%{http_code}` explicitly, as `cf-honeypot-ban.sh` does) is necessary to even *see* the failure, since plain `curl -s` swallows both HTTP errors and this class of connection failure identically as a silent no-op. And once `audit2allow` finds the real denial, treat its output as a starting point to review, not a policy to paste in verbatim -- it optimizes for "stop denying everything observed," not for the narrowest grant that actually resolves the issue.~~

**~~Verify~~** *(former heading)*


### Honeypot paths + fail2ban auto-ban › Verify

*Superseded block:*
```text superseded
# 1) trap fires + logs (from an IP in ignoreip so you don't ban yourself):
curl -s -o /dev/null -w '%{http_code}\n' https://dispatch.example.com/.env      # expect 000/444 (dropped)
sudo tail -1 /var/log/nginx/honeypot.log                                                     # expect a TRAP line w/ your IP
# 2) filter matches the log format:
sudo fail2ban-regex /var/log/nginx/honeypot.log /etc/fail2ban/filter.d/nginx-honeypot.conf   # expect matched > 0
# 3) jail is up:
sudo fail2ban-client status nginx-honeypot-corporatetraveldc                                 # shows jail + banned count
# 4) robots/llm advertise the decoys:
curl -s https://dispatch.example.com/robots.txt ; curl -s https://dispatch.example.com/llm.txt
```


### Honeypot paths + fail2ban auto-ban › Tuning

- ~~`bantime = -1` for permanent bans.~~
- ~~Add your roaming/home IPs to `ignoreip` before testing traps (or you'll ban yourself).~~
- ~~Add trap paths to `snippets/honeypot.conf` as new probe patterns show up in `honeypot.log`.~~
