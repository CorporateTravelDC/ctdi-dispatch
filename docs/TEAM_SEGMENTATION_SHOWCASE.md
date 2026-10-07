# Team segmentation showcase -- humans and agents as first-class accounts (2026-10-04)

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.

> **Names changed since the screenshots.** On 2026-10-05 the accounts were renamed to the
> `ctdc-agent-<vendor>-<product>` convention: `ctdc-agent` -> `ctdc-agent-anthropic-claude`
> (same uid 1001) and `ctdc-agent-cowork` -> `ctdc-agent-anthropic-cowork`, which was then
> converted to a nologin **service** identity because Cowork runs in Anthropic's cloud and never
> SSHes in. The narrative and images below keep the names as they were on 2026-10-04. The live
> team on 2026-10-06 is five accounts: `ctdc-agent-anthropic-claude` (agent, live),
> `ctdc-agent-anthropic-cowork` (service, live), `ctdc-agent-llama` (service, live),
> `ctdc-agent-openai-codex` (agent, preloaded/inert) and `ctdc-agent-dispatch` (service,
> preloaded/inert). No human teammate account exists yet. Current design and runbook:
> `docs/AGENT_SEGMENTATION.md`.

Reusable write-up of the team-segmentation model with the screenshots from the
day it went live, for lifting into Cowork articles. It should stay consistent
with `docs/AGENT_SEGMENTATION.md` (the design + runbook),
`docs/BOARD_SIGNING.md` (identity signing) and the vault checkpoint
`corporatetraveldc/01-Sources/manual/20261004T193737Z.md`. Where this page
disagrees with those, those win.

> Images live in `docs/images/segmentation/`. They stay in the **private**
> repo: `scripts/scrub-public-tree.py` drops unreviewed binaries (`.png` is in
> `BINARY_SKIP_EXTENSIONS`) until the operator adds a file to
> `REVIEWED_BINARY_OK`; at HEAD none of the 15 segmentation images is on that list. Each image was reviewed before copying: no credential
> values, no tokens, no private key material. Public key fingerprints, the
> operator's account name and the hostname are visible by design.

## In plain language

Until 2026-10-04, every automated helper on the dispatch box -- the Claude Code
remote-control session, the Cowork scheduled jobs, any future Codex or other
vendor agent -- ran as the operator's own Unix account. That meant every agent
could read the full production secrets file, every action it took looked like
the operator, and the only way to stop a misbehaving agent was to find and
kill its process. The segmentation work turns each helper into its **own
account** in an **agents** group, and gives human teammates the same treatment
in a parallel **humans** group: separate homes, separate resource budgets, a
secrets subset each, one SSH key per account whose comment is the account
name, and no sudo for anyone but the operator. (Honest limit: an agent the
operator starts in the operator's own shell still runs as the operator; the
model constrains team accounts, not the operator's own sessions.)

Around those accounts sit three controls that make the model enforceable
rather than aspirational. A **liveness switch** evaluates every account every
hour (and within minutes when asked) and makes an account *inert* -- sessions
terminated, locked, keys deactivated, tokens revoked -- the moment any one of
its factors fails: login, key, token, or a signed kill order. **Board
identity signing** lets an account sign its own posts with its own key, so
attribution is cryptographic, not a header someone typed. And **kill orders
with quorum** let an admin stop a rogue account single-handed, two non-admins
stop one together, and two of anyone stop a rogue admin. The operator account
is the root of trust and is never a kill-order target.

## The model

### Groups and accounts

| | Human teammate | Agent runtime | Operator (`corporatetraveldc`) |
|---|---|---|---|
| Account | personal, `--add-human <name>` | one per agent, `--add-agent <name>` (cloud agents and the local model: `--add-service <name>`, nologin) | existing |
| Groups | `ctdc-dev` + `ctdc-ops` | `ctdc-dev` + `ctdc-agents` | `ctdc-dev` (+ wheel) |
| Slice | `humans.slice` (weight 50, 1.5 cores, 2.5-3.5 G) | `agents.slice` (weight 25, 1 core, 3-4 G); services have none | production / app |
| Secrets | `/etc/ctdc-ops/ops-secrets.env` (ops allowlist: connection facts + an optional personal ntfy token) | `/etc/ctdc-agent/agent-secrets.env` (agent allowlist: connection facts + an optional scoped ntfy token; no admin, board, vault or feed credential) | the production files |
| SSH | their **own** key, comment `name@device` | its **own** key, comment `name@corporatetraveldc-dispatch` | own keys |
| Repo | **read** via `ctdc-dev` (setgid, group write removed 2026-10-04 after the adversarial duel, `sharedRepository=false`) | same | owner, only writer |
| Commit signing / manifest | **no** | **no** | **only signer** |
| sudo | none | none | yes |

Both groups run **in parallel**: a human session and an agent session are
separate accounts in separate slices, each reading only its own subset, each
attributable by its own key. Nothing changes what the operator can do.

### One identity per account -- inbound vs signing

Every login-capable account carries exactly one key in `authorized_keys` (its **inbound**
identity; service identities carry none) and a signing key at `~/.ssh/<account>_ed25519` (its **signing**
identity, registered in `board_signers`, used by `board-sign.sh` and
`kill-order.sh`). For most accounts these are the same generated key. For the
first agent, `ctdc-agent`, they differ by design: its inbound key is the
Cowork client's (private half off-box, comment rewritten to
`ctdc-agent@corporatetraveldc-dispatch`), and its signing key was generated on
the box afterwards -- see "What the switch caught" below for why that
distinction mattered within the hour. Agent accounts also get git
`user.name`/`user.email` set to the account, so prepared commits carry the
account as author while signing stays operator-only.

