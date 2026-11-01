# Agent-neutral skill catalog and gateway

Operator direction, 2026-10-29: one maintained catalog for existing and future
skills, regardless of original vendor or the agent reading them. Do not build
a parallel per-agent skill library. Claude's working native reconciliation
remains useful delivery machinery, not the source of a separate library.

## Common contract

Canonical source is skills/<name>/. Each package has SKILL.md, complete
supporting source/assets, original notices, PROVENANCE.json and a declared
needs entry in skills/skill-capabilities.txt. skills/catalog.json is a generated
review index; it never grants access. scripts/skills-catalog.py --check checks
coverage, provenance records, upstream hashes and the index. Run it before
release and after every skill addition/update.

The common gateway tool is skill_read:
- {} or {"name":"*"}: legacy list of granted signed package names.
- {"name":"*","path":"*"}: verified metadata for those granted packages.
- {"name":"NAME"}: instructions.
- {"name":"NAME","path":"*"}: verified relative file listing.
- {"name":"NAME","path":"PROVENANCE.json"}: attributable source record.
- {"name":"NAME","path":"references/FILE.md"}: supporting UTF-8 text.

The same code path applies to all connector accounts. Grants, denies, expiry,
manifest verification, path checks and scrub checks still apply. Package
metadata comes from verified package bytes. Catalog index entries do not
authorize a missing skill. The no-name default list remains backwards
compatible; metadata mode fails closed if a granted package cannot verify.

## Reconciled source inventory

The 16 previously pin-only Claude vendor skills are copied from Codex's own
root-managed granted copies only after each complete tree matches the existing
signed vendor pin. All original files are retained under upstream/ with a
portable adapter at the existing canonical name. Provider provenance records
the Anthropic vendor distribution; it does not replace third-party authorship
or license notices inside that distribution. No other agent home is read.

Existing CTDC signed skills join the same metadata/needs contract. The project
personal-export-analysis package becomes canonical with a source-preserving
adapter; its .claude/.agents copies are delivery mirrors. The generalized
guardian remains canonical. OpenAI's nine namespaced imports preserve twelve
upstream skill entrypoints; names distinguish actual packages, not consumers.
The morning skill's earliest upstream origin remains unverified; recording
CorporateTravelDC as maintainer does not establish original authorship.

## Capability-aware use

Every granted skill can be inspected through MCP after release. That is not a
claim every skill can execute in every client. Browser tools, desktop control,
file generation, vendor memory and external connectors need the actual exposed
capability and approved access. Missing tools produce a clear limitation and
the useful authorized draft/read result. No generic shell/execution tool is
added. The gateway does not install plugins or register new connectors.

Native local hooks remain client adapters because Codex and Claude emit their
own events. Cloud clients use their own gateway status/research/workspace
tools. No automatic cloud compaction hook is invented. Source and task logic
are shared; hooks and filesystem copies are delivery mechanisms.

## Existing and future release

Use the stable package name and provenance contract for every addition,
including local skills. Keep original notices and source hashes. Run the
catalog checker and affected tests, reconcile existing prepared fixes, pass
the actual operator preflight, sign the reviewed tree, install managed copies,
and roll out the provenance-checked runtime. Validate own-identity discovery,
metadata and references; test denial and package tampering in isolated
fixtures. Trust/activate local hooks with the actual client as the human.

This document describes prepared implementation, not a signed/live release.
Agents cannot apply their own handoff or perform human signing, root-managed
installation or client hook trust. Full rollout is requested but remains an
operator step until execution evidence exists.

## Scope boundary

The five repositories granted in the account pamphlet were checked for skills/
and .claude/.agents skill entrypoints. Only dispatch has such packages in those
locations. Native bundled/plugin sources readable in this account are covered
by the earlier OpenAI import. Other accounts' private homes and unregistered
software are not inspected; an operator-owned inventory must check any
additional installation roots before claiming whole-device exhaustiveness.
