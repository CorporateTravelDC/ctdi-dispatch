# Codex private-clone launch draft — 2026-10-31

Authority: the account pamphlet; docs/AGENT_SEGMENTATION.md; the operator's
2026-10-31 instructions to use the agent's own clone and stop using the failing
shell sandbox. This is an account-specific launch draft, not deployed state.

The own-account config has no explicit sandbox settings. The session permission
profile nevertheless names nonexistent read-only mount targets under the
operator checkout, including .codex. The observed bwrap failure occurs before
the requested command starts. No operator-repository directory is to be added
to accommodate that failure. The source of the injected profile is unverified.

scripts/codex-private-clone.sh checks the agent identity and private-clone owner,
sets that clone as the working root, and requests an unsandboxed CLI session.
It uses the installed CLI's locally verified flags. --no-daemon avoids attaching
the new interactive process to an existing daemon with the wrong task root.
config/codex/ctdc-agent-openai-codex.config.toml is the corresponding settings
fragment for operator review; it is not copied into the account config here.
The launcher and fragment do not alter an already-running remote-control task.

Unix permissions, the account's API grants, managed policy, signed manifests,
and operator-only installation/signing/commit/push rules remain in force.
No service is restarted, no credential is copied, no model is downloaded, and
no vendor source or skill is installed by this draft. No sandbox-bypass flag
is added to other agent accounts or to the shared platform configuration.
