# Shared development relay

Operator-requested 2026-10-29. This is a prepared addition until the operator
applies, reviews, signs and provisions the source. Agents never commit/sign/push.
The folder is a code handoff/review area, not the durable Second Brain vault.

| Path | Owner:group and mode | Purpose |
|---|---|---|
| `/srv/ctdc-dev` | `root:ctdc-dev` 2750 | Stable team-readable root, no agent write |
| `submissions/` | `root:ctdc-dev` 3770 | Each submitter writes an owned submission directory |
| `relays/` | `root:ctdc-dev` 3770 | Each writer owns a review/status/relay draft directory |
| `checkouts/` | `corporatetraveldc:ctdc-dev` 2750 | Operator creates and writes Git checkouts; team reads |

Keep editable clones in the owning account's home. Do not use a group-writable
Git index, object database, config or hooks for operator commits. Existing
`/srv/ctdc-handoff` remains the general handoff/intake; nothing is moved or retired.
The blog and every private agent folder retain their own access rules.
Cloud-only agents have no filesystem session; share their attributed vault
contribution path on the board and let the operator import reviewed material.
No access to other agents' private work is granted by this addition.

## Submission and attribution

From your own clone, use the existing helper with the development drop:

```bash
HANDOFF_DROP=/srv/ctdc-dev/submissions scripts/handoff-put.sh reviewed-change-v1 --subject "Reviewed change v1"
```

The helper's `<name>-from-<account>/<name>.patch` contains the Unix account,
base commit, UTC time and subject. Report its SHA-256 through your attributed
vault contribution. Include intent, repository-relative paths, tests/exit status,
limitations and predecessor/supersession references there. Never overwrite another
account's entry; publish a new version. Modes inside submission directories must
allow team read and deny group/other write (the helper uses 2755/0644).

Sticky directories protect against unlink/rename by another unprivileged member;
they do not make files immutable or prevent their owner from revising them. A
filename and Git author text alone are not proof of identity. Existing apply
checks validate Unix ownership and the separately reported hash after making a
private copy. These establish pinned handoff provenance, not a new cryptographic
signature over the submission. Root/operator access remains in the threat model.

Operator contributions are also welcome: put review notes or a source patch in
an operator-owned `relays/<name>-from-corporatetraveldc/` directory (2750, files
0640) and record the reviewed commit/hash in Jarvis. `handoff-apply.sh` accepts
`ctdc-*` agent names only and refuses self-apply: do not pretend an operator
submission was from an agent to bypass it. The operator reviews their own staged
work in an operator-owned checkout and uses the normal signed release workflow.

## Operator provisioning and checkout

After source review/signing, the updated `srv-layout.sh` provisions the four
folders and `verify.sh` checks their ownership/modes. The ordinary root-owned
installation / signed relay process still applies; agents must not execute it.
The layout preflight refuses symlinks, non-directory collisions and unexpected
owners before any layout mutation. Do not recursively chown a shared tree.

For this new, currently absent tree, these explicit provisioning commands create
only the four directories. Run as the human operator; inspect existing paths
first and stop on a symlink, collision or unexpected owner. Do not blindly reuse
this creation block against an existing tree.

```bash
sudo install -d -m 2750 -o root -g ctdc-dev /srv/ctdc-dev
sudo install -d -m 3770 -o root -g ctdc-dev /srv/ctdc-dev/submissions /srv/ctdc-dev/relays
sudo install -d -m 2750 -o corporatetraveldc -g ctdc-dev /srv/ctdc-dev/checkouts
```

In the operator's own terminal, create an operator-owned review clone. This
clone's origin points to the local private source; it is not a publication remote.
Use the existing private/public signed release workflow for eventual publication.

```bash
umask 027
git clone --no-hardlinks /opt/corporatetraveldc/private/ctdi-dispatch-internal /srv/ctdc-dev/checkouts/ctdi-dispatch-internal
git -C /srv/ctdc-dev/checkouts/ctdi-dispatch-internal config core.sharedRepository false
```

Before applying, verify the whole checkout including `.git` and its ancestors has
no agent-writable paths or named ACL grants; do not add global `safe.directory=*`.
Initialize the repository's normal operator checks through the approved workflow.
Never execute a contributed relay draft just because it is in this directory.
Review the pinned bytes and run relay lint/preflight and the existing human
approval/signature flow. No directory watcher executes submissions automatically.

Concrete migration of the already delivered flight patch: its current shared
path and hash remain valid; the operator can place the review worktree here
without copying or re-attributing the agent-owned patch.

```bash
HANDOFF_WORKTREE_PARENT=/srv/ctdc-dev/checkouts /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/handoff-apply.sh ctdc-agent-openai-codex /srv/ctdc-handoff/flight-skill-governance-20261029-v3-from-ctdc-agent-openai-codex/flight-skill-governance-20261029-v3.patch a89e2076d400b33220de6643b39cbe3416a290d9aa60e82afe87b7a2074f9adc
```

This makes a detached review worktree, not a commit. With a new submission in
`submissions/`, set both `HANDOFF_DROP=/srv/ctdc-dev/submissions` and
`HANDOFF_WORKTREE_PARENT=/srv/ctdc-dev/checkouts` when using the existing apply
helper, and provide the actual account/path/hash returned at submission time.
Choose unique names; an existing worktree is never overwritten. For simultaneous
reviews use separate worktrees, then one operator serially reconciles them into
a clean integration tree. Preserve submitter attribution in a reviewed source
manifest or commit message naming the account, base and pinned patch hash; follow
the canonical corporate Git authorship policy (no session URLs or forbidden
co-author trailers). Human commit identity and cryptographic signatures stay human.

The Second Brain repair complements the flight patch. Applying both independently
at the same base produces two review worktrees, not a combined result: reconcile
both diffs into the integration tree before testing and signing. The earlier
flight v1/v2 patches are superseded by v3 and must not be stacked on it.

## Verification still required on the real tree

Isolated checks of this change cover plan output, collision refusal and the
shared-root ACL-grant refusal. They do not prove cross-UID isolation, SELinux
labels, operator checkout ACLs, successful commit/sign/push, backup/restore or
remote-agent filesystem access. Validate these after provisioning, without
exposing credentials or executing the entire privileged verification script as
an agent. No shared directory is a backup: record durable findings and receipts
in Jarvis and decide an explicit backup policy for operator review checkouts.
