#!/usr/bin/env bash
# scripts/install-root-copies.sh -- install the scripts that ROOT executes into
# a root-owned directory, so root never runs a file from the operator-writable
# checkout (2026-10-04 adversarial duel, safety finding C1).
#
# Order after every sign:  sign-manifest.sh -> commit -> sudo install-root-copies.sh
#                          -> sudo systemctl daemon-reload
#
# What it does, as root:
#   1. verifies the signed manifest of the checkout FIRST; refuses on any failure
#   2. copies the root-run closure into /usr/local/libexec/ctdc/ (root:root 0755,
#      files 0755/0644), atomically per file
#   3. records the installed sha256 of every file in .ctdc-installed, and checks
#      each against the manifest's entry for its source path
#   4. installs the system units from the tree with ExecStart pointing at the
#      installed copies (unit files root:root 0644 in /etc/systemd/system)
#
# Root-run closure (keep in sync when a root unit gains a dependency):
#   scripts/team-liveness.sh        <- corporatetraveldc-team-liveness.service (+ .path)
#     src/common/__init__.py, src/common/board_sign.py  (kill-order signature check)
#     (scripts/board-signer-ctl.sh is NOT copied: it runs as the operator via
#      runuser, i.e. with operator privilege, from the checkout)
#   scripts/watchdog.sh             <- corporatetraveldc-watchdog.service (User=root)
#   scripts/renew-tailscale-cert.sh <- corporatetraveldc-tailscale-cert-renew.service
#   scripts/skill-grants.sh + scripts/lib/skill_grants.py <- corporatetraveldc-skill-grants.service
#     (reads skills/ from the checkout only AFTER verify-manifest passes, and
#      checks every copied file against the manifest; it copies, never executes)
#
#   scripts/installed-check.sh + cf-honeypot-ban.sh + cf-honeypot-notes.sh +
#   lockdown.sh + restore-network.sh <- fail2ban action.d (root), which this
#     also installs with jail.d into /etc/fail2ban (2026-10-04: the actions ran
#     the checkout's verify-manifest.sh and scripts as root)
#
#   scripts/tailscale-cert-refresh-nts.sh <- corporatetraveldc-nts-cert-refresh.service (2026-10-05,
#     was an operator user unit whose deploy needed interactive sudo)
#
#   --check   report what would be installed and whether installed copies are current
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST=/usr/local/libexec/ctdc
CHECK=0; [[ "${1:-}" == --check ]] && CHECK=1

SCRIPTS=(scripts/team-liveness.sh scripts/watchdog.sh scripts/renew-tailscale-cert.sh scripts/stall-monitor.py scripts/watchdog-tune.sh scripts/skill-grants.sh scripts/lib/skill_grants.py
         scripts/tailscale-cert-refresh-nts.sh
         scripts/installed-check.sh scripts/cf-honeypot-ban.sh scripts/cf-honeypot-notes.sh scripts/lockdown.sh scripts/restore-network.sh
         scripts/approved-exec.py scripts/approved-exec.conf)   # 2026-10-07: root executor for signed sudo approvals
# fail2ban runs its actions as root: action.d/jail.d are installed verbatim
# (they already name the libexec copies) into /etc/fail2ban, root 0644.
F2B=(fail2ban/action.d/cloudflare-token-corporatetraveldc.conf fail2ban/action.d/corporatetraveldc-lockdown.conf
     fail2ban/jail.d/nginx-honeypot-corporatetraveldc.conf fail2ban/jail.d/nginx-limit-req-corporatetraveldc.conf
     fail2ban/filter.d/nginx-honeypot.conf)
PYLIB=(src/common/__init__.py src/common/board_sign.py)
UNITS=(
  "systemd/system/corporatetraveldc-team-liveness.service"
  "systemd/system/corporatetraveldc-team-liveness.timer"
  "systemd/system/corporatetraveldc-team-liveness-orders.path"
  "systemd/corporatetraveldc-watchdog.service"
  "systemd/corporatetraveldc-watchdog.timer"
  "systemd/corporatetraveldc-tailscale-cert-renew.service"
  "systemd/corporatetraveldc-tailscale-cert-renew.timer"
  "systemd/system/corporatetraveldc-stall-monitor.service"
  "systemd/system/corporatetraveldc-watchdog-tune.service"
  "systemd/system/corporatetraveldc-watchdog-tune.timer"
  "systemd/system/corporatetraveldc-skill-grants.service"
  "systemd/system/corporatetraveldc-skill-grants.timer"
  "systemd/system/corporatetraveldc-llama-council.service"
  "systemd/system/corporatetraveldc-llama-council.timer"
  "systemd/system/corporatetraveldc-nts-cert-refresh.service"
  "systemd/system/corporatetraveldc-nts-cert-refresh.timer"
)