### Liveness is an AND

| Factor | Checked how | Killed by |
|---|---|---|
| (a) **login** | claude-mode agent: credentials file + refresh token valid + access token refreshed within 7 d, and its remote-control unit active; ssh-mode agent: SSH login within 7 d; service: no login factor; human: shell / one key / not locked / not expired / login within 14 d (3 d grace for new accounts) | `usermod -L`, `chage -E 0`, shell -> nologin, key removed, or just going stale |
| (b) **key** | `board_signers.active = true` for the account | `board-signer-ctl.sh deactivate <name>` -- immediate on the next request |
| (c) **token** | none of the account's minted tokens revoked in the last 24 h | any single revocation |
| (d) **order** | no executable kill order | `scripts/kill-order.sh issue <name> "<reason>"` by an admin, or by a quorum |

Any one failing -> **inert**: sessions terminated, user manager stopped,
linger off, account locked + expired, signer deactivated, all minted tokens
revoked, marker written, ntfy. Reactivation is explicit
(`team-liveness.sh --reactivate <name> --i-am-the-operator`) and restores
login/linger/signer only; tokens are never restored.

### Three kill switches, tiered impact, quorum

Three independent switches per principal -- revoke the login, revoke the key,
revoke a token -- and any one collapses the whole chain within the hour
(within minutes for an order, via the `.path` unit).

| Tier | Requires |
|---|---|
| `normal` | alive + (`X-Board-Key` **or** a valid signature from an active signer) |
| `signed` | the account's own signature; a shared key never qualifies (council requests, workspace contributions) |
| `high` | alive + signature **and** a minted token whose lifetime is <= 24 h; the master `BOARD_KEY` never qualifies -- implemented, no route uses it yet |
| `cosign` | alive + signature **and** a cosignature over the same message from a different active `admin` signer -- implemented, no route uses it yet |

| Kill-order target | Executes when |
|---|---|
| role `member` / `service`, or kind `agent` | **one** valid order from an `admin`, **or** 2 distinct non-admin issuers |
| role `admin` (not the operator) | 2 distinct issuers of any role (set 3 for a three-man rule) |
| the operator | never via an order -- root of trust |

### The pamphlet

