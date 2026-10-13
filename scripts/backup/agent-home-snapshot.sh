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
# Credentials are EXCLUDED: SSH private keys, CLI auth/token/session-key files,
# keyrings, browser profiles. A restored agent re-enrolls; it is never
# resurrected with live credentials.
#
# 2026-10-12 (docs-parity audit, verified on the box): the 2026-10-11/12
# snapshots captured a Claude Code session key (.claude/sessions/*.key) and a
# Brave browser profile (Local State holds the cookie encryption key) -- the
# name globs missed both. Fixes: wider excludes, AND a tripwire that lists every
# produced archive and DELETES it (and fails the unit) if any credential-shaped
# name got in, so a new credential file can never be backed up silently.
set -euo pipefail
# 2026-10-11 (operator): EVERY ctdc-agent-* account by default; CTDC_SNAPSHOT_EXCLUDE removes names
# (settable via /etc/corporatetraveldc/agent-snapshot.env, the unit's EnvironmentFile).
# CTDC_SNAPSHOT_HOMES="name:/path ..." / CTDC_SNAPSHOT_OUT / CTDC_SNAPSHOT_OWNER: tests only.
if [[ -n "${CTDC_SNAPSHOT_HOMES:-}" ]]; then
  AGENTS=(); declare -A HOME_OF=()
  for pair in $CTDC_SNAPSHOT_HOMES; do AGENTS+=("${pair%%:*}"); HOME_OF["${pair%%:*}"]="${pair#*:}"; done
else
  AGENTS=($(getent passwd | cut -d: -f1 | grep -E "^ctdc-agent-" | grep -vxF -f <(tr " " "\n" <<<"${CTDC_SNAPSHOT_EXCLUDE:-}" | sed "/^$/d") || true))
  declare -A HOME_OF=()
  for a in "${AGENTS[@]}"; do HOME_OF["$a"]="$(getent passwd "$a" | cut -d: -f6)"; done
fi
OUT="${CTDC_SNAPSHOT_OUT:-/var/lib/corporatetraveldc/agent-snapshots}"; KEEP=7; OWNER="${CTDC_SNAPSHOT_OWNER:-corporatetraveldc}"
EXCLUDES=(
  --exclude='.ssh/id_*' --exclude='.ssh/*_ed25519' --exclude='.ssh/*_rsa' --exclude='*.key' --exclude='*.pem'
  --exclude='*.p12' --exclude='*.pfx' --exclude='*auth*.json' --exclude='*token*' --exclude='*credential*'
  --exclude='.netrc' --exclude='.gnupg/private-keys-v1.d' --exclude='.local/share/keyrings'
  --exclude='.claude/sessions' --exclude='.config/BraveSoftware' --exclude='.config/google-chrome'
  --exclude='.config/chromium' --exclude='.mozilla' --exclude='Cookies' --exclude='Login Data*' --exclude='Web Data*'
  --exclude='Local State' --exclude='.cache' --exclude='node_modules' --exclude='.npm'
)
# Tripwire: any of these names inside an archive means an exclude was missed.
FORBIDDEN='(\.key|\.pem|\.p12|\.pfx)$|(^|/)\.ssh/(id_[^/.]+|[^/]*_(ed25519|rsa))$|auth[^/]*\.json$|(^|/)\.netrc$|private-keys-v1\.d/|/keyrings/|(^|/)\.claude/sessions/|BraveSoftware/|google-chrome/|chromium/|(^|/)\.mozilla/|(^|/)(Cookies|Login Data|Web Data|Local State)$'
ts=$(date -u +%Y%m%dT%H%MZ)
install -d -m 0700 -o "$OWNER" -g "$OWNER" "$OUT"
rc=0
for a in "${AGENTS[@]}"; do
  home="${HOME_OF[$a]:-}"; [[ -n "$home" && -d "$home" ]] || { echo "skip $a: no home"; continue; }
  d="$OUT/$a"; install -d -m 0700 -o "$OWNER" -g "$OWNER" "$d"
  f="$d/$a-$ts.tar.zst"
  # 2026-10-12: a live agent session changes files while they are read; tar exits 1 for that ("file changed
  # as we read it" -- a warning, the archive is still written). Only tar >= 2 is fatal, and one agent's
  # failure no longer stops the others (the 10-13 03:20Z run died on the Codex home and skipped the rest).
  set +e
  tar -C "$home" --numeric-owner --warning=no-file-changed --warning=no-file-ignored -cf - "${EXCLUDES[@]}" . | zstd -q -6 -o "$f"
  st=("${PIPESTATUS[@]}"); set -e
  if (( st[0] > 1 || st[1] != 0 )); then
    rm -f "$f"; echo "FAILED $a: tar rc=${st[0]} zstd rc=${st[1]}; no snapshot kept" >&2; rc=1; continue
  fi
  hits=$(zstd -dc "$f" | tar -tf - | grep -E "$FORBIDDEN" | wc -l || true)
  if (( hits > 0 )); then
    rm -f "$f"; echo "REFUSED $a: $hits credential-shaped name(s) got past the excludes; snapshot deleted, nothing kept" >&2
    rc=1; continue
  fi
  sha256sum "$f" | awk '{print $1}' > "$f.sha256"
  chown "$OWNER:$OWNER" "$f" "$f.sha256"; chmod 0600 "$f" "$f.sha256"
  ls -1t "$d"/"$a"-*.tar.zst | tail -n +$((KEEP + 1)) | while read -r old; do rm -f "$old" "$old.sha256"; done
  echo "$a: $(du -h "$f" | cut -f1) $(cut -c1-16 "$f.sha256") (tripwire clean)"
done
exit $rc
