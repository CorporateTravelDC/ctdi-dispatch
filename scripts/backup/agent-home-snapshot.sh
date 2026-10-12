#!/usr/bin/env bash
# scripts/backup/agent-home-snapshot.sh -- ROOT-run (installed copy in /usr/local/libexec/ctdc):
# snapshot agent accounts' homes into a directory the nightly backup already covers (2026-10-11).
#
# Agent homes are 0700 to their own account (segmentation), so the operator-run
# Borg backup cannot read them. This step runs as root at 03:20 UTC, before the
# 03:40 backup, and writes one compressed tarball per agent to
#   /var/lib/corporatetraveldc/agent-snapshots/<account>/<account>-<UTC stamp>.tar.zst
# owned by corporatetraveldc, mode 0600, with a .sha256 beside it. Keeps 7.
#
# Credentials are EXCLUDED: SSH private keys, CLI auth/token files, keyrings.
# A restored agent re-enrolls; it is never resurrected with live credentials.
set -euo pipefail
# 2026-10-11 (operator): EVERY ctdc-agent-* account by default; CTDC_SNAPSHOT_EXCLUDE removes names.
AGENTS=($(getent passwd | cut -d: -f1 | grep -E "^ctdc-agent-" | grep -vxF -f <(tr " " "\n" <<<"${CTDC_SNAPSHOT_EXCLUDE:-}" | sed "/^$/d") || true))
OUT=/var/lib/corporatetraveldc/agent-snapshots; KEEP=7; OWNER=corporatetraveldc
ts=$(date -u +%Y%m%dT%H%MZ)
install -d -m 0700 -o "$OWNER" -g "$OWNER" "$OUT"
for a in "${AGENTS[@]}"; do
  home=$(getent passwd "$a" | cut -d: -f6); [[ -n "$home" && -d "$home" ]] || { echo "skip $a: no home"; continue; }
  d="$OUT/$a"; install -d -m 0700 -o "$OWNER" -g "$OWNER" "$d"
  f="$d/$a-$ts.tar.zst"
  tar -C "$home" --numeric-owner -cf - \
      --exclude='.ssh/id_*' --exclude='.ssh/*_ed25519' --exclude='.ssh/*_rsa' --exclude='.ssh/*.key' \
      --exclude='*auth*.json' --exclude='*token*' --exclude='*credential*' --exclude='.netrc' \
      --exclude='.gnupg/private-keys-v1.d' --exclude='.local/share/keyrings' \
      --exclude='.cache' --exclude='node_modules' --exclude='.npm' \
      . | zstd -q -6 -o "$f"
  sha256sum "$f" | awk '{print $1}' > "$f.sha256"
  chown "$OWNER:$OWNER" "$f" "$f.sha256"; chmod 0600 "$f" "$f.sha256"
  ls -1t "$d"/"$a"-*.tar.zst | tail -n +$((KEEP + 1)) | while read -r old; do rm -f "$old" "$old.sha256"; done
  echo "$a: $(du -h "$f" | cut -f1) $(cut -c1-16 "$f.sha256")"
done
