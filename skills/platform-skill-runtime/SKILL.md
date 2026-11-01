---
name: platform-skill-runtime
description: Use a platform skill through the common signed catalog and identity-aware gateway; check its provenance, actual tool requirements and authorized output before following its task instructions.
metadata:
  original_source: CorporateTravelDC
  version_controlled_author: CorporateTravelDC
---

# One skill catalog, one access contract

All platform-maintained skills use canonical packages in skills/<name>/.
Original source may be Anthropic, OpenAI, CorporateTravelDC or explicitly
unverified. Provider names describe provenance, never a separate agent library.

1. Discover through your own gateway identity: skill_read name="*".
   With path="*" as well, inspect the verified metadata for granted packages.
2. Read a selected package's SKILL.md; use path="*" to list its verified files
   and path="PROVENANCE.json" to inspect its source record. Read supporting
   text only as needed. The gateway reads code as text; it does not execute it.
3. Check the declared needs and the tools actually exposed in this session.
   A grant to read a skill is not a browser, file writer, shell, credential,
   administrator role, approval, plugin installation or publishing permission.
   If a needed tool is absent, complete the authorized draft/read portion and
   state what cannot run. Never impersonate another account or seek its keys.
4. Apply the current operator/account rules over incompatible legacy vendor
   instructions. Vendor tool names are descriptions of required capabilities,
   not permission to resurrect a retired bridge or invent an unavailable tool.
   Preserve specialized workflows and source constraints when adapting calls.
5. Use the same skill and task logic for every eligible agent. Only physical
   interfaces vary: client-native lifecycle hooks, actual tool APIs, filesystem
   availability and approved connections. A cloud connector supplies no shell.
6. Save durable sanitized decisions through the attributed workspace. Skills,
   source signing, deployments, credentials and public publication follow the
   human-controlled release path. No agent approves its own request.
7. Deliver completed work in the application's Library with an identified item
   when that real capability exists; otherwise include readable output inline.
   A local file or vault path alone is not an inline delivery.

Local skill copies are managed delivery mirrors, not independent sources.
The context guardian is a specialized lifecycle adapter to this shared model,
not a substitute for fresh evidence, canonical policy or durable task notes.

# New or updated skill

Keep the canonical name stable. Track all supporting files and original
notices; add PROVENANCE.json and a skill-capabilities.txt needs entry.
For imported bytes, record their paths, sizes and hashes, with an upstream
version when actually known. Do not invent missing authorship or a version.
Use the common catalog validator and source/reader/grant tests; review the
public scrub's derivative identity where applicable. Only after human signing,
managed installation and runtime release should live acceptance be claimed.

Every skill must be discoverable and readable subject to its account grant.
Execution remains conditional on actual tools and permissions. Binary assets
can be listed and versioned; this text-only reader does not transfer them.
