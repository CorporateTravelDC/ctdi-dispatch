# Passwordless-sudo proposal for Claude on corporatetraveldc

Drafted for operator review, then approved 2026-07-27. The approval-gate
mechanism itself (DB table, endpoints, wrapper script) is **built and tested
end-to-end**. Everything else in this doc is grounded in actual friction hit
during that session, not speculative "might need someday" access.

> **Status correction, 2026-08-23 — the sudoers grants are INSTALLED and
> LIVE, and the installed set is broader than what this document proposes.**
> This file previously read as a pending proposal whose "only remaining step
> is the sudoers file itself … a handoff to the operator." That has not been
> true for some time. Verified live with `sudo -n -l` (read-only, no password
> prompt — which is itself the proof the NOPASSWD entries exist):
>
> ```
> User corporatetraveldc may run the following commands on corporatetraveldc-dispatch:
>     (ALL) ALL
>     (root) NOPASSWD: /usr/bin/systemctl restart ollama.service, .../start ollama.service, .../stop ollama.service
>     (root) NOPASSWD: /usr/bin/dnf remove *, /usr/bin/dnf autoremove
>     (root) NOPASSWD: /usr/bin/systemctl status argononed.service, .../status cpupower.service,
>                      /usr/bin/systemctl reload nginx.service, /usr/bin/cpupower frequency-set -g schedutil,
>                      /usr/bin/ausearch -c cpupower --start today, /usr/bin/ausearch -m avc --start today
>     (root) NOPASSWD: /usr/bin/systemctl {start,stop,enable,disable,mask,unmask} argononed.service,
>                      /usr/bin/systemctl {enable,disable} cpupower.service, /usr/bin/semanage port -a *
>     (root) NOPASSWD: /usr/bin/systemctl kill --signal=SIGKILL ollama.service      # listed twice — duplicate entry
>     (root) NOPASSWD: /usr/bin/systemctl stop ollama-governor.service, .../start ..., .../restart ollama-governor.service
> ```
>
> Four things follow that this document did not previously say:
>
> 1. **The two proposed grants (ollama.service verbs, `dnf remove`/
>    `autoremove`) are installed exactly as written below.** The
>    "Installing the sudoers file" handoff at the bottom is historical, not
>    a to-do.
> 2. **`(ALL) ALL` is present** — the `corporatetraveldc` account has full
>    sudo *with a password*. The narrow NOPASSWD list is about what runs
>    **unattended**, not about what the account is capable of. Nothing in
>    this document should be read as "this account cannot reach root."
> 3. **`ollama-governor.service` stop/start/restart carries a NOPASSWD
>    grant**, directly contradicting the "Still fully excluded" section
>    below, which asserted no such grant exists and none should — see the
>    correction inline in that section.
> 4. `systemctl kill --signal=SIGKILL ollama.service` was added beyond the
>    original proposal (backing `scripts/ollama-wedged-detector.sh`'s
>    force-kill stage) and is **listed twice** — a harmless but real
>    duplicate worth cleaning up on the next sudoers edit.
>
> `/etc/sudoers.d/` is `drwx------` root-only, so the per-file breakdown
> could not be read from this account; the effective grants above come from
> `sudo -n -l`, which is authoritative for what is actually in force.

## Status: built and tested 2026-07-27

- `approval_requests` table added to `common/db.py` (schema v21), wired into
  both `web` and `poller` startup.
- Three endpoints live in `web/main.py`: `POST /admin/approval-requests`
  (create, admin-token gated), `GET /admin/approval-requests/{id}` (status,
  admin-token gated), `GET /admin/approval-requests/{id}/resolve?action=allow|deny`
  (Tier 0, no auth — this is the one the phone taps).
- `scripts/sudo-approval-gate.sh` — the wrapper: creates the request, pushes
  an ntfy alert with real Allow/Deny action buttons to the `approval-gate`
  topic, polls, runs the command only on `allowed`, reports the recent-
  approval count for the frequency-promotion check at the end.
