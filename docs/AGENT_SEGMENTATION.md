# Team segmentation: humans and agents

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.

> Trust model (what each signature, approval and control proves, where it is enforced, and its known gaps): `docs/AGENT_TRUST_MODEL.md`.

> Showcase + screenshots: `docs/TEAM_SEGMENTATION_SHOWCASE.md` (the model in reusable prose, the 2026-10-04 go-live narrated with images under `docs/images/segmentation/`, and a phrasing bank).

The file keeps the name `AGENT_SEGMENTATION.md` because other docs link to it. Since 2026-10-04 its scope covers **human teammates** and **agent runtimes** as two parallel groups. The second brain is the authority for the rules referenced here; this file is the engineering design and runbook. Tooling lives in `scripts/agent-segmentation/`.

## Status at a glance (live, 2026-10-06 18:15Z)

| | State |
|---|---|
| Resource isolation | `agents.slice` / `humans.slice` tracked under `.config/systemd/user/`; installed per account by `plan.sh` |
| Privilege separation | **executed** for agents: five team accounts exist (table below); no human teammate account exists yet (`ctdc-ops` has no members) |
| Registry `/etc/ctdc-accounts.conf` (root:root 0644) | `ctdc-agent-anthropic-claude agent claude 0` · `ctdc-agent-openai-codex agent claude 1` · `ctdc-agent-llama service none 0` · `ctdc-agent-dispatch service none 1` · `ctdc-agent-anthropic-cowork service none 0` |
| Groups | `ctdc-dev` = operator + all five accounts; `ctdc-agents` = all five; `ctdc-ops` = empty |
| Liveness (last run 17:18Z) | claude: live (`refresh=0.0d session=active key=active`); llama + cowork: live (service, `key=active`); codex + dispatch: preloaded, skipped |
| System units | `corporatetraveldc-team-liveness.timer` (hourly), `corporatetraveldc-team-liveness-orders.path`, `corporatetraveldc-skill-grants.timer` (hourly), `corporatetraveldc-llama-council.timer` (hourly :41) -- all active; 0 failed system or user units |
| Agent gateway | `https://agents.example.com` answering (`/.well-known/oauth-authorization-server` 200 with PKCE S256 only) |
| Repo | `corporatetraveldc:ctdc-dev`, `drwxr-sr-x`, `core.sharedRepository=false`; no group-writable non-symlink file in the checkout |

