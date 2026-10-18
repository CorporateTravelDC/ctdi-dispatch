-- 0077_semantic_compile_runs.sql -- semantic compile run ledger (2026-10-18, relay L1).
--
-- The semantic compile (second_brain.semantic.compile.compile_layer) failed
-- ~17% of runs (lock / statement timeouts) while its unit reported success,
-- and nothing recorded which compiles actually completed. One row per run:
--
--   seq          row id (bigserial). Failed runs take a value too, and a
--                rolled-back nextval leaves a gap, so seq is NOT the count of
--                completed compiles.
--   compile_seq  the completed-compile counter: set only on ok rows, exactly
--                previous max + 1, inside the compile's own transaction (so a
--                compile that rolls back never takes a number). NULL on
--                failures. The `semantic-compiled` sentinel carries it; a
--                consumer that acts "every Nth compile" keys on this.
--   ok=false     rows are written by a separate connection after the
--                compile's transaction rolled back (best effort).
--
-- Not in compile.py's _TABLES: the compile TRUNCATEs those, never this one.
-- Contract: docs/SECOND_BRAIN_STATUS.md "Compile run ledger and sentinel".

CREATE TABLE IF NOT EXISTS semantic_compile_runs (
    seq          BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ NOT NULL,
    ok           BOOLEAN NOT NULL,
    error        TEXT,
    derivations  INTEGER,
    concepts     INTEGER,
    duration_s   REAL,
    compile_seq  BIGINT UNIQUE,
    CHECK ((ok AND compile_seq IS NOT NULL AND error IS NULL)
           OR (NOT ok AND compile_seq IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_semantic_compile_runs_finished
    ON semantic_compile_runs(finished_at);
