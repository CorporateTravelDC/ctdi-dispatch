#!/usr/bin/env bash
# scripts/ladd-import.sh -- the LADD filter import with crypto-erase intake (2026-11-04, operator).
# docs/LADD_CUI_HANDLING.md. Run as the operator; the import itself runs in the poller container.
#
#   scripts/ladd-import.sh [--supersede SEQ,SEQ] [--reason TEXT]
#
# Intake: put the FAA files in $LADD_INTAKE (default /run/user/$UID/ladd-intake, tmpfs = RAM), e.g.
#   scp LADD_Industry_*.txt LADD_remove_*.txt corporatetraveldc@<box>:/run/user/1000/ladd-intake/
# Industry/Source filters: names containing "Industry" or "Source"; remove lists: names containing "remove".
#
# What it does, counts and hashes only (never a value from the files):
#  1. Encrypts every intake file at once with a fresh per-run AES-256 key held only in this process
#     (never written anywhere); the plaintext leaves RAM. From here on only ciphertext exists outside
#     the import process.
#  2. Streams the decrypted files into the poller container's /dev/shm (tmpfs; the container's /tmp is
#     disk-backed overlay storage) and imports them (stamp-only comparison first, then the full replace).
#  3. EXHAUSTIVE check: the table must equal (every identifier in the filters) minus (every recorded or
#     supplied removal) -- set equality over every entry -- and every entry is also queried through the
#     real lookup path, db.faa_is_ladd(); every removal must report not LADD. Fail = stop; the intake is
#     still crypto-erased (the key dies with the run), so re-supply the files from the original copy.
#  4. Crypto-erase (NIST SP 800-88 Rev.1 cryptographic erase): the key is destroyed, the ciphertext and
#     the container copies removed, the intake folder checked empty. Ledger entry `ladd-source-deleted`.
#  5. Optional supersede: a `ladd-supersede` entry names the earlier seqs this run replaces and why
#     (the earlier entries stay; nothing is ever deleted from the ledger).
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTR="${LADD_CONTAINER:-systemd-corporatetraveldc-poller}"
INTAKE="${LADD_INTAKE:-/run/user/$(id -u)/ladd-intake}"
SUPERSEDE=""; REASON=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --supersede) SUPERSEDE="${2:?}"; shift 2 ;;
    --reason) REASON="${2:?}"; shift 2 ;;
    *) echo "usage: $0 [--supersede SEQ,SEQ] [--reason TEXT]" >&2; exit 64 ;;
  esac