- **Confirmed empirically** (not just planned): the resolve endpoint answers
  cleanly over `https://dispatch.example.com` from a fully
  external network path (my own sandbox, not Tailscale, not SSH'd into the
  Pi) — HTTP 200, correct JSON, no Cloudflare Access login wall despite the
  cloudflared config's comment suggesting Access-gating. That comment looks
  stale/aspirational, not enforced — worth knowing if you were relying on
  it elsewhere.
  - **Status update 2026-08-23:** the observation above was true when
    written (2026-07-27) but no longer describes the mechanism. A
    hostname-wide Cloudflare Access policy was later applied to
    `dispatch.example.com` — it silently broke resolve taps
    (discovered 2026-08-20), and the resolve path now works via a
    narrowly-scoped `dispatch-approval-resolve-bypass` Access app
    (`decision: bypass` on `/admin/approval-requests/*/resolve`). See
    CLAUDE.md's "RESOLVED 2026-08-20" approval-gate entries.
- Ran a full live dry run: created a request, pushed a real ntfy alert
  (topic `approval-gate` — **you'll need to add that topic in your ntfy app
  to see these**), resolved it via the same Cloudflare URL a phone tap would
  hit, watched the wrapper detect "allowed" and execute a harmless test
  command. Full loop works.

## Bottom line

The list of things that actually needed root tonight is much shorter than
"containers + anything Ollama-related" implies. Rootless Podman already
covers the entire container stack without sudo. The real gaps were two
specific, narrow things — and one of those turned out not to need sudo
either, once I used the right edit method.

## What does NOT need a grant (already works, no sudo required)

**The entire container stack.** Every operation touched tonight — building
the poller image, starting/stopping/restarting any of the 19+
`corporatetraveldc-*` services, `podman build`, `podman images`, Quadlet
reloads via `systemctl --user daemon-reload` — worked with zero elevated
privilege, because this stack is rootless Podman running under the
`corporatetraveldc` user's own systemd instance (`systemctl --user`). There
is no container-stack operation from tonight that justifies a sudo entry.
If there's a specific scenario you have in mind that I haven't hit yet
(binding a port under 1024, an SELinux relabel, something in the
build-images.sh path that assumes rootful podman), name it and I'll
evaluate that specific command — but nothing tonight supports a blanket
grant here.

**Editing `/etc/corporatetraveldc/dispatch.env` and `dispatch-secrets.env`.**
I hit a permission error trying to `cp`/`sed -i` these earlier tonight, and
initially assumed that meant I needed root. It doesn't: the files
themselves are owned `corporatetraveldc:corporatetraveldc` with `rw-------`
or `rw-r-----` — I already own them. The blocker was that
`/etc/corporatetraveldc/` itself is `drwxr-x---` (root-owned, no group
write), and both `cp` and `sed -i` create a new temp file in the same
directory before renaming it into place, which needs directory write. A
plain read-modify-write (open the file, edit in memory, write it back,
no new directory entry) only needs write on the *file*, which I already
have. Used this tonight to clean up the two orphan duplicate lines after
`RUNNER_ENRICHED_TOKEN` in `dispatch-secrets.env` — worked without any
sudo. So this doesn't need a grant either.

## What I'd actually propose

**`systemctl {start,stop,restart,status} ollama.service` — the inference
engine only.**

This is the one real, encountered gap. `ollama.service` runs as
`User=ollama` under system-scope systemd (`/etc/systemd/system/ollama.service`),
not under my `--user` instance, so I can't touch it at all right now — not
even to restart a wedged instance. `journalctl -u ollama.service` and
`systemctl show ollama.service` already work without sudo (system units'
status/logs are world-readable here), so the gap is specifically the
write actions: start/stop/restart.

```
# /etc/sudoers.d/corporatetraveldc-ollama
corporatetraveldc ALL=(root) NOPASSWD: /usr/bin/systemctl restart ollama.service, /usr/bin/systemctl start ollama.service, /usr/bin/systemctl stop ollama.service
```

## Standing rule, decided 2026-07-27: this and `dnf` are approval-gated, not freely usable

