# Second Brain — Current State (template)

_Living status doc for the "second brain" workstream. Update this file (not
chat memory) whenever second-brain work happens. This is the source of
truth — if an agent's chat memory conflicts with this file, this file wins._

This is a placeholder template. The real `docs/SECOND_BRAIN_STATUS.md`
(operator-specific paths, task detail, and vault layout) is intentionally
excluded from the public mirror -- see `scripts/scrub-public-tree.py`'s
`DROP_FILES`. Fill in the sections below for your own deployment.

## Status

| Task | Status | Notes |
|---|---|---|
| Vault deployment | _todo_ | Where your notes/files vault is hosted and how it's reached. |
| Vault index | _todo_ | Path to any indexing tool/output, and whether it's on a refresh schedule or one-shot. |
| Entity ingest | _todo_ | Which sources feed contact/client entities, and which are stubbed pending credentials. |
| Folder taxonomy | _todo_ | If you're using a PARA/Karpathy-method style layout (Inbox/Sources/Concepts/Entities/Syntheses/etc.), note whether the folders actually exist yet or only the plan does -- a planning discussion is not a migration. |
| Other linkages | _todo_ | Any other systems tied into the vault. |

## Explicitly deferred — do not resurface

- _List anything your operator has explicitly deferred here, so future
  agent sessions don't re-suggest it._

## Vault file layout

- _Describe your own vault's folder structure here._
- **Recommendation:** keep business/operational content under one dedicated
  top-level folder (e.g. named after your project/service), separate from any
  personal files also stored in the same vault (photos, personal documents,
  phone auto-upload folders). A generic folder name like "Docs" at the vault
  root is easy to confuse with personal use of a folder with the same name --
  namespace it under your project's own root instead.

## Open gaps

- _List known gaps -- e.g. no refresh timer, no live CRM feed, no upload
  folder convention -- so they're visible without digging._
- _If you designed an ambitious folder taxonomy or automated ingest pipeline
  in a planning conversation, explicitly confirm here whether it was actually
  built, or just discussed. Planning-vs-built drift is an easy, recurring
  failure mode for this kind of doc -- don't let "we talked about it" read as
  "it's done."_
- _If any ingestion path writes automatically-generated content into your
  vault, note whether it's actually been run end-to-end against your real
  storage backend, not just unit-tested in isolation -- container network
  scoping (a service bound to loopback-only, for instance) is a common way
  for code that works when run directly on the host to fail silently (or
  loudly) the first time it runs inside a container on a schedule._
- _When a container needs to reach a loopback-bound host service, reach
  for the narrowest documented opt-in your platform's own network policy
  already describes (a per-container alias, an existing reverse-proxy
  vhost already listening more broadly, etc.) before reaching for full
  host networking. Host networking usually "just works" on the first try,
  which is exactly why it's tempting under time pressure -- but it also
  grants that one container the host's entire network stack with no
  isolation boundary, which is a different risk class than the one
  scoping problem it was reached for. Note in this doc which mechanism
  was used and why, not just that connectivity was achieved._
