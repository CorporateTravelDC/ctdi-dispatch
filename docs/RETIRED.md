# Retired and archived code

One index for everything taken out of service. Each retirement lives in a dated folder beside its old location,
with a README saying what it was, why it was retired and what replaced it (if anything). Nothing in these folders
is installed or run.

| Date | Location | What | Why | Replaced by |
|---|---|---|---|---|
| 2026-08-30 | `scripts/retired-20260830/ollama-keepwarm.sh` | Ollama keep-warm loop | Ollama retired in the 2026-08-27 cutover to llama.cpp | `corporatetraveldc-llama` (one resident model server) |
| 2026-10-03 | `systemd/retired-20261003/` | duplicate Quadlets (acarsdec, acarsrouter, acars-watcher, dumpvdl2) | stale second copies that had drifted from the deployed source | `.config/containers/systemd/` (single source of truth) |
| 2026-10-14 | `scripts/retired-20261014/check-pat-expiry.sh` | GitHub PAT expiry pager (`dispatch-alerts`) | never ran (no timer, wrong file paths); nothing uses the PAT (git over SSH, `gh` on its own login); PAT expired 2026-09-13 | nothing needed; topic `dispatch-alerts` retired |

Added 2026-10-15: retirement folders that existed before this index and were
missing from it (found by `git ls-files | grep retired`). Until these rows were
added, the "one index for everything" claim above was not true: six folders were
missing. The "why" column comes from the commit that created each folder.

| Date | Location | What | Why | Replaced by |
|---|---|---|---|---|
| 2026-08-03 | `.config/systemd/user/retired-20260803/` | `corporatetraveldc-ollama-prewarm.{service,timer}`: one combined Ollama prewarm unit | replaced by per-skill prewarm units (commit `752e0e0e`) | per-skill prewarm timers, themselves retired 2026-08-14 (next row) |
| 2026-08-14 | `.config/systemd/user/retired-20260814/` | 14 per-skill `corporatetraveldc-ollama-prewarm-<skill>.{service,timer}` pairs (28 files) | per-skill prewarm was pointless once all skills shared two models; units masked and moved in `ea15e16f` (2026-08-15 05:19Z; `docs/DRIFT_GAPS_REPORT_2026-08-15.md` D6 cites it as `2ff1fbb`, a hash no longer in history); Ollama itself retired 2026-08-27 | nothing (one resident llama.cpp server) |
| 2026-08-16 | `.config/systemd/user/retired-20260816/` | `aam-daily-watch-pm.timer`, `aviation-daily-watch-pm.timer` (second daily runs) | made redundant when the daily watches moved to a recurring cadence (commit `081c3b81`) | the recurring cadence, now the rolling maintenance queue (row 2026-10-04) |
| 2026-08-30 | `.config/systemd/user/retired-20260830/` | `corporatetraveldc-ollama-keepwarm.{service,timer}` | Ollama retired in the 2026-08-27 llama.cpp cutover (commit `0ea42447`) | `corporatetraveldc-llama` |
| 2026-10-03 | `nginx/conf.d/retired-20261003/` | `executivestandard.example.com.conf`, the self-hosted mirror vhost of The Executive Standard | public hostname retired from the Pi, tunnel ingress and vhost both (commit `e4ce37c4`) | Substack custom domain (DNS-only CNAME, never reaches the Pi) |
| 2026-10-04 | `.config/systemd/user/retired-rolling-queue-20261004/` | 9 per-job timers: six `*-daily-watch`, `disruption-weather-digest`, `transport-pattern-digest`, `ep-advance-venues` | moved into the rolling maintenance queue (commit `b8b02b35`) | `corporatetraveldc-maintenance-dispatch.timer` + `scripts/lib/maintenance-queue.txt` |

**READMEs missing (2026-10-15).** Every folder is supposed to carry a README,
but only `systemd/retired-20261003/` and `scripts/retired-20261014/` do.
`scripts/retired-20260830/`, `nginx/conf.d/retired-20261003/` and the five
`.config/systemd/user/retired-*` folders have none. This index is their only description until READMEs are
added.

Not yet retired, still in active directories: the tracked
`corporatetraveldc-mcpo.service` and `corporatetraveldc-mcpo-public.service`
in `.config/systemd/user/` (MCP bridges retired 2026-08-18 and not installed
live). They should move to a `retired-*` folder with a row here.

Add a row whenever something is retired, in the same commit that moves it.