done
[[ -n "$SUPERSEDE" && -z "$REASON" ]] && { echo "--supersede needs --reason" >&2; exit 64; }
[[ "$(findmnt -n -o FSTYPE --target "$INTAKE" 2>/dev/null)" == tmpfs ]] || { echo "intake $INTAKE is not on tmpfs" >&2; exit 1; }
shopt -s nullglob
FILES=("$INTAKE"/*.txt)
(( ${#FILES[@]} )) || { echo "no .txt files in $INTAKE" >&2; exit 1; }

echo "== 1. Encrypt the intake with a per-run key (held in this process only)"
KEY="$(openssl rand -hex 32)"; export LADD_RUN_KEY="$KEY"
ENC="$INTAKE/.enc"; install -d -m 0700 "$ENC"
declare -a ROLE NAME
i=0
for f in "${FILES[@]}"; do
  b="$(basename "$f")"
  case "$b" in *[Rr]emove*) r=remove ;; *Industry*|*Source*) r=filter ;; *) echo "cannot tell the role of $b" >&2; exit 1 ;; esac
  openssl enc -aes-256-ctr -pbkdf2 -iter 200000 -salt -pass env:LADD_RUN_KEY -in "$f" -out "$ENC/$i.bin"
  rm -f "$f"
  ROLE[$i]="$r"; NAME[$i]="$b"
  printf '  %s  role=%s  (%s bytes ciphertext)\n' "$b" "$r" "$(stat -c %s "$ENC/$i.bin")"
  i=$((i + 1))
done
N=$i

T="/dev/shm/ladd-$(date -u +%Y%m%dT%H%M%SZ)"
cleanup() {
  podman exec "$CTR" rm -rf "$T" </dev/null >/dev/null 2>&1 || true
  unset LADD_RUN_KEY KEY
  rm -rf "$ENC"
}
trap cleanup EXIT
podman exec "$CTR" sh -c "umask 077; mkdir -p '$T'" </dev/null
podman cp "$REPO_DIR/scripts/import-ladd-filter.py" "$CTR:$T/import-ladd-filter.py" </dev/null
ARGS=()
for ((k = 0; k < N; k++)); do
  dst="$T/${ROLE[$k]}-$k.txt"
  openssl enc -d -aes-256-ctr -pbkdf2 -iter 200000 -pass env:LADD_RUN_KEY -in "$ENC/$k.bin" \
    | podman exec -i "$CTR" sh -c "umask 077; cat > '$dst'"
  if [[ "${ROLE[$k]}" == remove ]]; then ARGS+=(--remove "$dst"); else ARGS+=("$dst"); fi
done

echo "== 2. Stamp-only comparison, then the import (full replace; removals re-applied)"
"$REPO_DIR/scripts/relay-wait-load.sh"
podman exec -w /app -e PYTHONPATH=/app/src "$CTR" python3 "$T/import-ladd-filter.py" --stamp-only "${ARGS[@]}" </dev/null
podman exec -w /app -e PYTHONPATH=/app/src "$CTR" python3 "$T/import-ladd-filter.py" "${ARGS[@]}" </dev/null

echo "== 3. Exhaustive check: every entry, set equality plus the real lookup path (counts only)"
podman exec -i -w /app -e PYTHONPATH=/app/src -e LADD_T="$T" "$CTR" python3 - <<'PY'
import os, sys, time
from pathlib import Path
from common import db
t = Path(os.environ["LADD_T"])
def idents(p):   # exactly import-ladd-filter.py's _load_idents
    return {l.strip().upper() for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()}
listed = set().union(*[idents(p) for p in sorted(t.glob("filter-*.txt"))])
supplied_rm = set().union(set(), *[idents(p) for p in sorted(t.glob("remove-*.txt"))])
with db.conn() as c:
    recorded_rm = {r["n_number"] for r in c.execute("SELECT n_number FROM faa_ladd_removals").fetchall()}
    table = {r["n_number"] for r in c.execute("SELECT n_number FROM faa_ladd_aircraft").fetchall()}
removed = recorded_rm | supplied_rm
# a removal covers every stored form of an identifier (N01AB / 01AB), exactly as the import deletes them
removed_forms = {k for r in removed for k in db.ladd_lookup_keys(r)}
expected = {x for x in listed if not any(k in removed_forms for k in db.ladd_lookup_keys(x))}
missing, extra = len(expected - table), len(table - expected)
print(f"set equality: expected {len(expected)}, table {len(table)}, missing {missing}, extra {extra}")
t0 = time.time(); bad = 0
for i, v in enumerate(sorted(expected), 1):
    if not db.faa_is_ladd(v):
        bad += 1
    if i % 10000 == 0:
        print(f"  lookup path: {i}/{len(expected)} checked, {bad} not reporting LADD")
rm_bad = sum(1 for v in removed if db.faa_is_ladd(v))
print(f"lookup path: {len(expected) - bad}/{len(expected)} report LADD; removals: {len(removed) - rm_bad}/{len(removed)} report NOT LADD ({time.time() - t0:.0f} s)")
ok = missing == 0 and extra == 0 and bad == 0 and rm_bad == 0
print("EXHAUSTIVE CHECK PASSED" if ok else "EXHAUSTIVE CHECK FAILED -- the run stops; the key dies with it (the intake is crypto-erased either way): re-supply the files from the original copy")
sys.exit(0 if ok else 1)
PY

echo "== 4. Crypto-erase: destroy the key, remove the ciphertext and the container copies"
podman exec "$CTR" rm -rf "$T" </dev/null
unset LADD_RUN_KEY KEY
rm -rf "$ENC"
podman exec "$CTR" test ! -e "$T" </dev/null
leftover=("$INTAKE"/*); (( ${#leftover[@]} == 0 )) || { echo "intake not empty after the erase" >&2; exit 1; }
LEDGER_NOTE="${SUPERSEDE:+supersedes:$SUPERSEDE}" LEDGER_REASON="$REASON" \
podman exec -i -w /app -e PYTHONPATH=/app/src -e LEDGER_NOTE -e LEDGER_REASON "$CTR" python3 - <<'PY'
import json, os
from common import import_ledger
imp = [e for e in import_ledger.entries() if e.get("kind") == "ladd-import"][-1]
body = json.loads(imp["body"]) if isinstance(imp["body"], str) else imp["body"]
sources = [{k: s[k] for k in ("name", "bytes", "sha256", "role") if k in s} for s in body.get("sources", [])]
d = import_ledger.stamp("ladd-source-deleted", sources, {
    "files_deleted": len(sources), "absent_after_delete": True, "import_seq": imp["seq"],
    "method": "crypto-erase (NIST SP 800-88 Rev.1): per-run AES-256 key destroyed; plaintext only in tmpfs; ciphertext and container /dev/shm copies removed"})
print(f"ledger: ladd-source-deleted seq {d['seq']} for import seq {imp['seq']}, {len(sources)} file(s) by sha256")
sup = os.environ.get("LEDGER_NOTE", "")
if sup.startswith("supersedes:"):
    seqs = [int(x) for x in sup.split(":", 1)[1].split(",") if x.strip()]
    s = import_ledger.stamp("ladd-supersede", [], {"superseded_seqs": seqs, "superseded_by": imp["seq"],
                                                    "deletion_seq": d["seq"]}, note=os.environ.get("LEDGER_REASON") or None)
    print(f"ledger: ladd-supersede seq {s['seq']}: seqs {seqs} superseded by import seq {imp['seq']} (they stay in the chain)")
ok, _ = import_ledger.verify()
print("import ledger chain:", "intact" if ok else "BROKEN")
PY
echo "ladd-import.sh complete"
