# Retired and archived code

One index for everything taken out of service. Each retirement lives in a dated folder beside its old location,
with a README saying what it was, why it was retired and what replaced it (if anything). Nothing in these folders
is installed or run.

| Date | Location | What | Why | Replaced by |
|---|---|---|---|---|
| 2026-08-30 | `scripts/retired-20260830/ollama-keepwarm.sh` | Ollama keep-warm loop | Ollama retired in the 2026-08-27 cutover to llama.cpp | `corporatetraveldc-llama` (one resident model server) |
| 2026-10-03 | `systemd/retired-20261003/` | duplicate Quadlets (acarsdec, acarsrouter, acars-watcher, dumpvdl2) | stale second copies that had drifted from the deployed source | `.config/containers/systemd/` (single source of truth) |
| 2026-10-14 | `scripts/retired-20261014/check-pat-expiry.sh` | GitHub PAT expiry pager (`dispatch-alerts`) | never ran (no timer, wrong file paths); nothing uses the PAT (git over SSH, `gh` on its own login); PAT expired 2026-09-13 | nothing needed; topic `dispatch-alerts` retired |
| 2026-10-20 | `scripts/retired-20261020/` (README) | `migrate-sqlite-to-pg.py`, `migrate-second-brain-to-pg.py`, `migrate-demo-to-pg.py` (one-shot SQLite-to-Postgres migrators), `demo-sqlite-retire-check.py` (demo relay-4 prep) and its test | operator decision 2026-10-19: only Pi-hole, vnstat and ACARS Hub touch SQLite; every source file these scripts read is destroyed (d5fde567) and the demo sqlite rollback is removed | nothing; Postgres only (`db_backend.backend()` refuses sqlite outside the test suite, `tests/common/test_no_platform_sqlite.py`) |

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

~~Not yet retired, still in active directories: the tracked
`corporatetraveldc-mcpo.service` and `corporatetraveldc-mcpo-public.service`
in `.config/systemd/user/` (MCP bridges retired 2026-08-18 and not installed
live). They should move to a `retired-*` folder with a row here.~~
(2026-10-20: moved; rows below.)

Added 2026-10-20 (cleanup sweep). Both folders carry a README.

| Date | Location | What | Why | Replaced by |
|---|---|---|---|---|
| 2026-10-20 | `.config/systemd/user/retired-20261020/` | `corporatetraveldc-mcpo.service`, `corporatetraveldc-mcpo-public.service` | MCP bridges retired 2026-08-18, never installed live since; ExecStart wrapper path no longer exists | built-in agent gateway (`src/web/routes/agent_gateway.py`) |
| 2026-10-20 | `.config/systemd/user/retired-20261020/` | `corporatetraveldc-nms-v240-check.timer` | one-shot `OnCalendar=2026-08-08 02:30:00`, past, still enabled live | nothing (one-shot done) |
| 2026-10-20 | `.config/systemd/user/retired-20261020/` | `ops-brief-rebuild-watcher.{service,timer}` | one-time poller-rebuild watcher running an untracked script in `~`; timer disabled | nothing (rollouts rebuild images) |
| 2026-10-20 | `.config/containers/systemd/retired-20261020/` | `corporatetraveldc-ops-brief-deferred.container` | deferred rerun removed from `ops_brief.py` 2026-07-20; nothing triggers it; live timer/service were `/dev/null` masks | nothing (hourly `corporatetraveldc-ops-brief`) |
| 2026-10-20 | `.config/containers/systemd/retired-20261020/` | `corporatetraveldc-nms-v240-check.container` | its one-shot date is past | nothing (one-shot done) |
| 2026-10-20 | `.config/containers/systemd/retired-20261020/` | `amtrak-tracker.container.disabled` | stale 2026-07 draft; created no unit | `amtrak-tracker.container` (live) |

Live-only, removed by the same relay with no repo copy (never tracked):
`blog-substack-reminder.{service,timer,sh}` (expired 2026-09-02 one-shot),
`corporatetraveldc-ccw-demo-webdev-expiry.{service,timer}` (expired 2026-08-25 one-shot),
`~/.config/systemd/user/retired-20261006/corporatetraveldc-ollama-swap-alert.service`
(a live-only retired folder, never in the repo), and the two
`corporatetraveldc-ultrafeeder.container.bak-*` quadlet backups (2026-08-03).

Add a row whenever something is retired, in the same commit that moves it.
