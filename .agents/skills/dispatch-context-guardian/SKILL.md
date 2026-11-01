---
name: dispatch-context-guardian
description: Preserve a sanitized operational checkpoint across long tasks, compaction and resume in Codex, Claude Code or connector clients; troubleshoot the client-specific lifecycle integration.
metadata:
  original_source: CorporateTravelDC
  version_controlled_author: CorporateTravelDC
  version: 2.0.0
---

# Dispatch Context Guardian

A client-neutral operational checkpoint with client-specific delivery. It
supplements the task history and vault; it never replaces canonical rules,
authorization or fresh live verification. Original source and version-controlled
author: CorporateTravelDC. The original implementation targeted Claude Code.

## Local clients

`scripts/context_hook.py --client codex` consumes native lifecycle JSON.
PreCompact saves; SessionStart restores the same session; Stop can optionally
save without relying on a Claude transcript parser or fixed token threshold.
The adapter always exits zero and never triggers compaction or approval.
`--client claude` uses the same core. Native hook support and registration must
be verified for the actual installed client before calling the guardian active.

Read `references/client-hooks.md` for the reviewed integration shapes. The
`hooks.codex.json` template uses the granted user-scope package; registration
and Codex hook trust are operator steps. Do not overwrite existing hook config
or trust a hook on the operator's behalf.

The core reads three public GET endpoints at the configured platform's fixed
Tailscale API base: health, feeds and CPS. It stores allowlisted status, numeric
score, feed counts and fetch availability only. No runsheet, passenger data,
flight identifiers, raw API bodies, keys, token, transcript or settings file is
read or saved. A fetch failure is explicit, not a clean-health assertion.

Snapshots use `~/.local/state/ctdc-context-guardian/<client>/<session-hash>.json`
with private directory/file modes, atomic replacement and a versioned schema.
Client/session separation prevents unrelated tasks from sharing a snapshot.
Restore marks age and staleness, rejects legacy/raw snapshots and reprojects
allowlisted scalar fields before returning text. This is not a replacement
for a durable work checkpoint: record sanitized decisions, pending questions,
source revisions and evidence references in the authorized vault workspace.

## ChatGPT and Cowork through MCP

Read this signed skill and references with this account's `skill_read` tool.
A connector reading a skill does not install local hooks or give shell access.
Use the connector's `status` for the permitted current summary, record a
sanitized task checkpoint with `workspace_contribute`, and restore it with
`research_read` through that same identity. Do not claim an automatic cloud
compaction hook exists unless the client actually exposes one. Preserve pending
decisions in the task's main replies as well as any interactive question card.

## Rollout boundary

This signed catalog copy is canonical; `.claude/skills` and `.agents/skills`
are identical project mirrors for discovery and existing hook paths. The grants
catalog now exposes the canonical skill to eligible connector readers. A signed
release and an effective grant are still needed; an agent-owned draft or a
copied skill file alone is not an installed or trusted runtime hook.