Operator decision: both `ollama.service {start,stop,restart}` and
`dnf remove`/`dnf autoremove` go into the sudoers file as NOPASSWD entries
— but I don't get to use either one just because the entry exists. Before
running anything under either grant, I send an explicit Allow/Deny request
and wait for a tap. This replaces my earlier "just exclude dnf entirely"
position below — the operator's version is better: it keeps a human in the
loop (useful specifically when mobile and typing a full command back to me
isn't practical) without requiring the operator to be at a keyboard for
every single use.

```
# /etc/sudoers.d/corporatetraveldc-ollama
corporatetraveldc ALL=(root) NOPASSWD: /usr/bin/systemctl restart ollama.service, /usr/bin/systemctl start ollama.service, /usr/bin/systemctl stop ollama.service

# /etc/sudoers.d/corporatetraveldc-dnf
corporatetraveldc ALL=(root) NOPASSWD: /usr/bin/dnf remove *, /usr/bin/dnf autoremove
```

(`dnf remove *` with a wildcard is broader than I'd pick unsupervised, but
since every actual use is gated behind an explicit per-request Allow tap
naming the exact package list, the wildcard just avoids re-editing sudoers
per package — the operator sees and approves the real command each time
regardless of what the sudoers pattern allows in principle.)

**Approval-gate mechanism (design, not yet built):**

1. I add two admin endpoints to the existing FastAPI web service: one that
   creates a pending approval request (command, reasoning, timestamp,
   random request ID), one that resolves it allow/deny.
2. I push an ntfy notification using ntfy's native action-button feature —
   the notification itself shows Allow / Deny buttons, no typing required.
   Tapping one fires the resolve endpoint directly from the phone.
3. I poll (or the reactor pattern already used elsewhere in this stack
   handles it) until the request resolves to allow, deny, or times out.
   Allow → I run the actual sudo command. Deny or timeout → I don't.
4. Every request/resolution logs to the existing audit log
   (`/admin/audit`), same as everything else admin-side.

**Resolved 2026-07-27: build the approval-gate pattern as the default,
generic mechanism** for sensitive/impactful actions going forward — not a
one-off scoped to just these two grants. Any future sensitive ask (new
sudo grant, or any other action that warrants a human-in-the-loop check)
should route through this same allow/deny-via-ntfy mechanism rather than
inventing a new one-off pattern each time. Confirmed by the operator
2026-07-27.

**Resolved 2026-07-27: reachability fallback chain, fail-closed.**
Tailscale stays the default/primary path for the resolve callback, same as
everything else in this stack. If a request over Tailscale times out, retry
once over the Cloudflare Tunnel (the narrow exception below). If *that*
attempt also times out — treat the whole request as a **denial**, not a
pass-through. No response, from either path, ever defaults to allow. This
answers the earlier open question about the Tailscale/Cloudflare exception:
Cloudflare is in, but only as a one-shot retry after a Tailscale timeout,
never the first attempt.

```
resolve attempt 1: Tailscale        (default, as with everything else)
  ↓ timeout
resolve attempt 2: Cloudflare Tunnel (one retry, narrow exception, no
                                       sensitive data in the request itself
                                       — random ID + allow/deny action only)
  ↓ timeout
→ DENY (fail-closed; silence is never consent)
```

**Honest implementation note:** this exact retry sequence isn't what got
built, and I want to be upfront about why rather than let the "resolved"
language above imply it is. ntfy action buttons are a single static URL
per button — there's no client-side "try URL A, then URL B" retry
available at that layer; the phone either reaches the one URL baked into
the button or it doesn't. So the wrapper points both Allow and Deny at the
Cloudflare Tunnel URL directly (verified reachable regardless of Tailscale
state), not at Tailscale first. What I did build that delivers the same
actual guarantee — fail-closed, no response ever means yes — is the
request's own TTL: `get_approval_request()` checks expiry on every read,
and a request nobody ever taps reads back as `expired` the moment its TTL
passes, never as an implicit allow. So the *outcome* ("silence is never
consent") is real and tested tonight; the specific *mechanism* is "single
Cloudflare URL + server-side expiry" rather than "Tailscale retry then
Cloudflare retry then deny." If you want the literal two-network-hop retry
behavior, that would need a small piece of middleware between the ntfy tap
and the resolve endpoint (something that tries Tailscale, falls back to
Cloudflare, then gives up) — buildable, just wasn't what tonight's version
does.

**Resolved 2026-07-27: frequency-based promotion proposal.** Track
approvals per distinct command pattern (e.g. `dnf remove docs-cleanup-set`,
`systemctl restart ollama.service`) in the same audit log already backing
everything else here. If a given pattern gets **approved more than twice
within a rolling 7-day window**, I proactively propose — next time it comes
up, or as a standalone note — folding that specific command into the
sudoers file as a standing NOPASSWD entry with *no* approval-gate step,
since repeated fast approvals are a signal the human-in-the-loop check has
stopped adding real judgment for that specific, narrow case. This is a
proposal only, same bar as every other sudoers change here — it still
needs an explicit yes before the gate actually comes off for that command.

## DR/time-sensitive auto-promotion, decided 2026-08-15

Standing rule: any approval-gate request that is a DR use case, is
time-sensitive, or asks for a `systemctl kill`/forceful-restart-class
action gets promoted to **max ntfy priority (5) automatically** — not
left to each caller to remember to set. Implemented in
`scripts/sudo-approval-gate.sh` itself (not per-caller convention, same
lesson as the scattered-timeout mess this same night's model rebuild was
about): a request auto-qualifies if the command text contains `kill`
(case-insensitive — covers `systemctl kill`, `SIGKILL`, etc.) or the
caller sets `APPROVAL_GATE_DR=1` explicitly for a DR/time-sensitive
scenario that doesn't literally involve `kill`. Everything else stays at
the existing default priority 4. `APPROVAL_GATE_PRIORITY` is still
available as a raw override underneath both rules, for the rare case
something needs a priority other than 4 or 5.

Same 10-minute TTL, same fail-closed behavior (deny/expiry/silence == do
not run) as every other approval-gate request — a DR-classed request
gets seen faster and louder, it does not get a longer window or a
default-allow. First real caller: `scripts/ollama-wedged-detector.sh`'s
force-kill stage (T+120s of confirmed zero CPU progress after both
TIER1/TIER2 mitigation attempts failed — see that script's own header
comment for the full escalation ladder).

## Still fully excluded — no change, not part of the new standing rule

**`ollama-governor.service` — softened 2026-08-15, still a hard gate.**
This is the thermal safety mechanism (SIGSTOP/SIGCONT pause on the
`ollama serve` process at ~75-77°C, resume at ~67-68°C). The original
"never override or disable this under any circumstance, in any form"
rule from earlier tonight was written as an artifact of a specific prior
incident plus an assumption of a confirmed-broken/non-working fan — real
conditions all through tonight's actual model-rebuild work never
approached anywhere near the 75°C trip point that assumption was
guarding against (60-65°C observed, fan audibly spinning ~2300rpm), so
an unconditional total-prohibition turned out to be more rigid than the
real risk warranted. Softened, not removed: this service can **never be
silently or automatically stopped/started/restarted** — every such
action requires EITHER (a) the operator acting directly at a terminal
(interactive sudo), OR (b) an explicit Allow tap through
`sudo-approval-gate.sh`, which auto-promotes any `ollama-governor` request
to max ntfy priority (5) — see "DR/time-sensitive auto-promotion" above. An
Allow tap still isn't a substitute for judgment about a live thermal-pause
state nobody can fully see remotely, which is exactly why it requires an
explicit human tap every time.

> **Correction, 2026-08-23 — the parenthetical this paragraph used to carry
> ("no passwordless grant exists for this and none should") is factually
> wrong against live state, and so is the closing contrast that said this
> unit differs from `ollama.service` in that respect.** `sudo -n -l` shows:
>
> ```
> (root) NOPASSWD: /usr/bin/systemctl stop ollama-governor.service,
>                  /usr/bin/systemctl start ollama-governor.service,
>                  /usr/bin/systemctl restart ollama-governor.service
> ```
>
> A standing passwordless grant for all three verbs **does** exist, on the
> same footing as the `ollama.service` grant. What still holds is the
> *policy*: the enforcement is `scripts/sudo-approval-gate.sh`'s
> priority-5 auto-promotion (verified live — `sudo-approval-gate.sh:64`
> matches `*ollama-governor*` in the command string, alongside `*kill*` and
> `APPROVAL_GATE_DR=1`), not the absence of a sudoers entry. That is a
> materially weaker guarantee than this section implied: the gate is a
> convention this repo's own scripts follow, and a bare
> `sudo -n systemctl stop ollama-governor.service` would succeed without a
> tap.
>
> **NEEDS OPERATOR DECISION:** either drop the `ollama-governor` NOPASSWD
> entry so the sudoers file matches the stated policy (interactive sudo or
> approval-gate only), or keep it and restate this section as
> "convention-enforced, not sudoers-enforced." Do not leave the document
> asserting a grant does not exist when it does.

**`/usr/local/bin/ollama_governor.py` — no write access.** Same reasoning.
I have no business editing the safety mechanism's own code under a
passwordless grant, approval-gated or not. If this ever needs a real code
change, that's a propose-then-you-apply-it conversation, same as
everything else tonight.

## Skill audit: pre-existing skills that need an override

> **RESOLVED / HISTORICAL — verified live 2026-08-23. Everything in this
> section is a 2026-07-27 snapshot of a skill that has since been rewritten;
> none of it describes current state.** Two separate corrections:
>
> 1. **The four "hand the operator a sudo command" spots no longer exist.**
>    The skill's live file is
>    `~/.config/Claude/local-agent-mode-sessions/skills-plugin/…/skills/corporatetraveldc-dispatch-ops/SKILL.md`
>    (rewritten 2026-08-02, **outside this repo** — the repo's own
>    `skills/corporatetraveldc-dispatch-ops/` directory contains only
>    `ACARS-addendum.md` and has no `SKILL.md` at all, so a reader grepping
>    the repo for the table below will find nothing). `grep -c sudo` against
>    the live file returns **0**. The table below is kept as the record of
>    what was checked, not as a list of live spots.
> 2. **The "Bigger finding" staleness list below was acted on and is
>    closed.** The 2026-08-02 rewrite explicitly corrects every item: it
>    documents `DISPATCH_ADMIN_TOKEN` as the real env var (and `csex-token`
>    as the real CLI name — note this repo's own module is `src/ctdc_token/`
>    and its usage strings say `ctdc-token`, so the two names still disagree
>    and neither is on `PATH`); it states the state dir is
>    `/var/lib/corporatetraveldc/` "(NOT `/var/lib/corporatetraveldc-dispatch/`)";
>    it names the real `corporatetraveldc-*` units; it calls the old skill
>    inventory (`daily-brief`, `cps-recompute`, `anomaly-investigate`,
>    `codeplug-author`, `historical-query`) "fabricated … none of these
>    exist"; and it records that `references/api-reference.md` /
>    `references/troubleshooting.md` "never existed." **Do not open a
>    "dedicated pass to rewrite this skill" on the strength of the paragraph
>    below — that pass already happened.**

Checked every skill under the skills directory for anything that hardcodes
"hand this to the operator via sudo" behavior that would now be stale once
the approval-gate mechanism exists. Only one skill references `sudo` at
all: **`corporatetraveldc-dispatch-ops`**. Four spots in it currently tell
Claude to give the operator a sudo command to run manually:

| Spot | Current instruction | In scope of tonight's grant? |
|---|---|---|
| `anomaly-investigate` trigger | `sudo systemctl start corporatetraveldc-dispatch-anomaly-investigate.service` | No — not ollama.service or dnf, stays operator-only |
| `historical-query` trigger | `sudo tee .../historical-query.txt` + `sudo systemctl start corporatetraveldc-dispatch-historical-query.service` | No — same |
| `codeplug-author` trigger | `sudo tee .../codeplug-task.txt` + `sudo systemctl start corporatetraveldc-dispatch-codeplug-author.service` | No — same, and this one's CUI-guarded regardless |
| VIP watchlist file edit | `sudo tee -a .../vip_watchlist.txt` + `sudo sed -i ...` | No — same |

None of these four match the two things actually approved tonight
(`ollama.service` start/stop/restart, `dnf remove`/`autoremove`), so none
of them get folded into the sudoers file by this decision. They stay
exactly as written — hand the command to the operator — unless you
explicitly want to extend the approval-gate pattern to cover them too.

**Bigger finding, separate from the sudo question:** this skill looks
substantially stale against what's actually running tonight, independent
of the sudo issue:

- It references a `CSEX_DISPATCH_TOKEN` env var and `csex_<user>_<32-char>`
  token format, issued via `csex-token create`. The actual admin auth key in
  `dispatch-secrets.env` is `DISPATCH_ADMIN_TOKEN` (confirmed while building
  the approval-gate wrapper tonight — I initially assumed `DISPATCH_TOKEN`
  too, from the same stale-documentation trail, and had to grep the secrets
  file to find the real key name).
- It names systemd units as `corporatetraveldc-dispatch-*` (e.g.
  `corporatetraveldc-dispatch-poller`, `corporatetraveldc-dispatch-anomaly-investigate.service`).
  Every real unit touched tonight is named `corporatetraveldc-*` — no
  `-dispatch-` infix (`corporatetraveldc-ops-brief.service`,
  `corporatetraveldc-poller.service`, etc.).
- It uses `/var/lib/corporatetraveldc-dispatch/` as the state directory.
  The real path is `/var/lib/corporatetraveldc/`.
- Its "skill inventory" table lists `daily-brief`, `cps-recompute`,
  `tfr-enrichment`, `route-impact`, `weekly-summary`, `anomaly-investigate`,
  `codeplug-author`, `historical-query` — none of which match the actual
  running skills from tonight (`ops_brief.py`, `ep_advance_brief.py`,
  `aam_weekly_watch.py`, `dispatch_desk_memo.py`, `second_brain_daily.py`,
  `second_brain_weekly.py`, `thermal-ingest-guard.py`, etc.).
- It points to `references/api-reference.md` and `references/troubleshooting.md`
  for further detail — neither file exists in the skill directory.

This wasn't part of what you asked me to check tonight, but it's the kind
of thing that could actively mislead a future session (wrong token env var
name, wrong unit names, wrong file paths, a fabricated-looking skill
inventory). Worth a dedicated pass to rewrite this skill against the
actual current system whenever there's time for it — separate task from
the sudo work above.

## If you want to expand this later

Anything added later should follow the same test this list did: point to
an actual command that was blocked tonight (or a clearly-named future
scenario), not a category. "Containers" turned out to need nothing.
"Ollama" turned out to need exactly three verbs on exactly one unit. That
pattern — small, named, evidenced — is the one I'd want to keep using
if this list grows.

## Installing the sudoers file — DONE, kept as historical record

> This section is the original 2026-07-27 handoff. **It has been carried
> out**: the grants below are live, verified 2026-08-23 via `sudo -n -l` (see
> the status correction at the top of this file, which also lists the
> additional grants installed since). Kept verbatim because it is the
> recipe to reuse if the sudoers config is ever rebuilt — not because
> anything here is still outstanding.

```bash
sudo visudo -f /etc/sudoers.d/corporatetraveldc-approval-gated
```

Paste this exactly, save, exit:

```
corporatetraveldc ALL=(root) NOPASSWD: /usr/bin/systemctl restart ollama.service, /usr/bin/systemctl start ollama.service, /usr/bin/systemctl stop ollama.service
corporatetraveldc ALL=(root) NOPASSWD: /usr/bin/dnf remove *, /usr/bin/dnf autoremove
```

`visudo` validates syntax before saving — if it rejects the file, nothing
takes effect and your existing sudo config is untouched. Once it's in,
nothing changes automatically: I still won't run either command without
going through `scripts/sudo-approval-gate.sh` and getting an explicit
Allow first.

**One more thing you'll need to do:** add the `approval-gate` topic in your
ntfy app (Settings → Subscribe to topic) so these pushes actually reach
your phone — it's a new topic, separate from `ops-health`/`dispatch-debriefs`/etc.
