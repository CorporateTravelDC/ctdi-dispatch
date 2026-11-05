#!/usr/bin/env bash
# Link or relink ctdc-agent-anthropic-claude-ea to its gateway connector "claude-ea". Run as corporatetraveldc, in screen claude-ea:
#     bash /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/relink/ctdc-agent-anthropic-claude-ea.sh
exec "$(dirname "$0")/../agent-relink.sh" ctdc-agent-anthropic-claude-ea --slug claude-ea "$@"
