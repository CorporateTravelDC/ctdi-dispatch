# Client lifecycle integration

Prepared 2026-10-29. Codex source: https://learn.chatgpt.com/docs/hooks
(retrieved 2026-10-29). Native events include PreCompact and SessionStart;
common fields include session_id and hook_event_name. The transcript format
is explicitly not stable and is not used here. A SessionStart additionalContext
is delivered to the resumed model. Hooks are discovered beside active config
layers as hooks.json or inline hooks tables. Non-managed hooks require review
and trust of their exact definition; `/hooks` exposes that UI.

The included hooks.codex.json is a template, not an active installation.
After the operator releases and grants the package, merge it into the selected
Codex hook layer without replacing unrelated hooks, then review/trust it using
the client. The command uses the current account's granted ~/.agents copy.
SessionStart restores only when that session has a compatible snapshot;
an initial start reports absence explicitly. PreCompact saves before either
manual or automatic compaction. Stop is optional; it does not inspect context
occupancy and does not cause compaction. Existing Claude Stop/SessionStart
registrations can target the mirrored adapter with --client claude. Existing
registration was not changed or verified by this prepared patch.

Cloud connector clients have no local filesystem lifecycle hook in this
implementation. Use permitted status/research/workspace tools and manual
checkpoint references. Tool availability and client lifecycle are independent
from skill grants. No master token, SSH identity, production file or automatic
approval is needed or used by this core.
