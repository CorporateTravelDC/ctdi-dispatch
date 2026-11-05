#!/usr/bin/env bash
# Link or relink ctdc-agent-anthropic-claude to its gateway connector "claude-code". Run as corporatetraveldc, in screen claude-ea:
#     bash /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/relink/ctdc-agent-anthropic-claude.sh
exec "$(dirname "$0")/../agent-relink.sh" ctdc-agent-anthropic-claude --slug claude-code "$@"
