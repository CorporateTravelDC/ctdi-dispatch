#!/usr/bin/env bash
# ctdc-agent-anthropic-cowork is a cloud client (claude.ai Cowork): it has no Claude Code CLI on this
# box, so there is no on-box relink. Relink it from claude.ai's connector settings (connector `cowork`,
# MCP URL https://agents.example.com/mcp/cowork); the consent page asks for the operator's
# connector-link signature the same way.
echo "cowork links from claude.ai (connector settings -> https://agents.example.com/mcp/cowork); nothing to run on the box." >&2
exit 2
