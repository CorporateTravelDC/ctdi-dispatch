# Pamphlet delivery to local accounts and connectors

Prepared 2026-10-29. This design joins the earlier corrected registry-based
renderer to automatic local delivery. The release relay must install and verify
it before it is called deployed.

## Why delivery was incomplete

The old weekly operator-user job published vault editions only. Its comments
explicitly excluded home installation, which required a separate root command.
Earlier cloud overlays also assumed a vendor determined where a client ran.
Neither a connector identity nor a CLI product name establishes the Unix UID of
the current session. The connector in the recovery session is ChatGPT while the
local operator-hosted coding session is Codex; those identities remain distinct.

An account's private home remaining inaccessible from a different UID is an
intentional boundary. Vault tools are not the only delivery mechanism, and no
agent should borrow another account or shared credentials to retrieve a copy.

## One renderer, two delivery channels

The registry-based renderer is shared. The existing vault publisher includes
the account-specific integration preamble. The installed root local helper
executes signature verification and rendering as the operator, never root, and
installs only the resulting Markdown into independently validated directories.
It refuses an unsigned/divergent source tree or a stale installed helper.

Interactive local selection is registry kind=agent, activated (preloaded=0),
interactive login, and a valid passwd home/shell. A nologin service explicitly
deployed on this device can instead be listed in the root-managed
`/etc/ctdc-pamphlet-local-services.conf`, one registered service account per line.
Cloud/service location is never inferred from its vendor or login mode. The
installer seeds this policy only once and preserves operator additions. Neither
delivery route creates an account, activates it or grants access. There is no
account-name vendor branch.
Claude and Codex accounts receive their native files. A future on-device Grok
client can be provisioned as an ordinary interactive account (for example the
existing ssh login mode); no Grok login implementation or automatic native
hook is invented by this change.

Local targets are root-owned mode 0444:

- `PAMPHLET.md`: the canonical local copy for any client.
- `CLAUDE.md` and `AGENTS.md`: existing native discovery names.
- `.codex/AGENTS.md` only when that client directory already exists.

The helper does not create client configuration directories, follow home/client
symlinks, or overwrite unrelated native instructions. Its directory handles and
atomic replacements prevent symlink redirection. A failure in one account is
reported and makes the pass fail; successful accounts are not described as all
accounts succeeding. Unchanged generations verify existing outputs without
rewriting the Markdown on every timer tick.

Operator-readable mirrors and root receipts live at
`/var/lib/ctdc-agent-pamphlets/ACCOUNT/PAMPHLET.md` and `receipt.json`. The parent
is root:operator 0750. This gives operator-hosted remote sessions a stable local
place to read the appropriate account's reference without opening private homes
or changing the session's identity. It deliberately does not overwrite the
operator's AGENTS.md scratchpad or silently impersonate a dedicated agent.
Already-running sessions must reread instructions; a file copy cannot force a
model to discard cached context.

## Automatic triggers and deployment acceptance

The root-managed install service has a path unit watching the signed manifest,
account registry, grants, and a fixed deployment-request marker. Its timer runs
after boot and every 15 minutes to catch missed events or new client directories.
Root creates the request marker; only the operator group can write it. The marker
contains no command or destination and never bypasses source verification.

The weekly user service and serialized deployment invoke
`scripts/agent-pamphlet-refresh.sh`. That entrypoint verifies the signed source,
requests root-managed local delivery, publishes the vault editions and waits for
matching local source/input/document receipts checked after the request. A
failed vault publication or failed/stale local receipt fails deployment
acceptance; it does not report success based on one channel alone. No user-job
sudo rule, root renderer or hard-coded vendor list is needed.

The release installs the root-owned helper/units through install-root-copies.sh,
enables the path and timer, initializes local delivery, and installs the updated
weekly user service. New eligible local accounts are picked up on registry
change; service/cloud identities retain their connector/vault delivery.

## Validation boundaries

Tests exercise synthetic accounts including a future vendor name, current and
changed generations, root receipt policy, unmanaged-file protection, symlink
refusal, preserved client settings, and failures of either deployment channel.
They do not write real account homes, sign anything, contact the vault, activate
hooks or reload Wi-Fi hardware. Real per-account readback, receipt checks and
connector reads remain operator deployment acceptance checks.
