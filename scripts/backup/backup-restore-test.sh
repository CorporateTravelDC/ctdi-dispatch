#!/usr/bin/env bash
# scripts/backup/backup-restore-test.sh -- prove the newest Borg archive restores (2026-10-11).
# Extracts the archive's database dumps to a throwaway directory, checks each
# dump's SHA-256 against the ledger's backup entry, validates each dump's
# structure with pg_restore --list (inside the Postgres container, read-only),
# checks the newest local-only LADD dump against its "#" stamp, then records a
# restore-test entry in the private ledger. Nothing is restored INTO any database.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LEDGER_PY="$REPO_DIR/scripts/backup/backup_ledger.py"
ENV_FILE=/etc/corporatetraveldc/dispatch.env
read_env() { [[ -f "$ENV_FILE" ]] && grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- || true; }
STATE="$HOME/.local/state/ctdc-backup"
REPO="${BACKUP_REPO:-$(read_env BACKUP_REPO)}"; REPO="${REPO:-$STATE/borg}"
# 2026-10-12: one heavy job at a time (see scripts/lib/heavy_lock.sh).
source "$(dirname "${BASH_SOURCE[0]}")/../lib/heavy_lock.sh"
heavy_lock_acquire "restore test" 7200 || { echo "restore test: heavy-work turn still taken after 2 h" >&2; exit 1; }
PASSFILE="${BACKUP_PASSPHRASE_FILE:-$(read_env BACKUP_PASSPHRASE_FILE)}"; PASSFILE="${PASSFILE:-/etc/corporatetraveldc/backup/borg-passphrase}"
export BORG_PASSCOMMAND="cat $PASSFILE" BORG_REPO="$REPO"
log() { printf '[%s] [restore-test] %s\n' "$(date -u +%FT%TZ)" "$*"; }
fail() { log "FAIL: $*"; python3 "$LEDGER_PY" append restore-test --json "{\"ok\": false, \"reason\": \"$(echo "$*" | tr -d '"\\' | head -c 200)\"}" >/dev/null 2>&1 || true; exit 1; }
archive=$(podman unshare env BORG_PASSCOMMAND="$BORG_PASSCOMMAND" BORG_REPO="$REPO" borg list --short --last 1 --glob-archives 'ctdi-*') || fail "cannot list archives"
[[ -n "$archive" ]] || fail "no archives yet"
t=$(mktemp -d "$STATE/restore.XXXX"); chmod 700 "$t"; trap 'rm -rf "$t"' EXIT   # 2026-10-11: rm, not shred (btrfs is copy-on-write; shred only cost time)
log "extracting dumps from ::$archive"
( cd "$t" && podman unshare env BORG_PASSCOMMAND="$BORG_PASSCOMMAND" BORG_REPO="$REPO" borg extract "::$archive" --pattern '+ sh:**/*.pgdump' --pattern '+ sh:**/ledger.jsonl' --pattern '- *' ) || fail "extract failed"
want=$(python3 "$LEDGER_PY" last backup)
python3 - "$t" "$want" <<'PY' || exit 1
import hashlib, json, sys
from pathlib import Path
root, want = Path(sys.argv[1]), json.loads(sys.argv[2] or "{}")
exp = {Path(d["path"]).name: d["sha256"] for d in (want.get("data") or {}).get("dumps", [])}
got = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.pgdump")}
if not exp: sys.exit("ledger has no backup entry with dumps")
bad = [n for n in exp if got.get(n) != exp[n]]
if bad: sys.exit(f"dump hash mismatch vs ledger: {bad}")
print("dump hashes match the ledger:", ", ".join(sorted(exp)))
PY
for f in $(find "$t" -name '*.pgdump'); do
  n=$(podman exec -i corporatetraveldc-pgsql pg_restore --list < "$f" | grep -c ';' ) || fail "pg_restore --list failed on $(basename "$f")"
  log "$(basename "$f"): pg_restore --list OK ($n TOC entries)"
done
stamp=$(python3 "$LEDGER_PY" last cui-stamp)
python3 - "$stamp" <<'PY' || fail "LADD local dump does not match its # stamp"
import hashlib, json, sys
from pathlib import Path
s = json.loads(sys.argv[1] or "{}").get("data") or {}
for d in s.get("local_dump") or []:
    p = Path(d["path"])
    if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() != d["sha256"]:
        sys.exit(1)
print("LADD local dump matches its # stamp (or has been rotated out)")
PY
python3 "$LEDGER_PY" append restore-test --json "{\"ok\": true, \"archive\": \"$archive\"}" >/dev/null
python3 "$LEDGER_PY" verify
log "restore test OK for ::$archive"
