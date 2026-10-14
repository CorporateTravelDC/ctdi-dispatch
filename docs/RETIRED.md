# Retired and archived code

One index for everything taken out of service. Each retirement lives in a dated folder beside its old location,
with a README saying what it was, why it was retired and what replaced it (if anything). Nothing in these folders
is installed or run.

| Date | Location | What | Why | Replaced by |
|---|---|---|---|---|
| 2026-08-30 | `scripts/retired-20260830/ollama-keepwarm.sh` | Ollama keep-warm loop | Ollama retired in the 2026-08-27 cutover to llama.cpp | `corporatetraveldc-llama` (one resident model server) |
| 2026-10-03 | `systemd/retired-20261003/` | duplicate Quadlets (acarsdec, acarsrouter, acars-watcher, dumpvdl2) | stale second copies that had drifted from the deployed source | `.config/containers/systemd/` (single source of truth) |
| 2026-10-14 | `scripts/retired-20261014/check-pat-expiry.sh` | GitHub PAT expiry pager (`dispatch-alerts`) | never ran (no timer, wrong file paths); nothing uses the PAT (git over SSH, `gh` on its own login); PAT expired 2026-09-13 | nothing needed; topic `dispatch-alerts` retired |

Add a row whenever something is retired, in the same commit that moves it.
