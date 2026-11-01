---
name: chrome-browser
description: Read this skill before the first step that uses Claude in Chrome, the
  browser extension whose tools are named mcp__claude-in-chrome__* (also called Chrome,
  the browser extension, or the external browser) and which acts in the person's real
  Chrome with their own sign-ins; before those tools are turned on there may be a
  single enable__mcp__claude-in-chrome tool instead. It covers loading the tools in
  one ToolSearch call, checking the person's open tabs and working in a new tab, site
  permissions, GIF recordings, console logs, dialogs to avoid, and when to stop and
  ask. It is not for the built-in browser (mcp__Claude_Browser__* or mcp__remote-devices__Claude_Browser__*
  tools), which has its own skill, and it does not decide which browser to use.
metadata:
  original_source: Anthropic
  version_controlled_author: CorporateTravelDC
---

# chrome-browser

Use the shared platform-skill-runtime contract through your own identity.
Read upstream/SKILL.md for the preserved task workflow. Resolve its relative
references and helper paths under upstream/. All original supporting files
and notices are there. Read PROVENANCE.json for exact source identity.

A readable skill grants no missing browser, shell, connector, credential or
approval. Adapt vendor tool names only to equivalent tools actually exposed
in this session; follow current operator/account rules over legacy wording.
Do the useful authorized portion and report unmet requirements.

Needs: a browser surface on the client; none on this box
