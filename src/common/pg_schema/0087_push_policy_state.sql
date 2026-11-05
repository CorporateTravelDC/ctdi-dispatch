-- 0087_push_policy_state.sql -- push parity lock (2026-11-04, operator directive:
-- every push in every vertical is decided by one gate at aviation parity).
-- common/push_policy.py _ensure() is the SQLite twin (test suite only).
-- Tables: push_policy_state
--
-- push_policy_state: the last FIRED semantic value per (vertical, kind, entity, slot),
-- shared by every container so a re-send, a wording flip or a second source of the
-- same event is decided once. state_values is JSON text (same convention as
-- watchlist_history.event_detail).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS push_policy_state (
    vertical      TEXT NOT NULL,
    kind          TEXT NOT NULL,
    entity        TEXT NOT NULL,
    sub_id        TEXT NOT NULL,
    semantic_hash TEXT NOT NULL,
    state_values  TEXT,
    priority      INTEGER,
    decided_at    DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (vertical, kind, entity, sub_id)
);
