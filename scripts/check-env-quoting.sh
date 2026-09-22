#!/usr/bin/env bash
# scripts/check-env-quoting.sh -- permanent guard against quoted values in
# any dispatch env/secrets file. See dispatch-secrets.env.template's
# file-header banner (2026-09-05) for why quoting is ALWAYS wrong here:
# every real consumer (Podman Quadlet's EnvironmentFile= -> `podman run
# --env-file`, and every scripts/*.sh reader via the read_env_var()
# grep+cut pattern) takes the value 100% literally after the first "=",
# with no quote-stripping and no shell word-splitting -- a value with a
# comma or a space is preserved correctly unquoted, and a QUOTED value
# gets the literal quote characters baked in as corruption. Confirmed
# live 2026-09-05: this broke NWWS-OI auth (~1hr silent outage, masked by
# a separate heartbeat bug) and Amtrak core-route matching, both from a
# single stray pair of quote characters added under the mistaken belief
# that quoting was ever needed or safe here.
#
# Checks BOTH the tracked template/example files in this repo AND (when
# run on the live box) the real deployed copies in /etc/corporatetraveldc/
# -- tracked-file discipline alone doesn't stop someone hand-editing a
# live file later, which is exactly how this broke the first time.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

FAIL=0

check_file() {
    local f="$1"
    [[ -f "$f" ]] || return 0
    local hits
    hits="$(grep -nE "^[A-Za-z_][A-Za-z0-9_]*='.*'\$|^[A-Za-z_][A-Za-z0-9_]*=\".*\"\$" "$f" 2>/dev/null)"
    if [[ -n "$hits" ]]; then
        echo "[check-env-quoting] FAIL -- quoted value(s) in ${f}:"
        echo "${hits}" | sed 's/^/    /'
        FAIL=1
    fi
}

# Tracked files (repo)
check_file "dispatch-secrets.env.template"
check_file "config/dispatch.env"
for f in config/*.env.example; do
    check_file "${f}"
done

# Live deployed copies (only meaningful when running on the actual box)
if [[ -d /etc/corporatetraveldc ]]; then
    for f in /etc/corporatetraveldc/*.env; do
        check_file "${f}"
    done
fi

if [[ "${FAIL}" -eq 1 ]]; then
    echo ""
    echo "[check-env-quoting] Quoting a value in these files silently corrupts it for"
    echo "                     every real consumer -- podman --env-file never strips"
    echo "                     quotes. Remove the quotes; do not add escaping instead."
    echo "                     See dispatch-secrets.env.template's file-header banner."
    exit 1
fi

echo "[check-env-quoting] OK -- no quoted values found."
