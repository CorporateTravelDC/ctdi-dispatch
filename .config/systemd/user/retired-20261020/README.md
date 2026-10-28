> Status 2026-10-25: re-checked 2026-10-26 UTC against HEAD 5f7fca01 and the live box. Nothing below is out of date: none of these units is installed live.

# Retired 2026-10-20 -- dead user units (cleanup sweep)

Nothing here is installed or run. systemd does not read this subdirectory.
Indexed in `docs/RETIRED.md`.

| File | What it was | Why retired | Replaced by |
|---|---|---|---|
| `corporatetraveldc-mcpo.service` | mcpo MCP-over-OpenAPI bridge, full toolset, 127.0.0.1:8082 | MCP bridges retired 2026-08-18; never installed live since; its ExecStart wrapper (`~/mcp/dispatch-mcp/dispatch-mcp-wrapper.sh`) no longer exists (checkout archived as `dispatch-mcp.archived-20260817`); a re-install footgun | the built-in agent gateway (`src/web/routes/agent_gateway.py`) |
| `corporatetraveldc-mcpo-public.service` | same bridge, public-safe toolset, 127.0.0.1:8083 | same | same |
| `corporatetraveldc-nms-v240-check.timer` | one-shot timer for the FAA NMS v2.4.0 post-deploy check | `OnCalendar=2026-08-08 02:30:00` is past; it fired once and never fires again | nothing (one-shot done). Its container is in `.config/containers/systemd/retired-20261020/`. The two skills `src/poller/skills/nms_v240_{baseline_capture,post_deploy_check}.py` stay in the tree (no unit runs them) |
| `ops-brief-rebuild-watcher.service` + `.timer` | one-time "rebuild the poller image once thermal-ingest-guard is at tier 0" watcher | ran an untracked, unsigned script in the home directory (`%h/ops-brief-rebuild-watcher.sh`); timer disabled; its job is long done | nothing (rollouts rebuild images: `scripts/serialized-rollout.sh`) |

The cleanup relay (2026-10-20) disables these units and removes their live copies.
