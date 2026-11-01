---
name: mcp-builder
description: Guide for creating high-quality MCP (Model Context Protocol) servers
  that enable LLMs to interact with external services through well-designed tools.
  Use when building MCP servers to integrate external APIs or services, whether in
  Python (FastMCP) or Node/TypeScript (MCP SDK).
metadata:
  original_source: Anthropic
  version_controlled_author: CorporateTravelDC
---

# mcp-builder

Use the shared platform-skill-runtime contract through your own identity.
Read upstream/SKILL.md for the preserved task workflow. Resolve its relative
references and helper paths under upstream/. All original supporting files
and notices are there. Read PROVENANCE.json for exact source identity.

A readable skill grants no missing browser, shell, connector, credential or
approval. Adapt vendor tool names only to equivalent tools actually exposed
in this session; follow current operator/account rules over legacy wording.
Do the useful authorized portion and report unmet requirements.

Needs: local file I/O; registering an MCP server = operator-only