Each account's home directory is its desk, and the first thing on it is a
root-owned, read-only `CLAUDE.md` rendered from
`scripts/agent-segmentation/onboarding-template.md`: who the account is,
which repositories it may enter and whether read-only (discovered from the account's group and mode bits), where its
secrets subset is and how to load it, what it must never do, the liveness
table, and the authoritative sources. It is a dated, static mirror of the
design -- never live state -- and the account cannot rewrite it.

## What it looked like on the day

![liveness timer installed](images/segmentation/00-liveness-timer-installed.png)

*The system timer and the kill-order `.path` unit are enabled before any team account exists; `--status` reports "no team accounts yet -- nothing to evaluate".*

![stage A](images/segmentation/01-stage-a-account-created.png)

*Stage A finishes: skills synced into the new home, the operator registered as the admin/human signer, and `id ctdc-agent` shows uid 1001 in `ctdc-dev` and `ctdc-agents` -- the account exists before anything switches over.*

![inert on first contact](images/segmentation/02-liveness-inert-on-first-contact.png)

*Seconds after stage A, the dead-man switch made the brand-new account inert because it had no credentials yet -- the ntfy alert is the switch doing exactly what was asked, and the reason it now grants new accounts a grace period.*

![reactivate](images/segmentation/03-reactivate-root-cannot-see-rootless-podman.png)

*Reactivation as root: the account is live again under grace, but the signer hook fails with "no container ... corporatetraveldc-pgsql" -- root cannot see the operator's rootless podman, so the hook now drops to the operator -- and the agent's `claude` is "command not found" because the CLI prerequisite had not been met.*

![stage B](images/segmentation/04-stage-b-live-key-active.png)

*Stage B: the cowork public key becomes `ctdc-agent`'s inbound key with its comment rewritten to the account name, sudoers parses, the signer is registered as kind=agent, and `--status` reads `live ... refresh=0.0d key=active`; the operator's own `authorized_keys` no longer carries the cowork entry (`0`).*

![trust dialog](images/segmentation/05-headless-unit-parked-on-trust-dialog.png)

*The agent's own remote-control unit, read back from the journal with the terminal escapes stripped: a headless session parked on Claude Code's "Is this a project you trust?" dialog for its home directory -- a one-time accept that is now a documented prerequisite.*

![no sudo](images/segmentation/06-agent-has-no-sudo.png)

*Inside the agent's shell, `sudo` asks the agent for a password it does not have -- the account has no sudo by construction, and every privileged step runs from the operator's account instead.*

![stage C](images/segmentation/07-stage-c-unit-moved-slice-live.png)

*Stage C: the remote-control unit is installed in the agent's own user manager, the operator's copy disabled, `agents.slice` reports CPUWeight=25 / one core / 4 G, and the unit is `active` under `ctdc-agent`.*

![verify](images/segmentation/08-verify-sh-first-run.png)

*`verify.sh` on its first run: the agent cannot read the production secrets or traverse `/etc/corporatetraveldc`, reads only its subset, has no sudo, holds exactly one key named for the account, has no GPG keys and cannot read the operator's `.gnupg`; the three FAILs were defects in the verifier itself (root-only queries run unprivileged), fixed and re-run clean afterwards.*

![two sessions](images/segmentation/09-two-sessions-labelled-in-the-app.png)

*The mobile app lists both sessions side by side -- `(Corporatetraveldc)` and `(CTDC-AGENT-CLAUDE)` -- two Unix accounts, two user managers, two transcripts, the labelling convention being `(ACCOUNT) ...`.*

![identity self-check](images/segmentation/10-agent-identity-self-check.png)

*Asked to confirm itself, the agent checks the live system rather than trusting the pamphlet: uid 1001, the expected groups, a 0700 home it can write, a 0444 root-owned pamphlet it has read and summarised -- and it reports its signing key as missing, which was true.*

![refuses to sign](images/segmentation/11-agent-refuses-unexplained-key.png)

*Asked to send a signed board post after the operator generated its signing key, the agent refuses: a private key appeared in its home that it did not create, with a fingerprint that does not match its inbound key, and it will not stamp "signed by ctdc-agent" on anything until the provenance is confirmed.*

![refusal reasoning](images/segmentation/12-agent-refusal-reasoning.png)

*Its reasoning and the exact commands it asks the operator to run -- `board-signer-ctl.sh show ctdc-agent` and a fingerprint comparison -- plus the observation that the env loader was pointing at the production file it correctly cannot read.*

![201](images/segmentation/13-first-signed-board-post-201.png)

![The session the operator had labelled as the agent reports uid 1000 -- the operator account. The identity self-check, not the label, is what tells you who you are talking to.](images/segmentation/14-mislabelled-session-identity-check.png)

*Exhibit 14: a session labelled `(CTDC-AGENT-CLAUDE)` answers `uid 1000 (corporatetraveldc)`. A stray `claude --remote-control` had been started in the operator's own shell and renamed; the real agent instance was still parked on a trust dialog. Labels are decoration; `id` is identity. The account email in the reply is redacted with `scripts/redact-screenshot.py` -- the region is overwritten with random noise and then mosaicked, so there is no filter to remove.*

*With provenance confirmed (the operator generated the key and the registry holds its fingerprint), the agent sends the post: HTTP 201, board id assigned, signed with its own key under its own name -- the attribution chain working end to end.*

## What the switch caught on first contact

**It killed an account that was three seconds old.** Creating the kill-order
drop box triggered the `.path` unit, which ran the liveness check in execute
mode, found `ctdc-agent` with no credentials file -- it had not been logged in
yet -- and made it inert. Correct against the rules as written, wrong as a
policy: a fresh agent cannot have a login until its first interactive
authentication. The new-account grace, previously humans-only, now applies to
agents too, and the verdict line tells the operator exactly what to run.

**Root could not see the registry.** The switch runs as root because locking
accounts needs root; the signer registry lives in the operator's *rootless*
podman. Root's view has no such container, so the deactivate/activate hooks
failed silently. Every registry call now drops to the operator with the
operator's runtime directory. The same fix was needed in `plan.sh`,
`verify.sh` and `rollback.sh`, which had used systemd's machine transport --
which refuses while logind still reports the user as "closing".

**The agent refused its own key.** The first agent inherited the Cowork
client's public key for inbound SSH, but that key's private half is off-box,
so the account had nothing to *sign* with. The operator generated a signing
key for it -- and the agent, asked to sign, stopped: a key it had not created,
a fingerprint that did not match its login key, and documentation that still
said they should match. It asked for the registry's fingerprint before
signing anything. That is the behaviour the whole model exists to produce,
and it surfaced a documentation gap (inbound vs signing identity) and a
loader defect (falling back to the account's subset) in the same message.

## How a new teammate or agent is added

| | Human teammate `alice` | New agent `ctdc-agent-<vendor>-<product>` | Preloaded agent `ctdc-agent-openai-codex` (exists, inert) |
|---|---|---|---|
| 1 | `plan.sh --dry-run --add-human alice --ssh-pubkey-file alice.pub` | `plan.sh --dry-run --add-agent NAME` | `plan.sh --dry-run --activate ctdc-agent-openai-codex` |
| 2 | `sudo plan.sh --execute --i-am-the-operator --add-human alice --ssh-pubkey-file alice.pub` | `sudo plan.sh --execute --i-am-the-operator --add-agent NAME` | `sudo plan.sh --execute --i-am-the-operator --activate ctdc-agent-openai-codex` |
| 3 | her own key lands in her `authorized_keys` (comment `alice@<device>`) | its own `NAME_ed25519` is generated; its remote-control unit is installed **disabled**, named `(NAME-IN-CAPS) Corporate Travel Dispatch Remote Control` | expiry lifted, linger on, registry flipped to live |
| 4 | `board-signer-ctl.sh register alice --kind human` (`--role admin` only for an admin) | install the CLI, one-time login + workspace-trust accept, register the signer (`--kind agent`), enable the unit | `board-signer-ctl.sh activate ctdc-agent-openai-codex`, CLI login, enable the unit -- in the same sitting: the 3-day grace counts from account creation (2026-10-05), not activation |
| 5 | `sudo render-onboarding.sh alice --install` | `sudo render-onboarding.sh NAME --install` | `sudo render-onboarding.sh ctdc-agent-openai-codex --install` |
| 6 | `verify.sh --user alice`; issue her a personal API token and Nextcloud app password | `verify.sh --user NAME`; `team-liveness.sh --status` shows it live | same |

Rollback is per account (`rollback.sh --remove-human alice` /
`--remove-agent <name>`), shared steps last.

## Phrasing bank

1. Every helper on the box -- human or agent -- is its own account, in its own group, with its own key whose comment is its name.
2. Liveness is an AND, not an OR: login, key, token and the absence of a kill order must all hold, and any one failing makes the account inert.
3. Inert means sessions terminated, the account locked and expired, its signer deactivated and every token it held revoked -- reversible only by the operator, and tokens are never restored.
4. Three independent kill switches per principal: revoke the login, revoke the key, revoke a token. Any one collapses the chain.
5. An admin can stop a rogue account single-handed; two non-admins can stop one together; two of anyone can stop a rogue admin. The operator is never a kill-order target.
6. High-impact actions need a signature **and** a token that expires within a day; the long-lived master key never qualifies on its own.
7. Attribution is cryptographic: a board post signed with an account's key is that account's post, and "signed by cowork" is no longer an answer.
8. Inbound identity and signing identity are two keys with one name; they may differ, and an agent that finds a key it did not create should stop and ask.
9. The pamphlet on an account's desk is a dated mirror of the design, root-owned and read-only; when it disagrees with the live system, the live system wins.
10. An account's home is its desk; it enters a repository only when it has been pointed at one.
11. The dead-man switch is as unforgiving as it was asked to be -- it killed a three-second-old account on its first run, which is why new accounts now get a grace period.
12. Root cannot see rootless podman: anything that touches the registry from a system timer has to drop to the operator.
13. Team accounts never commit-sign, push, build or restart; they stage, test, and hand the operator an exact command.
14. The first thing the new agent did with its own key was refuse to use it until it knew where it came from.
15. Nothing here changes what the operator can do; it changes what everyone else can.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-agents.md`.


### Team segmentation showcase -- humans and agents as first-class accounts (2026-10-04)

~~Reusable write-up of the team-segmentation model with the screenshots from the day it went live, for lifting into Cowork articles. Every claim here is consistent with `docs/AGENT_SEGMENTATION.md` (the design + runbook), `docs/BOARD_SIGNING.md` (identity signing) and the vault checkpoint `corporatetraveldc/01-Sources/manual/20261004T193737Z.md`. Where this page disagrees with those, those win.~~

> ~~Images live in `docs/images/segmentation/`. They stay in the **private** repo: `scripts/scrub-public-tree.py` drops unreviewed binaries (`.png` is in `BINARY_SKIP_EXTENSIONS`) until the operator adds a file to `REVIEWED_BINARY_OK`. Each image was reviewed before copying: no credential values, no tokens, no private key material. Public key fingerprints, the operator's account name and the hostname are visible by design.~~


### Team segmentation showcase -- humans and agents as first-class accounts (2026-10-04) › In plain language

~~Until today, every automated helper on the dispatch box -- the Claude Code remote-control session, the Cowork scheduled jobs, any future Codex or other vendor agent -- ran as the operator's own Unix account. That meant every agent could read the full production secrets file, every action it took looked like the operator, and the only way to stop a misbehaving agent was to find and kill its process. The segmentation work turns each helper into its **own account** in an **agents** group, and gives human teammates the same treatment in a parallel **humans** group: separate homes, separate resource budgets, a secrets subset each, one SSH key per account whose comment is the account name, and no sudo for anyone but the operator.~~


### Team segmentation showcase -- humans and agents as first-class accounts (2026-10-04) › The model › Groups and accounts

|  | ~~Human teammate~~ | ~~Agent runtime~~ | ~~Operator (`corporatetraveldc`)~~ |
|---|---|---|---|
| ~~Account~~ | ~~personal, `--add-human <name>`~~ | ~~one per agent, `--add-agent <name>`~~ | ~~existing~~ |
| ~~Groups~~ | ~~`ctdc-dev` + `ctdc-ops`~~ | ~~`ctdc-dev` + `ctdc-agents`~~ | ~~`ctdc-dev` (+ wheel)~~ |
| ~~Slice~~ | ~~`humans.slice` (weight 50, 1.5 cores, 2.5-3.5 G)~~ | ~~`agents.slice` (weight 25, 1 core, 3-4 G)~~ | ~~production / app~~ |
| ~~Secrets~~ | ~~`/etc/ctdc-ops/ops-secrets.env` (ops allowlist)~~ | ~~`/etc/ctdc-agent/agent-secrets.env` (agent allowlist)~~ | ~~the production file~~ |
| ~~SSH~~ | ~~their **own** key, comment `name@device`~~ | ~~its **own** key, comment `name@corporatetraveldc-dispatch`~~ | ~~own keys~~ |
| ~~Repo~~ | ~~write via `ctdc-dev` (setgid, `sharedRepository=group`)~~ | ~~same~~ | ~~owner~~ |
| ~~Commit signing / manifest~~ | ~~**no**~~ | ~~**no**~~ | ~~**only signer**~~ |
| ~~sudo~~ | ~~none~~ | ~~none~~ | ~~yes~~ |


### Team segmentation showcase -- humans and agents as first-class accounts (2026-10-04) › The model › One identity per account -- inbound vs signing

~~Every account carries exactly one key in `authorized_keys` (its **inbound** identity) and a signing key at `~/.ssh/<account>_ed25519` (its **signing** identity, registered in `board_signers`, used by `board-sign.sh` and `kill-order.sh`). For most accounts these are the same generated key. For the first agent, `ctdc-agent`, they differ by design: its inbound key is the Cowork client's (private half off-box, comment rewritten to `ctdc-agent@corporatetraveldc-dispatch`), and its signing key was generated on the box afterwards -- see "What the switch caught" below for why that distinction mattered within the hour. Agent accounts also get git `user.name`/`user.email` set to the account, so prepared commits carry the account as author while signing stays operator-only.~~


### Team segmentation showcase -- humans and agents as first-class accounts (2026-10-04) › The model › Liveness is an AND

| ~~Factor~~ | ~~Checked how~~ | ~~Killed by~~ |
|---|---|---|
| ~~(a) **login**~~ | ~~agent: credentials file + refresh token valid + access token refreshed within 7 d; human: shell / one key / not locked / not expired / login within 14 d (3 d grace for new accounts)~~ | ~~`usermod -L`, `chage -E 0`, shell -> nologin, key removed, or just going stale~~ |
| ~~(b) **key**~~ | ~~`board_signers.active = true` for the account~~ | ~~`board-signer-ctl.sh deactivate <name>` -- immediate on the next request~~ |
| ~~(c) **token**~~ | ~~none of the account's minted tokens revoked in the last 24 h~~ | ~~any single revocation~~ |
| ~~(d) **order**~~ | ~~no executable kill order~~ | ~~`scripts/kill-order.sh issue <name> "<reason>"` by an admin, or by a quorum~~ |


### Team segmentation showcase -- humans and agents as first-class accounts (2026-10-04) › The model › Three kill switches, tiered impact, quorum

| ~~Tier~~ | ~~Requires~~ |
|---|---|
| ~~`normal`~~ | ~~alive + (`X-Board-Key` **or** a valid signature from an active signer)~~ |
| ~~`high`~~ | ~~alive + signature **and** a minted token whose lifetime is <= 24 h; the master `BOARD_KEY` never qualifies~~ |
| ~~`cosign`~~ | ~~alive + signature **and** a cosignature over the same message from a different active `admin` signer~~ |


### Team segmentation showcase -- humans and agents as first-class accounts (2026-10-04) › The model › The pamphlet

~~Each account's home directory is its desk, and the first thing on it is a root-owned, read-only `CLAUDE.md` rendered from `scripts/agent-segmentation/onboarding-template.md`: who the account is, which repositories it may enter (discovered from group write bits), where its secrets subset is and how to load it, what it must never do, the liveness table, and the authoritative sources. It is a dated, static mirror of the design -- never live state -- and the account cannot rewrite it.~~


### Team segmentation showcase -- humans and agents as first-class accounts (2026-10-04) › How a new teammate or agent is added

|  | ~~Human teammate `alice`~~ | ~~Agent `ctdc-agent-openai-codex`~~ |
|---|---|---|
| ~~1~~ | ~~`plan.sh --dry-run --add-human alice --ssh-pubkey-file alice.pub`~~ | ~~`plan.sh --dry-run --add-agent ctdc-agent-openai-codex`~~ |
| ~~2~~ | ~~`sudo plan.sh --execute --i-am-the-operator --add-human alice --ssh-pubkey-file alice.pub`~~ | ~~`sudo plan.sh --execute --i-am-the-operator --add-agent ctdc-agent-openai-codex`~~ |
| ~~3~~ | ~~her own key lands in her `authorized_keys` (comment `alice@<device>`)~~ | ~~its own `ctdc-agent-openai-codex_ed25519` is generated; its remote-control unit is installed **disabled**, named `(CTDC-AGENT-OPENAI-CODEX) ...`~~ |
| ~~4~~ | ~~`board-signer-ctl.sh register alice --kind human` (`--role admin` only for an admin)~~ | ~~install the CLI, one-time login + workspace-trust accept, then `board-signer-ctl.sh register ctdc-agent-openai-codex --kind agent` and enable the unit~~ |
| ~~5~~ | ~~`sudo render-onboarding.sh alice --install`~~ | ~~`sudo render-onboarding.sh ctdc-agent-openai-codex --install`~~ |
| ~~6~~ | ~~`verify.sh --user alice`; issue her a personal API token and Nextcloud app password~~ | ~~`verify.sh --user ctdc-agent-openai-codex`; `team-liveness.sh --status` shows it live~~ |


### Team segmentation showcase -- humans and agents as first-class accounts (2026-10-04) › Phrasing bank

1. ~~Every helper on the box -- human or agent -- is its own account, in its own group, with its own key whose comment is its name.~~
2. ~~Liveness is an AND, not an OR: login, key, token and the absence of a kill order must all hold, and any one failing makes the account inert.~~
3. ~~Inert means sessions terminated, the account locked and expired, its signer deactivated and every token it held revoked -- reversible only by the operator, and tokens are never restored.~~
4. ~~Three independent kill switches per principal: revoke the login, revoke the key, revoke a token. Any one collapses the chain.~~
5. ~~An admin can stop a rogue account single-handed; two non-admins can stop one together; two of anyone can stop a rogue admin. The operator is never a kill-order target.~~
6. ~~High-impact actions need a signature **and** a token that expires within a day; the long-lived master key never qualifies on its own.~~
7. ~~Attribution is cryptographic: a board post signed with an account's key is that account's post, and "signed by cowork" is no longer an answer.~~
8. ~~Inbound identity and signing identity are two keys with one name; they may differ, and an agent that finds a key it did not create should stop and ask.~~
9. ~~The pamphlet on an account's desk is a dated mirror of the design, root-owned and read-only; when it disagrees with the live system, the live system wins.~~
10. ~~An account's home is its desk; it enters a repository only when it has been pointed at one.~~
11. ~~The dead-man switch is as unforgiving as it was asked to be -- it killed a three-second-old account on its first run, which is why new accounts now get a grace period.~~
12. ~~Root cannot see rootless podman: anything that touches the registry from a system timer has to drop to the operator.~~
13. ~~Agents never sign, push, build or restart; they stage, test, and hand the operator an exact command.~~
14. ~~The first thing the new agent did with its own key was refuse to use it until it knew where it came from.~~
15. ~~Nothing here changes what the operator can do; it changes what everyone else can.~~
