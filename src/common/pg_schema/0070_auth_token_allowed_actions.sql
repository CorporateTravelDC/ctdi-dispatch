-- 0070_auth_token_allowed_actions.sql -- per-token action scopes (2026-10-05).
--
-- An admin-tier API token used to mean "every admin route". Cowork held one
-- (remote-admin/cowork, 2026-09-20) although its audit trail shows six
-- actions (alert push, watchlist adds, healthz, trigger list, one vault
-- remember). allowed_actions narrows a token to an explicit list of the
-- action names require_admin() already audits (comma-separated, fnmatch
-- patterns allowed, e.g. "watchlist.*"). NULL = unrestricted (every existing
-- token, unchanged). Logic: src/auth/auth.py require_admin.
ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS allowed_actions TEXT;
