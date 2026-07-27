# Passwordless-sudo proposal for Claude on corporatetraveldc

Drafted for operator review, then approved 2026-07-27. The approval-gate
mechanism itself (DB table, endpoints, wrapper script) is now **built and
tested end-to-end** — the only remaining step is the sudoers file itself,
which needs root to install, so that part is a handoff to the operator (see
"Installing the sudoers file" at the bottom). Everything else in this doc
is grounded in actual friction hit during tonight's session, not
speculative "might need someday" access.

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

## Still fully excluded — no change, not part of the new standing rule

**`ollama-governor.service` — no access, in any form, including read-only
convenience wrappers.** This is the thermal safety mechanism (SIGSTOP/
SIGCONT pause on the `ollama serve` process at ~75-77°C, resume at
~67-68°C) protecting a Pi 5 with a confirmed-broken/non-working fan. The
standing rule from earlier tonight is: never override or disable this
under any circumstance. Passwordless `restart` on this unit is a bad idea
even though restart isn't literally "disable" — a restart mid-pause-cycle
is a state transition I don't have a reason to trust myself with
unsupervised, and there's no operational need for me to touch it that
tonight's work surfaced. This one stays exclusively operator-controlled,
Termux or otherwise, even under the approval-gate model — an Allow tap
isn't a substitute for judgment about a live thermal-pause state I can't
fully see.

**`/usr/local/bin/ollama_governor.py` — no write access.** Same reasoning.
I have no business editing the safety mechanism's own code under a
passwordless grant, approval-gated or not. If this ever needs a real code
change, that's a propose-then-you-apply-it conversation, same as
everything else tonight.

## Skill audit: pre-existing skills that need an override

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

## Installing the sudoers file (needs you — I don't have root)

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