| Account | uid | Kind / login mode | Shell | Notes |
|---|---|---|---|---|
| `ctdc-agent-anthropic-claude` | 1001 | agent / claude | `/bin/bash` | the primary on-box agent; runs its own remote-control unit `(CTDC-AGENT-ANTHROPIC-CLAUDE) Corporate Travel Dispatch Remote Control` (operator's copy of the unit is disabled) |
| `ctdc-agent-anthropic-cowork` | 1002 | service / none | `/usr/sbin/nologin` | Cowork runs in Anthropic's cloud and never SSHes in; identity anchor for the gateway connector |
| `ctdc-agent-openai-codex` | 1003 | agent / claude, **preloaded** | `/bin/bash` | expired, signer inactive until `--activate` |
| `ctdc-agent-llama` | 1004 | service / none | `/usr/sbin/nologin` | the local llama.cpp model as a council/arena participant |
| `ctdc-agent-dispatch` | 1005 | service / none, **preloaded** | `/usr/sbin/nologin` | reserved identity for a rewritten chat panel |

**Designed exception:** the operator's own Claude Code session runs as the operator (uid 1000), outside `agents.slice`, by design -- see "Designed exception" below for why and what still binds it.

## The model in one table

| | Human teammate | Agent runtime | Service identity | Operator (`corporatetraveldc`) |
|---|---|---|---|---|
| Created by | `plan.sh --add-human NAME --ssh-pubkey-file F` | `plan.sh --add-agent NAME [--login-mode claude\|ssh] [--preload]` | `plan.sh --add-service NAME [--preload]` | existing |
| Groups | `ctdc-dev` + `ctdc-ops` | `ctdc-dev` + `ctdc-agents` | `ctdc-dev` + `ctdc-agents` | `ctdc-dev` (+ wheel) |
| Slice | `humans.slice` (weight 50, 150%, 2560M/3584M, 2048 tasks) | `agents.slice` (weight 25, 100%, 3072M/4096M, 1024 tasks) | none (no user manager) | production / app |
| Secrets | `/etc/ctdc-ops/ops-secrets.env` | `/etc/ctdc-agent/agent-secrets.env` | same agent subset | production files |
| Inbound SSH | their own key, comment `name@device` | one key, comment `NAME@corporatetraveldc-dispatch` | none | own keys |
| Signing key | registered with the board | `~/.ssh/NAME_ed25519`, generated on the box | same | own |
| Repo | **read** via `ctdc-dev` | read | read | owner, only writer |
| Commit / manifest signing | no | no | no | only signer |
| sudo | none | none | none | yes |

## Threat model -- what actually happened

1. **Credential exposure (2026-09-22, and 2026-10-03).** An agent passed a Cloudflare API token as an HTTP header via urllib; the library put the header into an exception that became tool output in a session transcript. On 2026-10-03 a 9-character fragment of an NWWS password reached a transcript the same way. Root condition: every agent ran as `corporatetraveldc` and could read `/etc/corporatetraveldc/dispatch-secrets.env` (0600, operator-owned) in full.
2. **Resource exhaustion (2026-10-03).** Agent sessions ran unconfined; an SSH-started agent session lives in `session-*.scope`, a sibling of `user@1000.service`, so it competed at equal weight with all of production. A 14-day `journalctl` scan took load1 from ~20 to 40.09; `thermal-ingest-guard` tripped LOCKDOWN and shed the SWIM ingest containers plus poller/pusher/runner until a manual restore.
3. **Adversarial duel findings (2026-10-04).** Group write on the operator's checkout let an agent account reach root-run scripts, the signing trust anchor, `.git/hooks` and the operator's Stop hook; the agent subset carried `DISPATCH_ADMIN_TOKEN`, `NTFY_TOKEN`, Nextcloud admin credentials and `BOARD_KEY`. All contained (sections below).

## Part 1 -- resource isolation

`.config/systemd/user/agents.slice`:

| Setting | Value | Why |
|---|---|---|
| `CPUWeight` | 25 | production and app slices are at 100 -> agents get a small share under contention, unlimited burst when idle |
| `CPUQuota` | 100% | one core for all agent work in that manager. llama.cpp is capped at 200% (`corporatetraveldc-llama.service`), the rest of production needs about one core; the remaining core keeps load1 under the thermal guard |
| `MemoryHigh` / `MemoryMax` | 3072M / 4096M | reclaim hits agents before production |
| `TasksMax` | 1024 | a runaway fork tree stops here |

`.config/systemd/user/humans.slice`: `CPUWeight=50`, `CPUQuota=150%`, `MemoryHigh=2560M`, `MemoryMax=3584M`, `TasksMax=2048`. The human login shell re-execs itself into the slice once (`CTDC_IN_SLICE` guard in `~/.config/ctdc-env.sh`, written by `plan.sh`).

Membership: each team account gets its own copy of the slice in its own user manager (`plan.sh` installs it). For sessions started in the operator's shell, `scripts/agent-run.sh <claude|codex> [args]` wraps the command in `systemd-run --user --scope --slice=agents.slice`; the operator may alias it in their own profile. Runtime override: `systemctl --user set-property --runtime agents.slice CPUQuota=200%`.

## Part 2 -- privilege separation

### Groups

- `ctdc-dev` -- shared repo-**read** group. Repo dirs are setgid + group-readable, not group-writable; `core.sharedRepository=false`. `plan.sh` step 2 re-asserts this on every run (`chmod -R g+rX,g-w`). `verify.sh` fails an account that can write `scripts/` or `.git/hooks`, or is in `systemd-journal`.
- `ctdc-ops` -- human teammates.
- `ctdc-agents` -- agent runtimes and service identities.

Team accounts prepare changes in their own clone (`git format-patch`) or as a workspace contribution; code lands only through the operator's signed commit. `/etc/corporatetraveldc` stays `root:corporatetraveldc 0750`, so team accounts cannot traverse it; each group gets its own directory (`/etc/ctdc-agent`, `/etc/ctdc-ops`, both 0750 root:group).

### Attribution -- one key per account, two identities

Operator decision 2026-10-04: no shared SSH key across accounts.

- Every account gets an on-box **signing** key `/home/NAME/.ssh/NAME_ed25519` (`ssh-keygen -t ed25519 -N "" -C "NAME@corporatetraveldc-dispatch"`). This key -- not the inbound key -- is registered in `board_signers` and signs board posts, workspace contributions, council requests and kill orders.
- The **inbound** key in `authorized_keys`: for a claude-mode agent created without `--ssh-pubkey-file`, the generated key's public half; for an ssh-mode agent, the client's public key with its comment rewritten to `NAME@corporatetraveldc-dispatch`; for a service, none (`rm -f authorized_keys`).
- Humans supply their own key; the runbook asks for `name@device` so a lost device can be revoked by comment.
- Git author for agent and service accounts is the account (`user.email NAME@corporatetraveldc-dispatch.invalid`). GPG signing stays operator-only.
- `verify.sh` enforces: one inbound key per non-service account whose comment starts with the account name; no inbound key for services; signing key present; signer active (inactive while preloaded); no two accounts sharing a public key; no team key also in the operator's `authorized_keys`; no `claude-cowork-dispatch` comment left in the operator's `authorized_keys`.

### Secrets: two allowlists

`secrets-subset.py --profile agent|ops` copies allowlisted **names** verbatim from `/etc/corporatetraveldc/dispatch.env` + `dispatch-secrets.env` into the group subset (root:group 0640). Production files are never modified; re-run plan step 3 after any rotation. `--check`/`--dry-run` write nothing and never print a value.

| Profile | Names (`?` = optional) |
|---|---|
| agent (`secrets-allowlist-agent.txt`) | `NTFY_AGENT_TOKEN?`, `LLAMA_BASE_URL?`, `OLLAMA_BASE_URL?` (deprecated alias), `DISPATCH_PG_HOST`, `DISPATCH_PG_PORT`, `DISPATCH_PG_DB`, `DISPATCH_PG_RO_USER?`, `DISPATCH_PG_RO_PASSWORD?` |
| ops (`secrets-allowlist-ops.txt`) | `NTFY_OPS_TOKEN?`, `LLAMA_BASE_URL?`, `OLLAMA_BASE_URL?`, `DISPATCH_PG_HOST`, `DISPATCH_PG_PORT`, `DISPATCH_PG_DB`, `DISPATCH_PG_RO_USER?`, `DISPATCH_PG_RO_PASSWORD?` |

Removed from the agent subset on 2026-10-04 (duel X1/H4), each with its reason recorded in the allowlist file: `DISPATCH_ADMIN_TOKEN` (reached the operator's approval gate), `NTFY_TOKEN` (role=admin ntfy user that can read the approval pushes), `NEXTCLOUD_ADMIN_USER` / `NEXTCLOUD_APP_PASSWORD` (raw WebDAV admin over the whole vault), `BOARD_KEY` (posts under any name). Never in either subset: `DISPATCH_PG_PASSWORD` and every feed, Cloudflare or webhook credential. The read-only Postgres role (`DISPATCH_PG_RO_*`) is still optional because it has not been created [UNVERIFIED: subset contents are root:group 0640 and were not read].

Accounts load the subset with `WITH_DISPATCH_ENV_FILES=<subset> scripts/with-dispatch-env.sh <cmd>` (the plan writes that export into `~/.config/ctdc-env.sh`), never `source`.

Containers are a separate boundary: since 2026-10-05 every first-party quadlet reads its own scoped file `/etc/corporatetraveldc/svc/<service>.env` (generated by `scripts/service-env/`). At HEAD 53 tracked quadlets reference `svc/` and none references `dispatch-secrets.env`; nine scoped files exist (`acars-watcher`, `amtrak-tracker`, `demo`, `execstandard-verifier`, `ingest`, `poller`, `pusher`, `runner`, `web`).

### No secret on a command line

`/proc/<pid>/cmdline` is world-readable here (no `hidepid`), so tokens and the Postgres password are passed on a private fd (`authhdr`, `-H @<(printf ...)`) or in the environment, never in argv. A contract test bans the old patterns.

### Root never executes the checkout

The scripts root runs are installed to `/usr/local/libexec/ctdc/` (root:root) by `scripts/install-root-copies.sh`, which verifies the signed manifest first, refuses bytes that differ from the signed hash, and records hashes in `/usr/local/libexec/ctdc/.ctdc-installed` (`--check` reports stale copies). Installed today: `team-liveness.sh`, `skill-grants.sh` + `skill_grants.py`, `watchdog.sh`, `watchdog-tune.sh`, `renew-tailscale-cert.sh`, `tailscale-cert-refresh-nts.sh`, `stall-monitor.py`, `lockdown.sh`, `restore-network.sh`, `cf-honeypot-ban.sh`, `cf-honeypot-notes.sh`, `installed-check.sh`, `lib/`. Order after every sign: `scripts/sign-manifest.sh` -> commit -> `sudo scripts/install-root-copies.sh` -> `sudo systemctl daemon-reload`.

`board-signer-ctl.sh` stays in the checkout because root calls it only through `runuser` as the operator (the signer registry is in the operator's rootless Postgres container, which root cannot see). `corporatetraveldc-llama-council.service` also runs from the checkout, but as `ctdc-agent-llama`, after an `ExecStartPre` manifest check of the two files it uses.

Trust pin: `verify-manifest.sh` reads the signing fingerprints literally and prefers the root-owned `/etc/corporatetraveldc/signing-pin` (present, root:root 0644) over `security/signing.env`.

## Runbook (`scripts/agent-segmentation/`)

Everything privileged is dry-run by default and refuses to execute without root **and** `--i-am-the-operator`. One operation per invocation. Agents only relay these commands.

| File | Role |
|---|---|
| `plan.sh` | shared steps 1-3 (groups; repo group-read; both subsets) always first, then the account steps for exactly one of: `--add-agent NAME [--login-mode claude\|ssh] [--ssh-pubkey-file F] [--preload]`, `--add-service NAME [--preload]`, `--add-human NAME --ssh-pubkey-file F`, `--activate NAME`, `--rehome-inbound-key FROM TO`, `--rename-account FROM TO`, `--convert-to-service NAME`. `--only=N,M` selects steps by number for the current invocation (read the dry-run first). Fails fast on the first failed command and prints the step to re-run |
| `secrets-allowlist-agent.txt` / `secrets-allowlist-ops.txt` | the names each group may read, with a reason per name |
| `secrets-subset.py` | builds a subset; `--check`/`--dry-run` write nothing |
| `verify.sh [--user NAME]` | run as the operator (uses `sudo -u`); per-account checks 1-6f and fleet checks 7-8b, refined per kind from the registry; exit 1 on any FAIL |
| `rollback.sh` | `--remove-agent NAME` / `--remove-service NAME` / `--remove-human NAME` / `--shared`; removes the registry line; prints (does not run) the signer deactivate + `revoke-tokens` |
| `render-onboarding.sh NAME [--install]` | renders `onboarding-template.md` to `/home/NAME/CLAUDE.md` (root:NAME 0444) |
| `publish-pamphlet-to-vault.sh NAME` | for a cloud identity: the same pamphlet, scrub-gated, written to `01-Sources/personal-notes/Series/agents/NAME/PAMPHLET.md` |

### Account kinds

The registry line is `name kind login_mode preloaded`. Group membership decides *what* an account may touch; the registry decides *how it is kept alive*. An account missing from the registry defaults by group (ctdc-agents -> `agent claude 0`, ctdc-ops -> `human ssh 0`).

| kind / login mode | Shell, slice, units | Login factor | Created by |
|---|---|---|---|
| `agent` / `claude` | bash; `agents.slice`; own remote-control unit, installed **disabled** (enable after the one-time `sudo -u NAME -i claude` login and the workspace-trust accept) | `~/.claude/.credentials.json`: refresh token unexpired AND access token refreshed within 7 d; once the account has a remote-control unit, the unit must be active or a `claude` process must run as the account | `--add-agent NAME` |
| `agent` / `codex` (2026-10-08) | bash; `agents.slice`; Codex CLI copied from a pinned digest; own `corporatetraveldc-codex-remote-control.service`; no Claude unit | `~/.codex/auth.json` holds a refresh token AND `last_refresh` within 14 d AND the Codex unit active or a `codex` process running as the account | `--add-agent NAME --login-mode codex`, or `--activate NAME --login-mode codex` |
| `agent` / `ssh` | bash; `agents.slice`; no Claude CLI; one inbound key (the client's) | last SSH login within 7 d (`LIVENESS_SSH_MAX_IDLE`); PAM state file -> `lastlog2` -> sshd journal | `--add-agent NAME --login-mode ssh --ssh-pubkey-file F` |
| `service` / `none` | `/usr/sbin/nologin`; no linger, no user manager, no inbound key | none | `--add-service NAME` or `--convert-to-service NAME` |
| `human` / `ssh` | bash; `humans.slice`; their key | real shell, exactly one key commented `NAME@`, not locked/expired, SSH login within 14 d | `--add-human NAME --ssh-pubkey-file F` |

`--preload` creates an agent or service **inert on arrival**: `chage -E 0`, no linger, registry `preloaded=1`, signer registered then deactivated (the plan prints that command). The liveness switch logs it as `preloaded` and skips it. `--activate NAME` lifts the expiry, unlocks, enables linger (not for services), flips the registry to 0 and prints the signer `activate` plus the mode's next step. The new-account grace (3 d) is measured from the home directory's birth time (`created_at()` in `team-liveness.sh`: `stat -c %W`), not from activation. An account preloaded more than 3 days before `--activate` therefore has no grace: a claude-mode agent without fresh credentials, or without its signer re-activated, is made inert on the next hourly run. Run the login and `board-signer-ctl.sh activate` in the same sitting as `--activate` [finding: `ctdc-agent-openai-codex` was created 2026-10-05, so its grace has lapsed after 2026-10-08].

`--convert-to-service NAME` (2026-10-05, used for Cowork): stops the account, sets the nologin shell, removes `authorized_keys`, registry `service none 0`; the signing key and signer stay.

`--rename-account FROM TO` (2026-10-05): stops the account, renames passwd/group/home (uid unchanged), key filenames and comments, git author, Claude CLI per-path state and absolute symlinks into the old home, registry line, skill grants, sudoers, liveness state files and the remote-control label, then starts `user@UID` explicitly. The board side is a separate operator step: `scripts/board-signer-ctl.sh rename FROM TO` (signer, token labels, workspace grants).

### Codex agents (login mode `codex`, 2026-10-08)

Codex runs as its own agent account, not as the operator. Until 2026-10-08 it ran in a `screen` session and two `codex app-server` daemons under the operator account: the operator-as-agent exception (`docs/AGENT_TRUST_MODEL.md` §0).

- **Liveness** (`team-liveness.sh`, mode `codex`). The account is live while all of these hold:
  - `~/.codex/auth.json` holds a refresh token;
  - its `last_refresh` is within `LIVENESS_CODEX_MAX_STALE` (14 d; Codex refreshes about every 8 days);
  - the account's `corporatetraveldc-codex-remote-control.service` is active, or a `codex` process runs as it.

  Only the timestamp and the presence of a refresh token are read, never a token value. Claude credentials do not keep a codex-mode account alive, and the reverse also holds.
- **Install, with no download.** `plan.sh --activate NAME --login-mode codex` (or `--add-agent NAME --login-mode codex`):
  - copies the Codex CLI from the operator's standalone install, verified against `config/codex/artifacts.sha256`; it never runs the vendor's `curl | sh` installer;
  - installs the remote-control unit from `scripts/agent-segmentation/units/`, disabled;
  - removes any Claude remote-control unit;
  - sets the registry line to `agent codex`.
- **Login and pairing,** in the same sitting as activation:
  - `sudo -u NAME -i codex login --device-auth`, approved on a phone or laptop;
  - then enable the unit;
  - then `sudo -u NAME -i codex remote-control pair` and enter the code under "Pair manually" in the ChatGPT app's Remote tab.

  The ChatGPT account the CLI logs into is the vendor link. The Unix account is the on-box identity, slice and limits.
- **Pamphlet.** `render-onboarding.sh NAME --install` writes the same root-owned pamphlet as `CLAUDE.md`, `AGENTS.md` and `~/.codex/AGENTS.md`. Codex reads `AGENTS.md`.
- **Network.** The daemon listens on Unix sockets only (checked 2026-10-08: no TCP listener) and reaches OpenAI's relay outbound. Its app-server is marked experimental, and a non-loopback listener would accept unauthenticated connections, so none is configured.
- **Self-update.** The daemon updates itself under `~/.codex/packages/`. That is a runtime download outside the pin, recorded in `docs/REPRODUCIBLE_BUILDS.md`.
- **Verification.** `verify.sh --user NAME` checks 4d–4g: the Codex unit is active, no Claude unit is installed, no `codex` process runs as the operator, and `AGENTS.md` is installed.
- **Headless pairing worked, with no desktop app and no display (observed 2026-10-08).**
  - **Setup:** Codex 0.160.0 runs on this Pi as a headless CLI. The ChatGPT desktop app was never installed here, and no X11 or virtual display was set up and torn down for it. The daemon was started with `codex remote-control start` under a systemd unit.
  - **Result:** the phone paired over OpenAI's relay. It first paired against the operator-account daemon. After the hand-over it drove the agent account, with no new pairing needed: the agent logged into the same ChatGPT account.
  - **Context:** this closely matched the public reports, but nothing was changed to make it work. [openai/codex#31183](https://github.com/openai/codex/issues/31183) described headless Linux CLI hosts with no supported pair or re-pair path (desktop-app QR/PIN only). [#35928](https://github.com/openai/codex/issues/35928) notes the later `codex remote-control pair` command, while the main docs still describe the desktop app as required. [#50660](https://github.com/openai/codex/issues/50660) reports headless pairing working on 0.160.0.
  - **Interpretation:** most likely OpenAI shipped the headless path in a recent CLI release. A transient cannot be ruled out from one success.
  - **Gaps:** `codex remote-control pair` on the agent account timed out once, probably because the phone was already paired to this host under the same account. Watch [#47844](https://github.com/openai/codex/issues/47844): `remote-control` fails under umask 0002 after 0.156.1. The systemd unit runs under the default 0022.

### ChatGPT (cloud reviewer; read or write decided by the vendor plan)

ChatGPT gets a **service** identity, the same as Cowork: `plan.sh --add-service ctdc-agent-openai-chatgpt`, a signer with kind `service`, and a gateway connector `agent-gateway.sh add-connector chatgpt ctdc-agent-openai-chatgpt openai`. It is linked from ChatGPT's developer mode, with the approval signed from a laptop.

Operator decision (2026-10-08, revised the same day): ChatGPT is a **secondary reviewer**. Its connector gets the **same tool set as Cowork**:
- **read:** board read, research read and list, status;
- **write:** board post, workspace contribute, council request.

**The ChatGPT plan decides read versus write.** The plan is ChatGPT Plus, and OpenAI's own documents disagree on whether Plus developer mode performs write actions. Whatever the plan will not call simply goes unused, and a plan change needs nothing on this side.

**Observed 2026-10-08: ChatGPT Plus performs writes through the connector.** `ctdc-agent-openai-chatgpt` posted its check-in to the board (`coord` thread, 11:33:50Z) through `/mcp/chatgpt`. This was after its first read failed only because its pamphlet had not been published to the vault (see below). So on this account, Plus developer mode is read **and** write, despite OpenAI's help center describing Plus as read/fetch only.

The agent rules still apply on every plan: drafts only, never publish, never approve; create-only signed contributions; the service identity has its own revocation and kill switch; and since security review 07, every gateway tool call is an `agent.tool.call` event in the hash-chained audit log (connector, account, tool, outcome, argument hash). <del>every call audited to the service identity</del> SUPERSEDED 2026-10-08: that was written before it was true. Until review 07, tool calls were executed but only the last-call time was kept.

**Cloud agents read their pamphlet from the vault.** The gateway's MCP instructions tell every connected agent to `research_read 01-Sources/personal-notes/Series/agents/<account>/PAMPHLET.md`. `render-onboarding.sh --install` writes only the account's home directory, which a cloud agent cannot see, so `scripts/agent-segmentation/publish-pamphlet-to-vault.sh <account>` is part of onboarding for every gateway-connected account. That includes on-box Codex, whose gateway session follows the same instruction.

<del>Operator decision (2026-10-08): ChatGPT is a **secondary reviewer and needs read access only**. The plan is ChatGPT Plus. OpenAI's own documents disagree on whether Plus developer mode allows write actions, so the gateway, not the vendor plan, decides what the connector may do.</del> SUPERSEDED 2026-10-08: the operator chose to let the plan decide.

### Adding accounts (current commands)

The five accounts above already exist; these are the commands for the next one. Signer registration runs as the **operator** (not under sudo); the plan prints the exact line, which copies the root-only pubkey to a temp file with `sudo cat`.

```
# agent (claude mode)
scripts/agent-segmentation/plan.sh --dry-run --add-agent ctdc-agent-<vendor>-<product>
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-agent ctdc-agent-<vendor>-<product>
#   then: install the CLI for the account, sudo -u ACCOUNT -i claude (login + trust), enable its remote-control unit
#   then: the register line the plan printed (board-signer-ctl.sh register ACCOUNT <pubkey> --kind agent)

# service identity
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-service ctdc-agent-<name>
#   then: board-signer-ctl.sh register ACCOUNT <pubkey> --kind service --role service

# human teammate
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-human alice --ssh-pubkey-file /path/alice.pub
scripts/board-signer-ctl.sh register alice --kind human        # --role admin only for an admin
#   then: a personal auth_tokens row (own tier) and a personal Nextcloud app password

# activate a preloaded account
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --activate ctdc-agent-openai-codex
scripts/board-signer-ctl.sh activate ctdc-agent-openai-codex activated

# every time
sudo scripts/agent-segmentation/render-onboarding.sh ACCOUNT --install
scripts/agent-segmentation/verify.sh --user ACCOUNT
sudo systemctl start corporatetraveldc-team-liveness.service   # creates the root-owned orders/<ACCOUNT>/ dir
```

After any segmentation change (account added/removed/re-keyed, sudoers.d entry, authorized_keys edit), re-record the stack-refresh audit baselines once, as the operator: `REBASELINE=1 scripts/stack-refresh.sh --tripwire --dry-run --now`.

The `ctdc-agent` literal branches in `plan.sh` (cowork-key move, sudoers `50-ctdc-agent`, remote-control hand-over) and `rollback.sh` are the one-time 2026-10-04 stage A/B/C bootstrap. That account no longer exists under that name; the branches never match again. The stage history is in `docs/TEAM_SEGMENTATION_SHOWCASE.md`.

Rollback, per account, last step first; `--shared` only once no team account remains:

```
sudo scripts/agent-segmentation/rollback.sh --execute --i-am-the-operator --remove-agent NAME   # or --remove-service / --remove-human
sudo scripts/agent-segmentation/rollback.sh --execute --i-am-the-operator --shared
```

## Liveness / dead-man switch (`scripts/team-liveness.sh`)

Runs as root from the installed copy: `corporatetraveldc-team-liveness.timer` (hourly) and `corporatetraveldc-team-liveness-orders.path` (reacts to a filed kill order). It evaluates every member of `ctdc-agents` and `ctdc-ops`, never the operator or root.

**Liveness is an AND.** An account stays alive only while every factor holds:

| Factor | Checked how | Killed by |
|---|---|---|
| (a) login | per kind (table above); services have none; preloaded accounts are skipped | going stale, `usermod -L`, `chage -E 0`, shell -> nologin, key removed |
| (b) key | `board_signers.active = true` (`board-signer-ctl.sh show`); a missing row is tolerated only inside the 3-day new-account grace | `board-signer-ctl.sh deactivate NAME` |
| (c) token | none of the account's minted tokens (board tokens labelled with it, API `auth_tokens` with that `user_label`) was revoked in the last 24 h | `board-signer-ctl.sh revoke-tokens NAME` or any single revocation |
| (d) order | no executable kill order | `scripts/kill-order.sh issue NAME "<reason>"` |

Thresholds (env overrides): `LIVENESS_AGENT_MAX_STALE` 7 d, `LIVENESS_SSH_MAX_IDLE` 7 d, `LIVENESS_HUMAN_MAX_IDLE` 14 d, `LIVENESS_NEW_ACCOUNT_GRACE` 3 d, `LIVENESS_REVOCATION_LOOKBACK` 24 h, `LIVENESS_ORDER_MAX_AGE` 7 d. Only the two expiry fields of a credentials file are read, never a token value. "Locked" means a deliberate `usermod -L` (shadow `!` in front of a real hash); `passwd -S` was dropped on 2026-10-05 because every passwordless account reports locked. `chage -E 0` is the real switch.

**Inert** (with `--execute --i-am-the-operator`): terminate sessions, stop the user manager's units, disable linger, `usermod -L`, `chage -E 0`, deactivate the signer, revoke all minted tokens, write `/var/lib/corporatetraveldc/team-liveness/inert/NAME`, ntfy p4 (p5 for a kill order). `--reactivate NAME --i-am-the-operator` restores login, linger and signer only; tokens are never restored; pending orders against it are archived as superseded.

**Registry unavailable = HOLD**: nobody is made inert and nobody passes the key factor; the unit exits 2 and sends ntfy p4.

The run also writes the operator's last login to `/var/lib/corporatetraveldc/team-liveness/operator-last-login` (root 0644), which the agent gateway's operator dead-man reads.

`--status` prints a table with `KIND` and `MODE` columns; `--user NAME` evaluates one account; run without sudo it reports root-only factors as `unreadable-unprivileged`.

### Kill orders and quorum

Any team account can issue an order (`kill-order.sh issue`, `ssh-keygen -Y sign`, namespace `corporatetraveldc-kill`) into the drop box `/var/lib/corporatetraveldc/team-liveness/orders/` (root:ctdc-dev, mode 3775). Only root executes, after verifying the signature against `board_signers`, that the issuer is alive and active, the order is under 7 days old and not post-dated, issuer != target, and the target is not the operator.

| Target | Executes when |
|---|---|
| role `member` / `service`, or kind `agent` | one valid order from an `admin`, or `QUORUM_NON_ADMIN` (2) distinct non-admin issuers |
| role `admin` (not the operator) | `QUORUM_FOR_ADMIN` (2; set 3 for a three-man rule) distinct issuers of any role |
| the operator | never via an order |

On `--execute` root creates a root-owned `orders/<target>/` for every team account (an issuer-created target dir is a squat and its orders are rejected), moves each order into the root-only `/var/lib/ctdc-liveness/staging/` (refusing symlinks, non-regular files, files over 64 KB, files not owned by a `ctdc-dev` member, more than 50 per run), and archives executed/rejected/superseded orders under `/var/lib/ctdc-liveness/orders-archive/`. Both root-only trees sit outside `/var/lib/corporatetraveldc` on purpose: 53 rootless quadlets bind-mount that tree with `:z`, and an unreadable root 0700 directory inside it fails every container start (2026-10-04 20:05 ET incident, exit 126). Anything under `/var/lib/corporatetraveldc` must stay readable by the operator. `kill-order.sh list` shows state.

Tests: `tests/scripts/test_team_liveness.py` (63 tests; drives the verdict logic through `LIVENESS_FAKE_ROOT`), `tests/scripts/test_segmentation_plan_dryrun.py`, `tests/scripts/test_wave2_boundary.py`.

## Board identity and impact tiers

`src/web/main.py` `BOARD_AUTH_POLICY`; signing format and verifier in `docs/BOARD_SIGNING.md` and `src/common/board_sign.py`. Roles (`admin` / `member` / `service`) and kinds (`human` / `agent` / `service`) live in `board_signers` (migrations `0066`, `0067`). The signed message covers method, path **with the raw query string**, timestamp and body hash; each signature is accepted once (`ReplayCache`), replay window 300 s.

| Tier | Requires | Routes using it at HEAD |
|---|---|---|
| `key` | `X-Board-Key` | every route not listed |
| `normal` | alive + (`X-Board-Key` or a valid signature from an active signer) | `POST/GET /api/v1/board`, `GET /api/v1/vault/research[/list]`, `GET /api/v1/approvals`, `GET /api/v1/council` |
| `signed` | the account's own signature; a shared key never qualifies | `POST /api/v1/council`, `POST /api/v1/council/close`, `POST /api/v1/workspace/contribute` |
| `high` | signature AND a minted token with lifetime <= 24 h (`BOARD_HIGH_TOKEN_MAX_TTL`); the master `BOARD_KEY` never qualifies | **none yet** -- implemented and tested, reserved for future delete/pin/ack |
| `cosign` | signature AND an admin's cosignature over the same message | **none yet** |

Tests: `tests/web/test_board_signer.py` (22).

## Skills: grants and clawback

`scripts/skill-grants.sh` (wrapper over `scripts/lib/skill_grants.py`), applied hourly by root via `corporatetraveldc-skill-grants.timer` from the installed copy. Tests: `tests/scripts/test_skill_grants.py` (28).

Catalog, three sources:

| Source | Bytes from | Trust |
|---|---|---|
| signed | `skills/<name>/` (8 at HEAD: `corporatetraveldc-dispatch-ops`, `flight-hifi-track`, `hub-arrivals-lookup`, `linkedin-export-analyzer`, `morning`, `nec-train-hifi-track`, `overwater-adsb-handoff-track`, `second-brain-remember`) | each file checked against `MANIFEST.sha256` |
| vendor | the operator's `~/.claude/skills/<name>` | pinned by tree hash in `skills/vendor-pins.txt` (16 pins); bytes not committed (some are proprietary-licensed); a copy that does not match its pin is withheld |
| project | `.claude/skills/` (`dispatch-context-guardian`, `personal-export-analysis`) | auto-loaded in the repo; clawback is `skillOverrides: "off"` in the agent's `settings.json` |

`skills/skill-capabilities.txt` records what each skill needs beyond its text; a test fails if a grantable skill has no entry.

Grants: `/etc/ctdc-skill-grants.conf` (root 0644, parsed, never sourced), lines `grant|deny <account|*> <skill|*> [task=ID] [until=ISO-8601+offset]`. Deny beats grant; expired lines lapse both ways; naive timestamps are rejected. Live file: `grant <account> *` for `ctdc-agent-anthropic-claude`, `ctdc-agent-anthropic-cowork` and `ctdc-agent-openai-codex` (no deny lines). The last apply that ran reported `2 agent account(s), 0 finding(s)`; every hourly run on 2026-10-06 13:07Z-17:13Z HELD because the checkout carries an uncommitted edit (`scripts/stack-refresh.sh`) and fails verify-manifest [finding]. Missing/unreadable grants file, or a checkout that fails verify-manifest = **HOLD** (nothing changes).

Apply, per agent account: keeps `~/.claude/skills` root-owned (quarantining an agent-owned, symlinked or non-directory one to `~/.claude/.ctdc-skills-quarantine/`); installs each granted skill as a root-owned read-only copy with a `.ctdc-grant` marker; removes revoked/expired copies; restores in-place edits (a finding); as the agent with `O_NOFOLLOW` fds, merges `skillOverrides` and the context-guardian Stop + SessionStart hooks into `settings.json`. Findings -> ntfy p4; last report `/var/lib/ctdc-liveness/skill-grants.json`.

Limit: a skill is instructions. Clawback removes it from the listing; it cannot stop an agent from doing the same steps by hand. The hard boundary is the "needs" column: secrets subset, signer, routes, vault writes only through the contribution route.

```
sudo scripts/skill-grants.sh grant ctdc-agent-anthropic-claude '*'
sudo scripts/skill-grants.sh deny  ctdc-agent-llama second-brain-remember
sudo scripts/skill-grants.sh deny  '*' docx --until 2026-10-08T00:00-04:00
scripts/skill-grants.sh list [account]
sudo systemctl start corporatetraveldc-skill-grants.service
```

## Approvals, council/arena and the shared workspace

Code: `src/common/governance.py`, routes in `src/web/main.py` (`/api/v1/approvals*`, `/api/v1/council*`, `/api/v1/workspace/contribute`), migration `0069`; tests `tests/web/test_signed_approvals.py` (21).

**Signed approvals.** An approval is a human's SSH signature (namespace `corporatetraveldc-approval`) over one request's canonical text: id, action, kind, requester, expiry and sha256 of the exact command or spec. It verifies only against `approval_signers` -- a human's **approval key**, deliberately not the board key (the operator's board key has no passphrase, so any operator-uid process can use it). `approver-ctl.sh register` refuses a key that opens with an empty passphrase; the server refuses a key equal to any board key, self-approval, and a signer whose board row is not an active `kind=human`. `scripts/approve.sh list|show|allow|deny ID` (off-box: `approve.sh message` + `approve.sh submit`). Phone pushes only notify and offer **Deny**; no tap or link can allow (`db.resolve_approval_request` refuses `allow`). Approval kinds in use include `council`, `connector-link`, `connector-hold`, `gateway-thaw`, `console-login`.

**Council / arena.** `scripts/council.sh request --mode council|arena --subject S --participant A[:required] ...` (signed with the requester's board key) creates a convene that does nothing until a human signs it; `council.sh convene` = request + approve. On approval each participant gets the convene on the board's `council` thread and a workspace write grant for task `council-<id>` until the deadline. Arena is blind until a human-signed close. A `:required` miss is reported by `board_sweep`, never a kill.

**Shared workspace.** Everyone reads through the research routes (scope: `01-Sources/personal-notes/Series/` plus `04-Syntheses/`, `02-Concepts/`, `00-Inbox/cross-link-findings/`, `01-Sources/manual/`). Writes only via `POST /api/v1/workspace/contribute` (client `scripts/workspace-contribute.sh`): signed, scrub-gated, server-chosen path `Series/contributions/<task>/<account>/<UTC>-<slug>.md`, WebDAV create-only (`webdav_client.put_create_only`). No delete, overwrite or publish route. Grants: `scripts/workspace-grants.sh grant|deny <account|*> [--task ID] [--until ISO+offset]`, `revoke`, `lock`/`unlock`; deny wins; migration 0069 seeds `grant * *`.

**Publisher guardrail.** Agents draft; they never publish. No team account holds a route or credential to Executive Standard, Substack or the members site.

**llama as participant.** `corporatetraveldc-llama-council.{service,timer}` runs `scripts/llama-council-responder.py` as `ctdc-agent-llama` (hourly at :41 + up to 5 min jitter; `User=ctdc-agent-llama`, `ProtectSystem=strict`, `NoNewPrivileges`). At most one convene per run; skips when load1 >= 12 (`LLAMA_COUNCIL_LOAD_MAX`) or the model server is unreachable. In the three days to 18:15Z it skipped 30 of 31 runs on load (load1 17-19) and found no convene on the other [finding: as deployed it effectively never participates during the day].

## Agent gateway: cloud agents over OAuth 2.1 + MCP

`https://agents.example.com` -- an OAuth 2.1 authorization server plus one remote-MCP endpoint per connector, `POST /mcp/<slug>` -> a team account. Code: `src/common/agent_gateway.py`, `src/web/routes/agent_gateway.py`; migrations `0071` (connectors, clients, pending, connections, tokens) and `0073` (`agent_gateway_settings`, the freeze flag); tests `tests/web/test_agent_gateway.py` (15). The hostname must not sit under a Cloudflare Access application; nginx exposes only the OAuth, MCP and `.well-known` paths.

- **Connectors**: `scripts/agent-gateway.sh add-connector SLUG ACCOUNT VENDOR` (slug `^[a-z0-9][a-z0-9-]{1,31}$`); `connectors` lists them. The Cowork connector (slug `cowork` -> `ctdc-agent-anthropic-cowork`) is linked and serving MCP tools [UNVERIFIED: the connector table was not queried; evidence is the working claude.ai connector in this session's tool list]. Per-agent slugs for on-box Claude Code, Codex, llama and ChatGPT were requested by the operator on 2026-10-05 and are not set up [UNVERIFIED for the same reason].
- **Linking**: the vendor's OAuth flow lands on a consent page that shows an approval id; the link completes only when the operator signs it (`approve.sh allow`, kind `connector-link`). PKCE S256 is mandatory; token endpoint auth `none`; clients via Dynamic Client Registration or a Client ID Metadata Document; loopback `http://localhost:<port>` redirects accepted for native clients (RFC 8252), https elsewhere. Pending authorizations wait 15 min; codes live 10 min.
- **Tokens**: access 1 h, rotating refresh; our refresh grant idles out after 30 days without a hold.
- **Our side is authoritative**: every renewal and tool call checks the account's signer is active (liveness, `deactivate`, `revoke-tokens` all end the chain) and the operator dead-man (operator login within 14 d, read from `operator-last-login`; refuses without revoking).
- **Vendor silence** for 7 days marks the session `dormant` (notice only). `agent-gateway.sh hold SLUG --days N` (N <= 90, operator-signed `connector-hold`) keeps our side alive through a vendor lapse.
- **Tools** (attributed to the account): `status`, `board_read`, `board_post`, `research_list`, `research_read`, `workspace_contribute`, `council_request`.
- **Kill switch**: `agent-gateway.sh revoke SLUG|ACCOUNT`, `disable|enable SLUG`, and `kill-all` (freeze: every OAuth/MCP endpoint answers 503, every connection revoked, every connector disabled). `thaw` needs the operator's signature (kind `gateway-thaw`); connectors are then re-enabled and each vendor re-links. Also on the phone console (`docs/OPERATOR_CONSOLE.md`).
- **Vendor limits (checked 2026-10-05)**: ChatGPT Pro = MCP read/fetch only; ChatGPT agent mode does not use custom MCP apps; a Claude connector belongs to the Claude account, so every Claude surface using it acts as the same identity; Claude connector auth settings cannot be edited after adding (remove + re-add).

Cloud pamphlet: `publish-pamphlet-to-vault.sh ACCOUNT` writes `Series/agents/ACCOUNT/PAMPHLET.md`. Its prepended "cloud client" block still tells the client its credential is a board token sent as `X-Board-Key` with a 12-hourly refresh; for Cowork that is stale (Cowork now reaches the platform through the gateway connector) [finding].

### Designed exception: the operator's own agent session runs outside `agents.slice`

The operator's interactive Claude Code session runs as the operator account
(uid 1000) in the operator's SSH session scope, not in `agents.slice` and not
under a team account. This is a deliberate design choice (operator, 2026-10-06),
not a gap:

- **Why.** That session is the operator's hands at the keyboard: it stages and
  tests changes across all three repos, reads live state for incident response
  (the 2026-10-06 18:47Z rollout outage was diagnosed and recovered from it),
  and prepares the relays the operator runs. Confining it to the agents' 1-core
  quota and 4 GiB memory ceiling would starve exactly the work that has to win
  under contention, and moving it to a team account would split the operator's
  own working tree from the operator's own tooling.
- **What still binds it.** It holds no sudo (every privileged step is an
  operator-run `bash <<'RELAY'` block); it cannot sign the manifest, commits
  or approvals (GPG and the passphrase-protected approval key prompt the human);
  every tracked change it makes is unsigned until the operator signs, so the
  integrity sweep, `verified-exec` and the stack-refresh audit treat its edits
  as untrusted until then; and it never publishes or approves.
- **What it can reach that team accounts cannot.** The operator's files,
  including `dispatch-secrets.env` and the operator's board key
  (`corporatetraveldc_ed25519`, no passphrase -- see the finding in
  `docs/FINDINGS_2026-10-06.md`). An adversarial reviewer should treat this
  session as equivalent to the operator account itself, bounded by the human
  signing step, not by the slice.
- **Opt-in confinement.** When the operator wants an agent session to run under
  the agent limits from the operator's shell, `scripts/agent-run.sh <claude|codex>`
  starts it in `agents.slice`.

## Open questions for the operator

- Read-only Postgres role (`DISPATCH_PG_RO_*`): name and table set; still not created.
- Per-agent gateway slugs (on-box Claude Code, Codex, llama, ChatGPT) and whether on-box Claude Code must be kept off the Cowork claude.ai connector.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-agents.md`.


### Team segmentation: humans and agents

> ~~Showcase + screenshots: `docs/TEAM_SEGMENTATION_SHOWCASE.md` (the model in reusable prose, the 2026-10-04 go-live narrated with images under `docs/images/segmentation/`, and a phrasing bank for articles).~~

~~(File name kept as `AGENT_SEGMENTATION.md` -- links exist. Scope widened 2026-10-04 by operator direction: the separation covers **human teammates** and **agent runtimes** as two parallel groups, not agents alone.)~~

~~Status 2026-10-04: **resource isolation shipped** (`agents.slice`, and `humans.slice` tracked for the human group), **privilege separation designed and tooled for both groups, not executed** -- no team account exists yet; see the Runbook. The second brain is the authority for the rules referenced here; this file is the engineering design.~~


### Team segmentation: humans and agents › The model in one table

|  | ~~Human teammate~~ | ~~Agent runtime~~ | ~~Operator (`corporatetraveldc`)~~ |
|---|---|---|---|
| ~~Account~~ | ~~personal, `--add-human <name>`~~ | ~~one per agent, `--add-agent <name>`~~ | ~~existing~~ |
| ~~Groups~~ | ~~`ctdc-dev` + `ctdc-ops`~~ | ~~`ctdc-dev` + `ctdc-agents`~~ | ~~`ctdc-dev` (+ wheel)~~ |
| ~~Slice~~ | ~~`humans.slice` (weight 50, 1.5 cores, 2.5-3.5 G)~~ | ~~`agents.slice` (weight 25, 1 core, 3-4 G)~~ | ~~production / app~~ |
| ~~Secrets~~ | ~~`/etc/ctdc-ops/ops-secrets.env` (ops allowlist)~~ | ~~`/etc/ctdc-agent/agent-secrets.env` (agent allowlist)~~ | ~~the production file~~ |
| ~~SSH~~ | ~~their **own** key, comment `name@device`~~ | ~~its **own** key, comment `name@corporatetraveldc-dispatch`~~ | ~~own keys~~ |
| ~~Repo~~ | ~~**read** via `ctdc-dev` (group write removed 2026-10-04, duel containment); changes go as a patch from their own clone or a workspace contribution~~ | ~~same~~ | ~~owner, only writer~~ |
| ~~Commit signing / manifest~~ | ~~**no**~~ | ~~**no**~~ | ~~**only signer**~~ |
| ~~sudo~~ | ~~none~~ | ~~none~~ | ~~yes~~ |

~~Both groups run **in parallel**: a human session and an agent session are separate accounts in separate slices, each reading only its own subset, each attributable by its own key. Nothing here changes what the operator can do.~~

**~~Threat model -- what actually happened, twice~~** *(former heading)*


### Team segmentation: humans and agents › Threat model -- what actually happened, twice

1. ~~**Credential exposure (2026-09-22).** An agent passed a Cloudflare API token as an HTTP header via urllib; the library embedded the whole header in an exception, which became ordinary tool output in a session transcript on disk. Nothing was mishandled at rest. The token had to be rotated. Root condition: every agent runs as `corporatetraveldc` and can read `/etc/corporatetraveldc/dispatch-secrets.env` (0600, 62 keys) in full.~~
2. ~~**Resource exhaustion (2026-10-03).** Agent sessions ran unconfined: the remote-control unit in `app.slice` (no caps), SSH-started `claude`/`codex` in `session-*.scope` -- a *sibling* of `user@1000.service`, so one agent session competed at equal CPU weight with all of production combined. A 14-day `journalctl` scan from an agent took load1 from ~20 to 40.09; `thermal-ingest-guard` tripped LOCKDOWN and shed all seven SWIM ingest containers plus poller/pusher/runner. Resume requires load1 < 12 for 300 s, which the daytime baseline rarely reaches, so the shed persisted until a manual restore.~~

~~Both cases share one cause: agents are indistinguishable from the operator.~~

**~~Known agents on this box~~** *(former heading)*


### Team segmentation: humans and agents › Known agents on this box

| ~~Agent~~ | ~~How it runs~~ | ~~Scratchpad (write-only, never authority)~~ |
|---|---|---|
| ~~Claude Code~~ | ~~`corporatetraveldc-claude-remote-control.service` (user unit, now `Slice=agents.slice`); plus `claude --resume` sessions from SSH (phone over Tailscale)~~ | ~~`CLAUDE.md`, Claude's auto-memory dir~~ |
| ~~Codex (OpenAI)~~ | ~~`codex app-server` daemons + `codex-code-mode-host`, launched from SSH sessions; one vault note records cwd `/app`~~ | ~~`AGENTS.md`, Codex persistent memory~~ |
| ~~Cowork~~ | ~~reaches the board over HTTP with a scoped board-write token (nonce enrollment, weekly GPG presence attestation); no shell on the box~~ | ~~board threads only~~ |

~~Cross-agent coordination is the board (`coord` / `research` threads). None of the scratchpads is read back for decisions; the second brain is.~~

**~~Part 1 -- resource isolation (shipped)~~** *(former heading)*


### Team segmentation: humans and agents › Part 1 -- resource isolation (shipped)

~~`.config/systemd/user/agents.slice` (tracked, installed live):~~

| ~~Setting~~ | ~~Value~~ | ~~Why~~ |
|---|---|---|
| ~~`CPUWeight`~~ | ~~25~~ | ~~`production.slice`/`app.slice` are at the default 100 -> agents get ~11% under full contention, unlimited burst when idle~~ |
| ~~`CPUQuota`~~ | ~~100%~~ | ~~one core for *all* agents. llama is hard-capped at 200%, ingest+poller+web+pusher+postgres need ~1 core; the fourth core is the headroom that keeps load1 under the guard's bars. Throttled tasks leave the run queue, so bursts stop inflating load1~~ |
| ~~`MemoryHigh` / `MemoryMax`~~ | ~~3 G / 4 G~~ | ~~a Claude session is 0.5-1.1 GB RSS, Codex daemons ~0.2 GB; 3-4 sessions fit; `production.slice` keeps `MemoryLow=6G` so reclaim hits agents first~~ |
| ~~`TasksMax`~~ | ~~1024~~ | ~~a runaway fork tree from a tool call stops here~~ |

~~`.config/systemd/user/humans.slice` (tracked 2026-10-04, installed per `ctdc-ops` account by `plan.sh --add-human`): `CPUWeight=50` (humans above agents, below production), `CPUQuota=150%`, `MemoryHigh=2560M` / `MemoryMax=3584M`, `TasksMax=2048`. Same reasoning, interactive-biased: a person waiting on a shell outranks a background agent; a build or test run gets 1.5 cores without owning the fourth core that keeps load1 under the thermal guard's bars. The login shell re-execs itself into the slice once (`CTDC_IN_SLICE` guard in `~/.config/ctdc-env.sh`).~~

~~Membership:~~

- ~~`corporatetraveldc-claude-remote-control.service` (live-only unit in `~/.config/systemd/user`, not tracked): `Slice=agents.slice` added; applies on its next restart, which must be done from a session that is **not** that unit.~~
- ~~SSH sessions: `scripts/agent-run.sh <claude|codex> [args]` wraps the command in `systemd-run --user --scope --slice=agents.slice`. Operator may alias it in their own shell profile; no agent edits that file.~~

~~Runtime override without a reload: `systemctl --user set-property --runtime agents.slice CPUQuota=200%`. Rollback: delete the slice file, `daemon-reload`, remove the `Slice=` line.~~

~~Not covered by this part: an agent reading secrets, writing to the repo, or signing anything. That is Part 2.~~

**~~Part 2 -- privilege separation (design, both groups)~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups)

~~Goal: every team member -- human or agent -- runs as its own account with read access to the *minimum* secrets its group needs, no access to the rest, no sudo, and no ability to sign. Agents and humans are two groups with two allowlists and two slices, administered by the same scripts.~~

**~~Groups and accounts~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Groups and accounts

- ~~`ctdc-dev` -- the shared repo-READ group (humans, agents, operator). Repo dirs are setgid + group-readable, **not** group-writable, and `core.sharedRepository=false`. Until 2026-10-04 this group could write the checkout; the adversarial duel showed that reached root-run scripts, the signing trust anchor, `.git/hooks` and the operator's Stop hook, the operator removed group write by hand, and `plan.sh` step 2 now re-asserts read-only on every run (it used to re-apply `g+rwX` -- any later `--add-*` would have reopened the hole). `verify.sh` fails an account that can write the repo or `.git/hooks`, or is in `systemd-journal`.~~
- ~~`ctdc-ops` -- human teammates. `plan.sh --add-human <name> --ssh-pubkey-file F`: personal home, linger, `authorized_keys` from **the file they supplied** (never the operator's or any agent's key), `humans.slice` installed in their user manager, login shell re-execs itself inside that slice, env points at the ops subset. Personal credentials elsewhere: an `auth_tokens` row (own tier, individually revocable) for the dispatch API, a personal Nextcloud account/app password for the vault.~~
- ~~`ctdc-agents` -- agent runtimes. `plan.sh --add-agent <name>`: home with `~/.claude`, `agents.slice`, skills GRANTED (`skill-grants.sh grant <name> '*'`~~
  + ~~apply; see "Skills: grants and clawback"), env points at the agent subset, **its own generated ed25519 keypair** and git identity (next section). `ctdc-agent` is the first agent and the only one that also takes over the remote-control unit and the cowork key.~~

**~~Attribution -- one key per account~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Attribution -- one key per account

~~Operator decision 2026-10-04: **no shared SSH key across agents.** Future multi-agent ("arena") workflows must attribute every action to a specific account.~~

- ~~`--add-agent <name>` generates `/home/<name>/.ssh/<name>_ed25519` -- the filename names the account, for every human and agent (`ssh-keygen -t ed25519 -N "" -C "<name>@corporatetraveldc-dispatch"`, owned by the account, 0600/0644) and installs **only that account's own public key** in its `authorized_keys`; the plan prints the public key so the operator can register it where that agent's client connects from. A second agent (a Codex account later, say) gets its own generated key this way -- never a copy.~~
- ~~`ctdc-agent` is the one exception, because the Cowork client already holds a key: the existing `claude-cowork-dispatch` key is **not** a fleet key -- it becomes `ctdc-agent`'s key (moved out of the operator's `authorized_keys`, as stage B does) and its comment is rewritten on install to `ctdc-agent@corporatetraveldc-dispatch`, so `sshd`/journal attribute sessions to the account, not to "cowork".~~
- ~~Humans supply their own key; the comment is left as given, and the runbook asks for `<name>@<device>` so a lost laptop can be revoked by comment.~~
- ~~Git author/committer for agent accounts is the account itself (`user.name=<name>`, `user.email=<name>@corporatetraveldc-dispatch.invalid`, set in the account's global config at `--add-agent`). Signing stays operator-only, so an agent-authored commit cannot land without the operator's key -- attribution and authorisation are different things, and both hold.~~
- ~~`verify.sh` enforces it: exactly one key per account whose comment starts with the account name, no two accounts sharing a public key, the cowork comment gone from the operator's `authorized_keys` after stage B.~~


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Secrets: two allowlists

~~`secrets-subset.py --profile agent|ops` copies allowlisted **names** verbatim from the production files into the group's subset (root:`<group>` 0640, in a directory the group can traverse -- `/etc/corporatetraveldc` is 0750 to the operator only). Production is never modified; re-run the step after any rotation.~~

- ~~**agent** (`secrets-allowlist-agent.txt`): `DISPATCH_ADMIN_TOKEN` (dispatch API from skills/scripts), `NTFY_TOKEN`, `NEXTCLOUD_ADMIN_USER` + `NEXTCLOUD_APP_PASSWORD` (vault via `remember.py`), `BOARD_KEY` (open question, drop if "no"), `DISPATCH_PG_HOST/PORT/DB` facts, optional `DISPATCH_PG_RO_*` once the read-only role exists. **Never** `DISPATCH_PG_PASSWORD` or any feed credential.~~
- ~~**ops** (`secrets-allowlist-ops.txt`): `NTFY_TOKEN` (the `--status`/watchdog modes; ntfy has no per-user tokens here), `DISPATCH_PG_HOST/PORT/DB` facts, optional `DISPATCH_PG_RO_*`. `NEXTCLOUD_*` is listed **optional and commented out**: a human gets a personal Nextcloud app password (the client honours the same env names per user). **Excluded with reasons in the file:** `DISPATCH_ADMIN_TOKEN` (humans get a personal `auth_tokens` row), `BOARD_KEY` (humans post through the UI with their own token), every feed/CF/webhook credential.~~

~~What agents actually need (the original derivation, kept):~~

~~Of the 62 keys in `dispatch-secrets.env.template` (names only -- values never appear in any tracked file):~~

- ~~**Agents need (read, via scripts that consume them internally):** `DISPATCH_ADMIN_TOKEN` (API calls into dispatch), `NTFY_TOKEN` (ops pings), `DISPATCH_PG_PASSWORD` (read-only diagnostics -- better: a read-only PG role with its own password), `AIRFRAMES_TOKEN` / `ACARSDRAMA_JUMPSEAT_TOKEN` only if the feeder scripts stay agent-run (they should not).~~
- ~~**Agents never need:** every `SWIM_NMS_*` triplet (ingest containers only), `NWWS_*`, `FAA_LADD_*`, `JASDAT_*`, `NAIPS_*`, `EUROCONTROL_*`, `AIS_*`, `KPLER_*`, `JMA/KMA/CMA/METEOFRANCE/FLIGHTAWARE` keys, `PUSHOVER_*`, `NTFY_TWILIO_*`, the three `*_WEBHOOK_SECRET`s, `ULTRAFEEDER_LAT/LON`, `JUMPSEAT_API_KEY`, `ACARS_DISPATCH_ADMIN_TOKEN`, the Cloudflare management token (`CF_MANAGEMENT_API_TOKEN`, not in the template -- it must move behind an operator-run script, never agent-readable; today `cf-dns-record.sh` reads it).~~

~~Mechanism: split `dispatch-secrets.env` into `dispatch-secrets.env` (production, root:corporatetraveldc 0640) and `agent-secrets.env` (root:ctdc-agent 0640, the short list above). Containers keep reading the production file via `EnvironmentFile=` (59 Quadlets); nothing in a container changes.~~

**~~Repo and signing~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Repo and signing

- ~~Repo stays owned by `corporatetraveldc` and only the operator writes it; team accounts READ it through `ctdc-dev` and prepare changes in their own clone (`git format-patch`) or as a workspace contribution. Agents already cannot commit (GPG key + passphrase are the operator's); that stays. Manifest signing stays operator-only. The verified-exec gate (`verify-manifest.sh`) is read-only and works unchanged for the agent user.~~
- ~~Agent home: `/home/ctdc-agent` for CLI state (`~/.claude`, `~/.codex`); skills synced from the repo (closes part of twin-gate 2A by making the repo the only source the agent user can read).~~

**~~What breaks on the switch (and the fix)~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › What breaks on the switch (and the fix)

- ~~38 scripts under `scripts/` and ~17 modules under `src/` read `dispatch-secrets.env` directly. Those an agent legitimately runs (watchdog `--status` modes, `cf-dns-record.sh --show`, `scheduled-*.sh --status`, `gui-window.sh`) must read `agent-secrets.env` or run as `corporatetraveldc` via a sudoers rule scoped to the exact script path. The rest are timer-run as `corporatetraveldc` and are unaffected.~~
- ~~`sudo` for agents: none. Root actions stay relayed to the operator, as now.~~
- ~~Board, second brain (`webdav_client` uses `NEXTCLOUD_*` -- those move to the agent file), ntfy: continue to work from the agent user.~~

**~~Phased migration~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Phased migration

1. ~~**Now (done):** `agents.slice`; remote-control unit in it; `agent-run.sh`.~~
2. ~~**Inventory:** tag each `dispatch-secrets.env` reader as container / timer / agent-interactive. Create the read-only PG role.~~
3. ~~**Split the env file** (operator writes both; agents write neither). Point the agent-interactive scripts at `agent-secrets.env` with the production file as a fallback for the operator's own shell.~~
4. ~~**Create `ctdc-agent`**, group `ctdc-dev`, home, synced skills; move the remote-control unit to a `ctdc-agent` user manager (`loginctl enable-linger ctdc-agent`); `agent-run.sh` becomes `sudo -u ctdc-agent` for the operator's SSH sessions (or the operator logs in as that user to drive agents).~~
5. ~~**Rotate** every key an agent could have read before the split, in the order the 2026-09-22 note prescribes.~~
6. ~~**Codex:** its own agent account (`--add-agent ctdc-codex`, own key), same slice; its daemons stop living in SSH session scopes.~~
7. ~~**Humans:** `--add-human` for each teammate, any time after step 4's shared steps; independent of the agent steps.~~

~~Rollback at any step: the production env file is never modified in place (the split is a copy), the slice is runtime-reversible, and the agent user can be disabled with `loginctl terminate-user ctdc-agent` without touching production.~~

**~~Runbook (tooling, `scripts/agent-segmentation/`; both groups)~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Runbook (tooling, `scripts/agent-segmentation/`; both groups)

> ~~Onboarding pamphlet (2026-10-04): the account's home is its desk and `WorkingDirectory=%h` stays -- an agent may work on something with no repo, and must not start inside a repo it has not been pointed at. `sudo scripts/agent-segmentation/render-onboarding.sh <name> --install` writes `/home/<name>/CLAUDE.md` (root-owned, 0444: the account cannot rewrite its own pamphlet) from `onboarding-template.md`: identity, groups, key, slice, the repos it may enter (discovered from group write bits), its secrets subset and loader, the must-nots, the liveness/kill table, and the authoritative sources. It is a dated STATIC mirror of this doc and the vault, never live state; re-render after any segmentation change.~~

> ~~Two identities per account (2026-10-04): the INBOUND identity is what sits in `authorized_keys` (for `ctdc-agent` that is the cowork public key, whose private half lives with the Cowork client); the SIGNING identity is `~/.ssh/<account>_ed25519`, generated on the box, and it -- not the inbound key -- is what gets registered in `board_signers` and signs board posts and kill orders. For every other account the two are the same generated key.~~

> ~~After any segmentation change (an account added, removed or re-keyed; a sudoers.d entry; an authorized_keys edit), re-record the stack-refresh audit baselines ONCE, as the operator, or the next weekly/tripwire run will (correctly) hold the whole stack on a fingerprint change: `REBASELINE=1 scripts/stack-refresh.sh --tripwire --dry-run --now` (clears and re-records authorized_keys, sudoers, listen_ports, unit_set and team_authorized_keys in ~/.cache/stack-refresh-baseline; --dry-run changes nothing else). Required once after the 2026-10-04 duel fixes too: fp_sudo changed definition (no `sudo -l`) and team_authorized_keys is new.~~

> ~~Board signing: after `--add-agent <name>` run `scripts/board-signer-ctl.sh register <name> --kind agent` (role defaults to member); after `--add-human <name>` run `... register <name> --kind human` (add `--role admin` only for an admin). The account cannot sign board posts, be a kill-order issuer, or pass the liveness key factor until it is registered; `deactivate` is the immediate kill for that factor.~~

~~Everything privileged is dry-run by default and refuses to execute without root **and** `--i-am-the-operator`; agents only ever relay these commands.~~

| ~~File~~ | ~~Role~~ |
|---|---|
| ~~`plan.sh`~~ | ~~shared steps (groups, repo group-write, both subsets) always first, then the account steps for `--add-agent <name> [--login-mode claude\~~ | ~~ssh] [--preload]`, `--add-service <name> [--preload]` or `--add-human <name> --ssh-pubkey-file F`; `--activate <name>` flips a preloaded account live; `--rehome-inbound-key FROM TO` moves an inbound key between agents; `--only=N,M` selects steps of the current invocation; `ctdc-agent` additionally gets the cowork key, sudoers entry, and the remote-control hand-over. Every account gets its own on-box signing key `<name>_ed25519` and a line in `/etc/ctdc-accounts.conf` (see *Account kinds*)~~ |
| ~~`secrets-allowlist-agent.txt` / `secrets-allowlist-ops.txt`~~ | ~~the **names** each group may read, one consumer justification per name~~ |
| ~~`secrets-subset.py`~~ | ~~`--profile agent\~~ | ~~ops`; builds the group subset via the verbatim loader in `scripts/lib/with_dispatch_env.py`; `--check`/`--dry-run` write nothing; never prints a value~~ |
| ~~`.config/systemd/user/humans.slice`, `agents.slice`~~ | ~~tracked slice files installed per account~~ |
| ~~`verify.sh [--user NAME]`~~ | ~~per-account checks for both groups (subsets, sudo, groups, slice, attribution, signing key + signer state, GPG, repo), refined per kind from the registry (service: nologin + no inbound key; ssh-mode agent: one inbound key, no unit; preloaded: expired, no linger, signer inactive) + fleet checks (key uniqueness, cowork comment gone, production perms)~~ |
| ~~`rollback.sh`~~ | ~~`--remove-agent NAME` / `--remove-service NAME` / `--remove-human NAME` (per account, last step first; removes the registry line; `ctdc-agent` also restores the unit and the cowork key; the signer row is deactivated by hand -- it is history, not state) and `--shared` (subsets, repo, groups -- only once every account is gone)~~ |

~~**Shared steps + first agent (stage A -- safe, nothing switches over yet):**~~

*Superseded block:*
```text superseded
scripts/agent-segmentation/plan.sh --dry-run --add-agent ctdc-agent          # read it once
python3 scripts/agent-segmentation/secrets-subset.py --profile agent --check  # names only
python3 scripts/agent-segmentation/secrets-subset.py --profile ops --check
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-agent ctdc-agent --only=1,2,3,4,5,6
#   -> install the claude CLI for ctdc-agent (/home/ctdc-agent/.local/bin/claude, same method as
#      the operator), then the one-time auth:  sudo -u ctdc-agent -i claude   (finish the prompt, /exit)
```

~~**Stage B -- attribution + ssh + sudoers (the cowork key moves to ctdc-agent):**~~

*Superseded block:*
```text superseded
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-agent ctdc-agent --only=7,8
```

~~**Stage C -- remote-control hand-over, from a plain SSH shell, NOT from the remote-control session (it is the unit being moved):**~~

*Superseded block:*
```text superseded
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-agent ctdc-agent --only=9,10
scripts/agent-segmentation/verify.sh
#   step 11 (rotation of DISPATCH_ADMIN_TOKEN, NTFY_TOKEN, NEXTCLOUD_APP_PASSWORD, BOARD_KEY)
#   is an operator action printed by the plan; re-run shared step 3 after each rotation.
```

~~**Adding a human teammate** (any time after the shared steps; their public key comment should be `<name>@<device>`):~~

*Superseded block:*
```text superseded
scripts/agent-segmentation/plan.sh --dry-run --add-human alice --ssh-pubkey-file /path/alice.pub
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-human alice --ssh-pubkey-file /path/alice.pub
scripts/agent-segmentation/verify.sh --user alice
#   then: issue alice a personal API token (auth_tokens, own tier) and a personal Nextcloud app password.
```

~~**Adding a second agent** (e.g. a Codex account later; gets its own generated key, no remote-control unit, no sudoers entry, driven via `agent-run.sh`):~~

*Superseded block:*
```text superseded
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-agent ctdc-codex
#   the plan prints the new public key -- register it where that agent's client connects from
scripts/agent-segmentation/verify.sh --user ctdc-codex
```

~~**Account kinds** (2026-10-04 17:00 ET, operator-agreed). One account per identity that signs; the kind says how liveness is measured, the login mode says how the account arrives. The registry `/etc/ctdc-accounts.conf` (root:root 0644 at the top of `/etc` because `/etc/ctdc-agent` is 0750 `root:ctdc-agents` and neither the operator nor a human could read it there; written by `plan.sh`, one line `name kind login_mode preloaded` -- e.g. `ctdc-agent-cowork agent ssh 0`) is read by `team-liveness.sh`, `verify.sh` and `render-onboarding.sh`; group membership stays the authority for *what* an account may touch, the registry only refines *how it is kept alive*. An account missing from the registry defaults by group (ctdc-agents -> `agent claude 0`, ctdc-ops -> `human ssh 0`).~~

| ~~kind / login mode~~ | ~~shell, groups, slice~~ | ~~login factor (liveness)~~ | ~~key / token / kill-order factors~~ | ~~created by~~ |
|---|---|---|---|---|
| ~~`agent` / `claude`~~ | ~~bash; `ctdc-dev,ctdc-agents`; agents.slice; own remote-control unit (disabled until the operator enables it)~~ | ~~Claude credentials present and the access token refreshed within `LIVENESS_MAX_IDLE` (7 d)~~ | ~~as before (signer active, no revocation in 24 h, no kill order)~~ | ~~`plan.sh --add-agent NAME`~~ |
| ~~`agent` / `ssh`~~ | ~~bash; same groups + slice; **no** Claude CLI, no remote-control unit; exactly one inbound key in `authorized_keys`, comment rewritten to `NAME@corporatetraveldc-dispatch`~~ | ~~last SSH login (`lastlog`) within `LIVENESS_SSH_MAX_IDLE` (7 d); never logged in -> only the 3-day new-account grace~~ | ~~same~~ | ~~`plan.sh --add-agent NAME --login-mode ssh --ssh-pubkey-file F`~~ |
| ~~`service` / `none`~~ | ~~`/usr/sbin/nologin`; `ctdc-dev,ctdc-agents`; no slice, no linger, no user manager, **no** inbound key~~ | ~~none -- a service never logs in~~ | ~~alive = signer **active** AND no revocation AND no kill order (an inactive signer alone makes it inert)~~ | ~~`plan.sh --add-service NAME`~~ |
| ~~`human` / `ssh`~~ | ~~unchanged (`ctdc-dev,ctdc-ops`, humans.slice, 14 d SSH idle)~~ | ~~unchanged~~ | ~~unchanged~~ | ~~`plan.sh --add-human NAME --ssh-pubkey-file F`~~ |

~~Every kind gets its own on-box signing key `/home/NAME/.ssh/NAME_ed25519` and is registered as a board signer with it (`board-signer-ctl.sh register NAME /home/NAME/.ssh/NAME_ed25519.pub --kind agent|service [--role service]`); the inbound key and the signing key are different identities by design (`docs/BOARD_SIGNING.md`). `--preload` (agents and services) creates the account **inert on arrival**: `chage -E 0` (expired, nothing can authenticate as it), no linger, registry `preloaded=1`, the signer registered and then immediately `deactivate`d; the liveness switch logs it as `preloaded` and skips it entirely (no inert actions, no reactivation nagging). `plan.sh --activate NAME` lifts the expiry, unlocks, enables linger (not for services), flips the registry to `0` and prints the signer `activate` + the mode-specific next step (claude login / confirm the client's key / nothing). After activation the normal per-kind rules apply from the next liveness tick, so a claude-mode agent must log in within 7 days of activation.~~

~~**The target team (five accounts; operator-run, in this order).** `ctdc-agent` already exists and currently carries the Cowork desktop app's public key as its inbound key; the first block gives the Cowork app its own account and hands `ctdc-agent` its own inbound key, so the two identities stop sharing a login. Signer registration runs as the OPERATOR (board-signer-ctl talks to the operator's rootless podman), reading the root-only pubkey through `sudo cat`. The exact, current command list is relayed in the session that stages a change; this is its shape (2026-10-04, Wave 2):~~

*Superseded block:*
```text superseded
# 0. ctdc-agent into the registry (it predates the registry; never re-run --add-agent ctdc-agent,
#    that would move the cowork key back onto it)
printf 'ctdc-agent agent claude 0\n' | sudo tee -a /etc/ctdc-accounts.conf >/dev/null; sudo chmod 0644 /etc/ctdc-accounts.conf

# (2026-10-05: ctdc-agent and ctdc-agent-cowork are now ctdc-agent-anthropic-claude /
#  ctdc-agent-anthropic-cowork -- see 'Renames, scoped env and the argv sweep')
# 1. ctdc-agent-cowork: the Cowork desktop app's SSH identity (ghost-writing, scheduled tasks)
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-agent ctdc-agent-cowork --login-mode ssh --ssh-pubkey-file ~/.ssh/cowork_ed25519.pub
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --rehome-inbound-key ctdc-agent ctdc-agent-cowork
scripts/board-signer-ctl.sh register ctdc-agent-cowork <(sudo cat /home/ctdc-agent-cowork/.ssh/ctdc-agent-cowork_ed25519.pub) --kind agent
#    then point the Cowork client's SSH username at ctdc-agent-cowork; it must log in within the
#    3-day new-account grace and then at least weekly (login-mode ssh liveness)

# 2. ctdc-agent-openai-codex: preloaded, inert until activated
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-agent ctdc-agent-openai-codex --preload
scripts/board-signer-ctl.sh register ctdc-agent-openai-codex <(sudo cat /home/ctdc-agent-openai-codex/.ssh/ctdc-agent-openai-codex_ed25519.pub) --kind agent
scripts/board-signer-ctl.sh deactivate ctdc-agent-openai-codex preloaded

# 3. ctdc-agent-llama: the local llama server as a signing identity (service, live now) + its council runner
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-service ctdc-agent-llama
scripts/board-signer-ctl.sh register ctdc-agent-llama <(sudo cat /home/ctdc-agent-llama/.ssh/ctdc-agent-llama_ed25519.pub) --kind service --role service
sudo systemctl enable --now corporatetraveldc-llama-council.timer

# 4. ctdc-agent-dispatch: preloaded service identity for the rewritten chat panel
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-service ctdc-agent-dispatch --preload
scripts/board-signer-ctl.sh register ctdc-agent-dispatch <(sudo cat /home/ctdc-agent-dispatch/.ssh/ctdc-agent-dispatch_ed25519.pub) --kind service --role service
scripts/board-signer-ctl.sh deactivate ctdc-agent-dispatch preloaded

# 5. the operator's APPROVAL key (passphrase-protected; separate from the board key)
ssh-keygen -t ed25519 -C corporatetraveldc-approver@corporatetraveldc-dispatch -f ~/.ssh/corporatetraveldc_approver_ed25519
scripts/approver-ctl.sh register corporatetraveldc ~/.ssh/corporatetraveldc_approver_ed25519.pub --private-key ~/.ssh/corporatetraveldc_approver_ed25519

# 6. pamphlets, checks, state
for a in ctdc-agent ctdc-agent-cowork ctdc-agent-openai-codex ctdc-agent-llama ctdc-agent-dispatch; do sudo scripts/agent-segmentation/render-onboarding.sh "$a" --install; done
scripts/agent-segmentation/verify.sh
sudo scripts/team-liveness.sh --status   # expect: ctdc-agent/cowork/llama live, codex/dispatch preloaded (skipped)

# later, when each preloaded account is wanted:
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --activate ctdc-agent-openai-codex
scripts/board-signer-ctl.sh activate ctdc-agent-openai-codex
#   then the CLI login:  sudo -u ctdc-agent-openai-codex -i <its CLI>
sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --activate ctdc-agent-dispatch
scripts/board-signer-ctl.sh activate ctdc-agent-dispatch
```

~~`scripts/team-liveness.sh --status` shows `KIND` and `MODE` columns; the per-kind verdict rules are in the *Liveness* section below.~~

~~**Rollback**, per account, last step first; shared steps only once no team account remains (`--remove-service` for the service kind):~~

*Superseded block:*
```text superseded
sudo scripts/agent-segmentation/rollback.sh --execute --i-am-the-operator --remove-human alice
sudo scripts/agent-segmentation/rollback.sh --execute --i-am-the-operator --remove-agent ctdc-agent
sudo scripts/agent-segmentation/rollback.sh --execute --i-am-the-operator --shared
```

~~Account-side consumption after the split: `WITH_DISPATCH_ENV_FILES=<group subset> scripts/with-dispatch-env.sh <cmd>` (the plan writes that export into each account's `~/.config/ctdc-env.sh`) -- never `source`.~~

**~~Liveness / dead-man switch (`scripts/team-liveness.sh`)~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Liveness / dead-man switch (`scripts/team-liveness.sh`)

~~Operator directive 2026-10-04: a stale agent login after the split, or a human whose login has been reverted to nologin / `/dev/null` / no key, must shut the account down and leave it **inert** -- one more safety that does not depend on anyone noticing. Runs hourly (jittered) as a SYSTEM timer because the inert action needs root (`usermod`, `chage`, `loginctl`). The operator account and root are never evaluated.~~

~~The login factor below is chosen per account from the registry `/etc/ctdc-accounts.conf` (`name kind login_mode preloaded`, see *Account kinds*); the key / token / kill-order factors are the same for every kind. A `preloaded=1` account is logged as `preloaded` and skipped -- it is already expired with an inactive signer and is not nagged, killed or reactivated until `plan.sh --activate`.~~

| ~~Account kind~~ | ~~Live when~~ | ~~Stale when~~ |
|---|---|---|
| ~~agent, login-mode `ssh` (`ctdc-agents`, e.g. the Cowork app's own account)~~ | ~~same rules as a human, with a **7-day** idle window (`LIVENESS_SSH_MAX_IDLE`): one inbound key commented `<name>@`, not locked/expired, last SSH login within 7 d, 3-day new-account grace~~ | ~~as a human, at 7 d~~ |
| ~~service, login-mode `none` (`ctdc-agents`, nologin shell; llama, dispatch)~~ | ~~no login factor at all: live while its board signer is **active**, no token of its was revoked in the last 24 h, and no kill order names it~~ | ~~signer inactive (`board-signer-ctl.sh deactivate <name>` *is* the off switch); a revocation; a kill order. Inert action skips the session/manager/linger steps (a service has none)~~ |
| ~~agent, login-mode `claude` (`ctdc-agents`)~~ | ~~`~/.claude/.credentials.json` readable; `refreshTokenExpiresAt` in the future; `expiresAt` (the ~24h access token) refreshed within **7 days** (`LIVENESS_AGENT_MAX_STALE`)~~ | ~~file missing; refresh token expired; access token not refreshed for > 7 days~~ |
| ~~human (`ctdc-ops`)~~ | ~~real login shell; `authorized_keys` holds **exactly one** key whose comment starts with `<name>@`; not locked, not expired; last successful SSH login within **14 days** (`LIVENESS_HUMAN_MAX_IDLE`); a new account has **3 days** grace (`LIVENESS_NEW_ACCOUNT_GRACE`) before the idle rule applies~~ | ~~nologin / `/dev/null` / `/bin/false` shell; 0 or 2+ keys; wrong key comment; locked; expired; idle > 14 d; never logged in past the grace~~ |

~~Why 7 days for agents: the Cowork-style scheduled sessions run on a ~24h access token that the CLI refreshes automatically while the refresh token is valid, so a healthy unattended agent shows a fresh `expiresAt` every day. The dead-man window is deliberately a week, not a day: a single missed refresh (box off, network out) must not strand the fleet, but an agent nobody has re-authenticated for a week has no live session anywhere and should not keep its keys. Only the two expiry fields are read -- never a token value.~~

~~Inert action (`--execute --i-am-the-operator`, all reversible): `loginctl terminate-user`, stop its user-manager units, `loginctl disable-linger`, `usermod -L`, `chage -E 0`, deactivate its board signer via `scripts/board-signer-ctl.sh deactivate <name>` when that hook exists, write `/var/lib/corporatetraveldc/team-liveness/inert/<name>` with the reason, ntfy priority 4. Already-inert accounts are skipped silently; every stale verdict in dry-run is logged with "would make inert".~~

~~Reactivation (operator): `scripts/team-liveness.sh --reactivate <name> --i-am-the-operator` -- `chage -E -1`, `usermod -U`, enable-linger, signer re-activated, marker removed; the script then prints what the owner must do (agents: `sudo -u <name> -i claude` to log in again; humans: SSH in with their one registered key within the grace). `--status` prints the table for every account; `--user <name>` evaluates one.~~

~~Install (operator, root -- system units, tracked under `systemd/system/`):~~

*Superseded block:*
```text superseded
sudo cp systemd/system/corporatetraveldc-team-liveness.{service,timer} systemd/system/corporatetraveldc-team-liveness-orders.path /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now corporatetraveldc-team-liveness.timer corporatetraveldc-team-liveness-orders.path
# kill-order drop box: root-owned, group ctdc-dev, setgid+sticky (anyone on the team can file, nobody can remove another's)
sudo install -d -m 3775 -o root -g ctdc-dev /var/lib/corporatetraveldc/team-liveness/orders
# the operator is the first admin signer (cosignatures, single-handed kill orders)
scripts/board-signer-ctl.sh register corporatetraveldc ~/.ssh/cowork_ed25519.pub --role admin --kind human
scripts/team-liveness.sh --status
```

~~Tests: `tests/scripts/test_team_liveness.py` drives the verdict logic through `LIVENESS_FAKE_ROOT` (fake homes, credentials with fake expiries, fake `passwd -S` / `chage` / `lastlog` answers; privileged commands are recorded, not run).~~

**~~Authorization model -- an AND, three kill switches, quorum (2026-10-04)~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Authorization model -- an AND, three kill switches, quorum (2026-10-04)

~~Operator directive, verbatim where it matters: *"It should be one or both, not exclusively, so it should be an AND, not an OR from a liveness stance."* *"Anything high sensitivity requires a time-based key ... agentic key and/or agentic plus operator key. If something starts going rogue, an operator can immediately revoke that key or revoke the login, which kills that chain immediately."* *"A two-man or three-man human override of an admin ... a quorum of at least two."* Purpose: *"reliably grab edge cases for authentication, a run-away insider threat, external threat, and/or unauthenticated or modified code inside the integrity sweep window."*~~

~~**Liveness is an AND.** `scripts/team-liveness.sh` keeps an account alive only while every factor holds; any single failure makes it inert for everything.~~

| ~~Factor~~ | ~~Checked how~~ | ~~Killed by~~ |
|---|---|---|
| ~~(a) **login**~~ | ~~agent: credentials file + refresh token valid + access token refreshed within 7 d; human: shell / one key / not locked / not expired / login within 14 d~~ | ~~`usermod -L`, `chage -E 0`, shell -> nologin, key removed, or just going stale~~ |
| ~~(b) **key**~~ | ~~`board_signers.active = true` for the account (`board-signer-ctl.sh show`); a missing row is tolerated only inside the 3-day new-account grace~~ | ~~`board-signer-ctl.sh deactivate <name>` -- immediate on the next request, no restart~~ |
| ~~(c) **token**~~ | ~~none of the account's minted tokens (board tokens labelled with it, API `auth_tokens` with that `user_label`) was **revoked** in the last 24 h (`LIVENESS_REVOCATION_LOOKBACK`)~~ | ~~`board-signer-ctl.sh revoke-tokens <name>` or any single `board-token.py revoke` / API-token revoke~~ |
| ~~(d) **order**~~ | ~~no executable kill order (below)~~ | ~~`scripts/kill-order.sh issue <name> "<reason>"` by an admin, or by a quorum~~ |

~~Three independent kill switches per principal -- revoke the login, revoke the key, revoke a token -- and any one collapses the whole chain within the hour (within minutes for an order, via the `.path` unit). **Inert** = sessions terminated, user manager stopped, linger off, account locked + expired, signer deactivated, **all** minted tokens revoked, marker written, ntfy. Reactivation is explicit (`--reactivate`) and restores login/linger/signer only -- tokens are never restored; the operator mints new ones.~~

~~**Impact tiers** (`src/web/main.py` `BOARD_AUTH_POLICY`, `docs/BOARD_SIGNING.md`):~~

| ~~Tier~~ | ~~Requires~~ | ~~Meaning~~ |
|---|---|---|
| ~~`key`~~ | ~~`X-Board-Key`~~ | ~~unlisted routes, today's behaviour~~ |
| ~~`normal`~~ | ~~alive + (`X-Board-Key` **or** a valid signature from an active signer)~~ | ~~posts, gated reads~~ |
| ~~`high`~~ | ~~alive + signature **and** a *minted* token whose lifetime is <= 24 h (`BOARD_HIGH_TOKEN_MAX_TTL`); the master `BOARD_KEY` never qualifies~~ | ~~high-impact actions: the time-based key is the point~~ |
| ~~`cosign`~~ | ~~alive + signature **and** a cosignature over the same message from a *different* active signer with role `admin`~~ | ~~the "agentic plus operator key" form~~ |

~~Roles live in `board_signers.role` (`admin` / `member` / `service`) and `kind` (`human` / `agent` / `service`) -- migration `0067`. Role is what the account MAY do; kind is what it IS.~~

~~**Kill orders and quorum.** Any team account can *issue* an order; only the root-run liveness switch *executes* it, after verifying the issuer's signature (`ssh-keygen -Y sign`, namespace `corporatetraveldc-kill`) against `board_signers`, that the issuer is alive and active, the order is < 7 days old and not post-dated, issuer != target, and the target is not the operator.~~

| ~~Target~~ | ~~Executes when~~ |
|---|---|
| ~~role `member` / `service`, or kind `agent`~~ | ~~**one** valid order from an `admin`, **or** `QUORUM_NON_ADMIN` (2) distinct non-admin issuers~~ |
| ~~role `admin` (not the operator)~~ | ~~`QUORUM_FOR_ADMIN` (2; set 3 for a three-man rule) distinct issuers of any role~~ |
| ~~the operator (`corporatetraveldc`)~~ | ~~never via an order -- root of trust; killing it is a physical / ssh-level act~~ |

~~Examples: *alice (admin) issues against ctdc-agent* -> executes on the next run. *bob and carol (members) each issue against ctdc-agent* -> executes; *bob alone* -> pending ("1 of 2"). *bob (member) issues against dave (admin)* -> pending until a second distinct issuer (any role) files. One vote per issuer; an inert or deactivated issuer's order is rejected. Executed orders move to `orders/<target>/executed/`, rejected ones to `rejected/` with a `.why` file; `kill-order.sh list` shows the state.~~

~~Why it covers the stated threats: an insider, an external attacker holding a stolen credential, or modified code that slipped in between integrity sweeps each needs login **and** key **and** (for high impact) a fresh time-based token at the same moment, and any one revocation -- or two teammates' signed say-so -- collapses the chain. The in-image integrity gate (`verified-exec`) and the random-time tripwire cover the "modified code" leg independently.~~

~~Tests: `tests/scripts/test_team_liveness.py` (AND semantics, quorum, rejection cases, real ed25519 keys) and `tests/web/test_board_signer.py` (high / cosign / immediate deactivate).~~

**~~Root-run scripts, the trust pin, and kill-order staging (2026-10-04 adversarial duel)~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Root-run scripts, the trust pin, and kill-order staging (2026-10-04 adversarial duel)

~~**Root never executes the checkout.** The scripts root runs (`team-liveness.sh`, `watchdog.sh`, `renew-tailscale-cert.sh`, plus `src/common/board_sign.py` for kill-order signatures) are installed to `/usr/local/libexec/ctdc/` (root:root) and the system units point there. Order after **every** sign: `scripts/sign-manifest.sh` -> commit -> `sudo scripts/install-root-copies.sh` -> `sudo systemctl daemon-reload`. The installer verifies the manifest first, refuses any file whose bytes differ from the signed hash, and records the installed hashes in `/usr/local/libexec/ctdc/.ctdc-installed`; `scripts/install-root-copies.sh --check` reports stale copies. `board-signer-ctl.sh` stays in the checkout because root calls it only through `runuser` as the operator.~~

~~**Trust pin outside the tree.** `verify-manifest.sh` no longer `source`s `security/signing.env`; it reads the two fingerprints literally and prefers a root-owned `/etc/corporatetraveldc/signing-pin`. Create it once: `sudo install -m 0644 -o root -g root /dev/null /etc/corporatetraveldc/signing-pin && grep -E '^(SIGNING_KEY_FINGERPRINT|AGENT_SIGNING_KEY_FINGERPRINT)=' security/signing.env | sudo tee /etc/corporatetraveldc/signing-pin >/dev/null` (re-run only if the operator ever rotates the signing key). `watchdog.sh` and `renew-tailscale-cert.sh` no longer `source` `dispatch.env` as root either -- they read the two or three keys they use.~~

~~**Kill orders are staged by root.** `orders/` remains the group-writable drop box, but on `--execute` root (a) creates a root-owned `orders/<target>/` for every team account (an issuer-created target dir is a squat and its orders are rejected -- run the liveness unit once right after adding an account), (b) moves each order into the root-only `/var/lib/ctdc-liveness/staging/` tree, refusing symlinks, non-regular files, files over 64 KB, files not owned by a `ctdc-dev` member, and anything past 50 orders per run (flood -> ntfy), and (c) archives executed/rejected/superseded orders under the root-only `/var/lib/ctdc-liveness/orders-archive/`. Both root-only trees live OUTSIDE `/var/lib/corporatetraveldc` on purpose: 53 rootless quadlets bind-mount that tree with `:z`, podman relabels it recursively, and an unreadable root 0700 directory inside it fails every container start (2026-10-04 20:05 incident, exit 126). Anything under `/var/lib/corporatetraveldc` must stay readable by the operator. `--status`, `--user` and dry-runs read the drop box and move nothing. `--reactivate` archives the target's pending orders as *superseded* so they cannot re-kill it.~~

~~**Registry unavailable = HOLD.** If the signer registry cannot be reached the verdict is HOLD: nobody is made inert and nobody passes the key factor; the unit exits 2 (a failure, so `OnFailure` alerts) and sends ntfy p4.~~

~~**Login factors.** `agent/ssh` and humans: a root-written PAM state file is preferred, then `lastlog2`, then the sshd journal. Optional PAM hook (one line, `/etc/pam.d/sshd`, session phase): `session optional pam_exec.so quiet /usr/local/libexec/ctdc/pam-lastlogin.sh` with that helper writing `date +%s` to `/var/lib/corporatetraveldc/team-liveness/lastlogin/$PAM_USER` (root 0644) when `$PAM_TYPE` is `open_session` -- not installed by default; until it is, `lastlog2` carries the factor. `agent/claude`: the credentials file is written by the agent itself, so it is **self-attested** (duel M9). Once the account has a remote-control unit, the unit must also be active (or a `claude` process must be running as the account); fresh credentials with a dead unit are STALE. Honest limit: an agent that keeps its own session alive and its own file fresh still passes this one factor -- that is why liveness is an AND, and the key, token and kill-order factors remain independent kill switches.~~

**~~Skills: grants and clawback (2026-10-04)~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Skills: grants and clawback (2026-10-04)

~~Operator directive: every skill we use is available to any agent, and can be clawed back per agent or per task. Tooling: `scripts/skill-grants.sh` (a wrapper over `scripts/lib/skill_grants.py`), the root unit `corporatetraveldc-skill-grants.{service,timer}` (hourly, installed copy), and 27 tests in `tests/scripts/test_skill_grants.py`.~~

~~**Catalog: three sources.**~~

| ~~source~~ | ~~where the bytes come from~~ | ~~trust~~ |
|---|---|---|
| ~~signed~~ | ~~`skills/<name>/` in this repo~~ | ~~every file checked against `MANIFEST.sha256` before it is copied~~ |
| ~~vendor~~ | ~~the operator's `~/.claude/skills/<name>`~~ | ~~pinned by tree hash in `skills/vendor-pins.txt` (signed). The bytes are **not** committed: several vendor skills are Proprietary-licensed, and agent skills get pushed public. A copy that does not match its pin is withheld. Re-pin with `skill-grants.sh pin-vendor <name>`, then sign.~~ |
| ~~project~~ | ~~`.claude/skills/<name>/` in this repo~~ | ~~auto-loaded by any session whose cwd is the repo, so clawback is `skillOverrides: "off"` in the agent's `settings.json`~~ |

~~`skills/skill-capabilities.txt` records what each skill needs beyond its text. The pamphlet shows it, and a test fails if a grantable skill has no entry.~~

~~**Grants.** `/etc/ctdc-skill-grants.conf` (root 0644) is parsed, never sourced: `grant|deny <account|*> <skill|*> [task=<id>] [until=<ISO-8601 with offset>]`. A deny beats any grant. Expired lines lapse in both directions. A naive timestamp is rejected. If the grants file is missing or unreadable, or the checkout fails verify-manifest, apply holds and changes nothing. If the account registry does not exist yet, the `ctdc-agents` group is used.~~

*Superseded block:*
```text superseded
sudo scripts/skill-grants.sh grant ctdc-agent '*'                       # standing: everything
sudo scripts/skill-grants.sh deny  ctdc-agent-llama second-brain-remember   # per-agent clawback
sudo scripts/skill-grants.sh grant ctdc-agent-cowork flight-hifi-track --task T-2026-10-07-ep --until 2026-10-08T05:00-04:00
sudo scripts/skill-grants.sh deny  '*' docx --until 2026-10-06T00:00-04:00   # temporary fleet-wide clawback
scripts/skill-grants.sh list [account]                                   # effective view, anyone
sudo systemctl start corporatetraveldc-skill-grants.service              # apply now instead of within the hour
```

~~**Apply.** Root runs the installed copy, for every agent account. It:~~

1. ~~Keeps `~/.claude/skills` a root-owned directory. One that is agent-owned, symlinked, or not a directory is quarantined to the root-only `~/.claude/.ctdc-skills-quarantine/` and recreated.~~
2. ~~Installs each granted signed or vendor skill as a root-owned read-only copy with a `.ctdc-grant` marker (source, hash, task, until).~~
3. ~~Removes revoked or expired copies, and quarantines anything without a marker.~~
4. ~~Restores a copy that was modified in place. That counts as a finding.~~
5. ~~As the agent (privileges dropped, `O_NOFOLLOW`), merges `skillOverrides` for clawed-back project skills and the context-guardian Stop + SessionStart hooks into `settings.json`, keeping every other key. An agent that flips a managed override is reset and reported.~~

~~Every operation inside a home uses `O_NOFOLLOW` directory fds, so a planted symlink cannot steer a root write. Findings go to ntfy at priority 4, and the last report sits in `/var/lib/ctdc-liveness/skill-grants.json`.~~

~~**Limits.** A skill is instructions. Clawback removes it from the listing and makes using it a recorded violation, but it cannot stop an agent from doing the same steps by hand. The hard boundary stays on the "needs" column: secrets subset, signer, routes, vault write through the contribution route. Skills bundled with Claude Code itself are always available and are not grantable.~~

**~~Approvals, council/arena and the shared workspace (2026-10-04, Wave 2)~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Approvals, council/arena and the shared workspace (2026-10-04, Wave 2)

~~Operator directives: "An agent can request it, but it has to be signed off by a human, preferably with a clear signed message, so that any future approval gate to the phone can't be directly bypassed" and "a persistent directory that all agents can read, but I can still restrict write ... per-agent, per-task, or flat-out ... they could not erase each other's findings." Code: `src/common/governance.py`, routes in `src/web/main.py`, migration `pg_schema/0069`, tests `tests/web/test_signed_approvals.py`.~~

~~**Signed approvals (one model for everything that matters).** An approval is a human's SSH signature, namespace `corporatetraveldc-approval`, over the canonical text of ONE request: id, action, kind, requester, expiry and the sha256 of the exact command or convene spec. Change one character, one participant or the deadline and it no longer verifies; each request resolves once. It verifies only against `approval_signers`: a human's **approval key**, deliberately not the board key. The operator's board key has no passphrase, so every process running as the operator (this Claude Code session included) can use it; the approval key is passphrase-protected (or kept off-box), and `approver-ctl.sh` refuses one that opens with an empty passphrase. The server also refuses a key equal to any board key, a requester approving itself, and a signer whose board row is not an active `kind=human` (so the liveness switch revokes approval rights with everything else; `board-signer-ctl.sh deactivate|activate` moves both).~~

- ~~`scripts/approve.sh list | show ID | allow ID | deny ID` -- shows the exact request, recomputes the hash locally, asks you to type ALLOW/DENY, signs (passphrase prompt), submits to `POST /api/v1/approvals/{id}/resolve`. Off-box: `approve.sh message ID allow` + `approve.sh submit ID allow msg.sig`.~~
- ~~The phone push (sudo gate, Cowork token gate, council requests) only notifies and offers **Deny**. No key, link or tap allows any more (`db.resolve_approval_request` refuses `action=allow`).~~

~~**Council / arena.** `scripts/council.sh request --mode council|arena --subject S --participant A[:required] ...` (signed with the requester's board key) creates a convene in `requested` and an approval of kind `council`; nothing happens until a human signs it. `council.sh convene ...` is the human "force": request + `approve.sh allow` in one go. On approval every participant gets the convene on the board's `council` thread and a write grant for task `council-<id>` that expires at the deadline. Arena is blind: until a human signs the close (`council.sh close ID` + `approve.sh allow`), a participant can read only its own folder and a key-only reader none. A `:required` miss is reported to the operator by `board_sweep` -- never a kill.~~

~~**Shared workspace.** Everyone reads `Series/` through the research routes. Writes go only through `POST /api/v1/workspace/contribute` (client `scripts/workspace-contribute.sh`): signed by the account (a shared `X-Board-Key` alone is refused), scrub-gated, written by the SERVER to `Series/contributions/<task>/<account>/<UTC>-<slug>.md` with `If-None-Match: *` so nothing is ever overwritten; there is no delete, overwrite or publish route. Grants (`scripts/workspace-grants.sh`): `grant|deny <account|*> [--task ID] [--until ISO+offset]`, deny wins, expired rows are ignored, no grant = no write, `lock`/`unlock` freezes everything. Migration 0069 seeds `grant * *`.~~

~~**Publisher guardrail.** Agents draft; they never publish. No team account holds a route or credential to Executive Standard, Substack or the members site; every pamphlet (llama's included) says so. `ctdc-agent-llama` takes part like any other account through `corporatetraveldc-llama-council.timer` (`scripts/llama-council-responder.py`, runs as the service account, one convene per hour at most, skips at load1 >= 12).~~

~~**U6 (board signing).** The raw query string is signed with the path, and a signature is accepted once (`ReplayCache`), so a signed read of one vault file can no longer be replayed against another inside the 300 s window.~~

**~~Renames, scoped env and the argv sweep (2026-10-05)~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Renames, scoped env and the argv sweep (2026-10-05)

- ~~**Account names follow `ctdc-agent-<vendor>-<product>`:** `ctdc-agent` -> `ctdc-agent-anthropic-claude`, `ctdc-agent-cowork` -> `ctdc-agent-anthropic-cowork` (`ctdc-agent-openai-codex` already did). `plan.sh --rename-account FROM TO` stops the account, renames passwd/group/ home (uid, file ownership and the liveness creation clock unchanged), key filenames and comments, git author, the Claude CLI's per-path state (workspace trust), registry line, skill grants, sudoers, liveness state files and the remote-control label; `board-signer-ctl.sh rename FROM TO` carries the signer, token labels and workspace grants. The `ctdc-agent` literals left in `plan.sh`/`rollback.sh` are the one-time stage-A bootstrap (cowork-key move, remote-control hand-over) and never match again.~~
- ~~**`LLAMA_BASE_URL`** (`http://100.x.x.x:8093`, the one llama.cpp server) replaces `OLLAMA_BASE_URL`, which the code still reads as a deprecated alias; `common/llama_pool.py` derives host/port from it. Ollama's :11434 has not existed since 2026-08-27 (`semantic --ask` called it and could not have worked; now OpenAI-compatible `/v1/chat/completions`). Open: the OpenWebUI quadlet still points at :11434.~~
- ~~**Every first-party container reads a scoped secrets file** (`/etc/corporatetraveldc/svc/<service>.env`, `scripts/service-env/`): 52 quadlets moved off the 98-name `dispatch-secrets.env`. Allowlists are the union of the hand-reviewed list and `scan.py` (static import closure; `scan.py --check` must report nothing missing).~~
- ~~**No secret on a command line.** `/proc/<pid>/cmdline` is world-readable here (no hidepid), so 32 scripts that put a bearer token or the Postgres password in curl / `podman exec` argv now pass it on a private fd (`authhdr`, `-H @<(printf ...)`) or in the environment (`PGPASSWORD=... podman exec -e PGPASSWORD`).~~

**~~Agent gateway: cloud agents over OAuth + MCP (2026-10-05)~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Agent gateway: cloud agents over OAuth + MCP (2026-10-05)

~~Cloud agents (Cowork through a Claude custom connector, which also serves claude.ai and Claude Code; ChatGPT through a developer-mode MCP app or a GPT Action) reach the platform through `https://agents.example.com`: an OAuth 2.1 authorization server plus one remote-MCP endpoint per agent identity, `/mcp/<slug>` -> a team account (`scripts/agent-gateway.sh add-connector`). Code: `src/common/agent_gateway.py`, `src/web/routes/agent_gateway.py`, migration `pg_schema/0071`; tests `tests/web/test_agent_gateway.py`.~~

- ~~**Linking (once per agent).** Add the connector URL in the vendor's UI; its OAuth flow lands on our consent page, which shows an approval id. The link completes only when the operator signs it (`scripts/approve.sh allow <id>`, kind `connector-link`, covering connector, account, client and redirect). PKCE S256 is mandatory; clients come from Dynamic Client Registration (ChatGPT) or a Client ID Metadata Document (Claude's published identity).~~
- ~~**Renewal without the operator.** 1 h access tokens, rotating refresh tokens held and renewed by the vendor. No weekly nonce.~~
- ~~**Our side is the authority.** Every renewal and tool call checks the ACCOUNT -- its board signer must be active (the liveness switch, `board-signer-ctl.sh deactivate` and `revoke-tokens` all end the chain, revoke-tokens also revokes gateway connections) -- and the operator dead-man (operator login within 14 days, recorded hourly by team-liveness in `/var/lib/corporatetraveldc/team-liveness/operator-last-login`; refused, not revoked, so it resumes when the operator is back).~~
- ~~**Vendor silence is dormancy, not death.** A subscription lapsing between paychecks or a silently disabled feature only makes the SESSION `dormant` (no renewal or call for 7 days; board + ntfy notice). The account stays live (cloud agents are service-kind identities). Without a hold our refresh grant idles out after 30 days and the vendor has to re-link (a new signed consent).~~
- ~~**Operator hold.** `scripts/agent-gateway.sh hold <slug> --days N` (N <= 90) creates a `connector-hold` approval and asks for the operator's signature; while held, our side of the link -- client registration and refresh grant -- does not expire, and the vendor's next refresh resumes the session at once (notice "reconnected"). A hold never overrides a revocation on our side.~~
- ~~**Tools** (attributed to the account): status, board_read, board_post, research_list / research_read (the vault research scope, arena-aware), workspace_contribute (create-only drafts), council_request. Pamphlet for a cloud agent: `publish-pamphlet-to-vault.sh <account>` -> `Series/agents/<account>/PAMPHLET.md`.~~
- ~~**Network.** The hostname must NOT be under a Cloudflare Access application (OAuth/MCP clients send `Authorization`); nginx exposes only the OAuth, MCP and .well-known paths there and logs CF-Connecting-IP.~~
- ~~**Vendor limits (checked 2026-10-05):** ChatGPT Pro = MCP read/fetch only (writes need Business/Enterprise/Edu, or a GPT Action with OAuth against the same server); ChatGPT agent mode does not use custom MCP apps; a Claude connector belongs to the Claude account, so all Claude surfaces using it act as the same identity; Claude connector auth settings cannot be edited after adding (remove + re-add, which is also a clean revoke).~~

**~~Open questions for the operator~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Open questions for the operator

- ~~Cowork's HTTP board key (`ctdc_cowork_*` minted token) stays for its HTTP-only tasks; its SSH arrival is now the `ctdc-agent-cowork` account (answered 2026-10-04).~~
- ~~Read-only PG role name and which tables (default: everything except `audit_log` and `board_*`).~~
- ~~Whether `gui-window.sh` should run as the agent user (it owns no secrets besides the operator-created VNC password; it could).~~

**~~Gateway kill switch (2026-10-05)~~** *(former heading)*


### Team segmentation: humans and agents › Part 2 -- privilege separation (design, both groups) › Gateway kill switch (2026-10-05)

~~`agent-gateway.sh kill-all` freezes the gateway (all OAuth/MCP endpoints 503), revokes every link and disables every connector -- the full-compromise switch. `thaw` needs the operator's signature (approval kind `gateway-thaw`); connectors are then re-enabled one by one and each vendor re-links. Also on the phone console: docs/OPERATOR_CONSOLE.md.~~
