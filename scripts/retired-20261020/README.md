# Retired 2026-10-20 -- platform SQLite tooling

Operator decision 2026-10-19: the only things on the box allowed to touch SQLite are Pi-hole, vnstat and ACARS Hub.
The 2026-10-19 relay (d5fde567) destroyed every platform SQLite file Postgres replaced (G26 proof record on-box),
and the demo's sovereign SQLite file and `demo_access.db` were already empty. Every script here reads or checks a
file that no longer exists, so each one either does nothing or fails.

| File | What it was | Why retired |
|---|---|---|
| `migrate-sqlite-to-pg.py` | One-shot bulk copy of the platform tables from `corporatetraveldc.db` into Postgres (2026-09-06 to 09-19 cutover), with row-hash parity checks | The cutover finished 2026-09-19 and the source file was destroyed 2026-10-19 |
| `migrate-second-brain-to-pg.py` | One-shot copy of the second-brain vault index from `second_brain_index.db` into Postgres | Finished 2026-09-19; the source file is destroyed |
| `migrate-demo-to-pg.py` | One-shot copy of `demo.db` (`demo_snapshots`) and `demo_access.db` (`demo_profiles`) into Postgres | Finished 2026-09-20; both source files are gone |
| `demo-sqlite-retire-check.py` | Relay-4 preparation for the demo/production split: proved N hours on Postgres, listed the sovereign files, hashed them (G26) and printed the shred command for the operator | Its file list is already gone from disk; the demo runs on Postgres only, and the sqlite rollback it guarded was removed the same day |
| `retired_test_demo_sqlite_retire_check.py` | That script's test (was `tests/scripts/test_demo_sqlite_retire_check.py`) | Retired with the script. Renamed so pytest does not collect it from here |

Replaced by: nothing. Production and the demo run on Postgres only, and the code refuses SQLite outside the test suite.
`common.db_backend.backend()` raises `SqliteRetired` on `DISPATCH_DB_BACKEND=sqlite` unless
`CTDC_ALLOW_SQLITE_FOR_TESTS=1` is set, and only `tests/conftest.py` sets it. `tests/common/test_no_platform_sqlite.py`
holds this. See `docs/POSTGRES_MIGRATION.md` §3.7 for the remaining code work (the SQLite twin migrators and test
fixtures), which is planned for a post-freeze batch.

Kept here for the record. Nothing in this folder is installed or run.