manifest_hash() { awk -v p="$1" '$2==p || $2=="*"p {print $1; exit}' "${REPO}/MANIFEST.sha256"; }
dest_of() { case "$1" in scripts/*) echo "${DEST}/$(basename "$1")" ;; src/*) echo "${DEST}/lib/${1#src/}" ;; esac; }

if (( CHECK )); then
  for f in "${SCRIPTS[@]}" "${PYLIB[@]}"; do
    d=$(dest_of "$f"); want=$(manifest_hash "$f")
    have=$( [[ -f "$d" ]] && sha256sum "$d" | cut -d' ' -f1 || echo missing)
    printf '%-40s %s\n' "$f" "$( [[ "$have" == "$want" ]] && echo current || echo "STALE (installed ${have:0:12}, signed ${want:0:12})")"
  done
  for f in "${F2B[@]}"; do
    cmp -s "${REPO}/${f}" "/etc/${f}" && s=current || s="DIFFERS from /etc/${f}"
    printf '%-40s %s\n' "$(basename "$f")" "$s"
  done
  for u in "${UNITS[@]}"; do
    n=$(basename "$u"); grep -q "${DEST}/" "/etc/systemd/system/${n}" 2>/dev/null && s=installed-path || s="not using ${DEST}"
    [[ "$n" == *.service ]] && printf '%-40s %s\n' "$n" "$s"
  done
  exit 0
fi

(( EUID == 0 )) || { echo "install-root-copies: run with sudo" >&2; exit 77; }

# 1. the checkout must verify against the operator's signed manifest
if ! runuser -u "$(stat -c %U "${REPO}")" -- "${REPO}/scripts/verify-manifest.sh" >/dev/null 2>&1; then
  echo "install-root-copies: verify-manifest FAILED -- refusing to install anything" >&2; exit 3
fi

# 2+3. copy each file, then confirm its installed hash equals the signed one
install -d -m 0755 -o root -g root "${DEST}" "${DEST}/lib" "${DEST}/lib/common"
rec=$(mktemp); trap 'rm -f "$rec"' EXIT
for f in "${SCRIPTS[@]}" "${PYLIB[@]}"; do
  want=$(manifest_hash "$f"); [[ -n "$want" ]] || { echo "install-root-copies: ${f} is not in the manifest -- refusing" >&2; exit 4; }
  d=$(dest_of "$f"); mode=0644; [[ "$f" == scripts/*.sh || "$f" == scripts/*.py ]] && mode=0755
  install -m "$mode" -o root -g root "${REPO}/${f}" "${d}.new"
  have=$(sha256sum "${d}.new" | cut -d' ' -f1)
  [[ "$have" == "$want" ]] || { rm -f "${d}.new"; echo "install-root-copies: ${f} changed between verify and copy -- refusing" >&2; exit 5; }
  mv -f "${d}.new" "$d"; printf '%s  %s\n' "$have" "$f" >>"$rec"
done
install -m 0644 -o root -g root "$rec" "${DEST}/.ctdc-installed"

# 4. units: ExecStart rewritten from the checkout path to the installed copy
for u in "${UNITS[@]}"; do
  n=$(basename "$u"); tmp=$(mktemp)
  sed -E "s#/opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/(team-liveness|watchdog|renew-tailscale-cert|skill-grants|tailscale-cert-refresh-nts)\.sh#${DEST}/\1.sh#g" "${REPO}/${u}" >"$tmp"
  install -m 0644 -o root -g root "$tmp" "/etc/systemd/system/${n}"; rm -f "$tmp"
done
# 5. fail2ban config (each file hash-checked against the manifest like the scripts)
f2b_changed=0
for f in "${F2B[@]}"; do
  want=$(manifest_hash "$f"); [[ -n "$want" ]] || { echo "install-root-copies: ${f} is not in the manifest -- refusing" >&2; exit 4; }
  d="/etc/${f}"; install -m 0644 -o root -g root "${REPO}/${f}" "${d}.new"
  have=$(sha256sum "${d}.new" | cut -d' ' -f1)
  [[ "$have" == "$want" ]] || { rm -f "${d}.new"; echo "install-root-copies: ${f} changed between verify and copy -- refusing" >&2; exit 5; }
  cmp -s "${d}.new" "$d" || f2b_changed=1
  mv -f "${d}.new" "$d"; restorecon "$d" 2>/dev/null || true
done
if (( f2b_changed )); then
  if fail2ban-client -t >/dev/null 2>&1; then systemctl reload-or-restart fail2ban && echo "fail2ban config updated + reloaded"
  else echo "install-root-copies: fail2ban-client -t FAILED on the new config -- fail2ban NOT reloaded; fix before the next restart" >&2; fi
fi
echo "installed $(wc -l <"${DEST}/.ctdc-installed") file(s) to ${DEST}; units updated -- now: sudo systemctl daemon-reload"
