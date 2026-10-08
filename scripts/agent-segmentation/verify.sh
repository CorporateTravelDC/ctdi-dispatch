#!/usr/bin/env bash
# scripts/agent-segmentation/verify.sh -- post-migration checks for BOTH team
# groups, run AS THE OPERATOR (it uses `sudo -u <account>` for the per-account
# probes, so it prompts for the operator's sudo password once). Prints
# PASS/FAIL per check, never a secret value. Exit 1 if any check fails.
#
#   scripts/agent-segmentation/verify.sh                 # every ctdc-ops + ctdc-agents account
#   scripts/agent-segmentation/verify.sh --user alice    # one account
#
# Per account (docs/AGENT_SEGMENTATION.md goals):
#   1. cannot read /etc/corporatetraveldc/dispatch-secrets.env or traverse the dir
#   2. CAN read its own group's subset (root:<group> 0640, allowlisted names
#      only) and CANNOT read the other group's subset
#   3. has no sudo; is in exactly one of ctdc-ops / ctdc-agents, plus ctdc-dev
#   4. its slice (humans.slice / agents.slice) is loaded in its user manager
#   5. attribution: authorized_keys holds exactly one key whose comment starts
#      with the account name; (agents) git user.name is the account name
#   6. no GPG secret keys; cannot read the operator's .gnupg; can READ the repo
#      through the shared group and can NOT write it or .git/hooks; not in
#      systemd-journal (2026-10-04 duel containment)
# Fleet-wide (needs sudo to read /home/*/.ssh/authorized_keys; skipped with a
# note if that is refused):
#   7. no two accounts share a public key; the operator's authorized_keys no
#      longer carries the claude-cowork-dispatch comment
#   8. production secrets file still 0600 operator-owned; dir still 0750
# Account KINDS (2026-10-04, /etc/ctdc-accounts.conf "name kind
# login_mode preloaded"): service -> nologin shell, no authorized_keys, no
# slice/unit checks; agent login-mode ssh -> one inbound key, no unit check;
# preloaded -> expired (chage), no linger, signer registered but INACTIVE.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
OPERATOR_USER="corporatetraveldc"; DEV_GROUP="ctdc-dev"; OPS_GROUP="ctdc-ops"; AGENTS_GROUP="ctdc-agents"
PROD_SECRETS="/etc/corporatetraveldc/dispatch-secrets.env"
AGENT_SECRETS="/etc/ctdc-agent/agent-secrets.env"; OPS_SECRETS="/etc/ctdc-ops/ops-secrets.env"
RC_UNIT="corporatetraveldc-claude-remote-control.service"
REGISTRY="/etc/ctdc-accounts.conf"
ALLOW_AGENT="${SELF_DIR}/secrets-allowlist-agent.txt"; ALLOW_OPS="${SELF_DIR}/secrets-allowlist-ops.txt"

ONLY_USER=""; prev=""
for a in "$@"; do case "$a" in --user=*) ONLY_USER="${a#--user=}" ;; --user) ;; -h|--help) sed -n 2,24p "$0"; exit 0 ;; *) [[ "$prev" == --user ]] && ONLY_USER="$a" || { echo "unknown arg $a" >&2; exit 2; } ;; esac; prev="$a"; done

fails=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; fails=$((fails+1)); }
skip() { printf 'SKIP  %s\n' "$1"; }
as_user() { sudo -u "$1" -- "${@:2}"; }
members() { getent group "$1" | cut -d: -f4 | tr ',' '\n' | grep -v '^$'; }

[[ "$(id -un)" == "${OPERATOR_USER}" ]] || { echo "run as ${OPERATOR_USER}" >&2; exit 2; }
getent group "${OPS_GROUP}" >/dev/null || getent group "${AGENTS_GROUP}" >/dev/null || { echo "no team groups exist -- migration not run"; exit 2; }

if [[ -n "$ONLY_USER" ]]; then ACCOUNTS=("$ONLY_USER"); else mapfile -t ACCOUNTS < <( { members "${OPS_GROUP}"; members "${AGENTS_GROUP}"; } | sort -u ); fi
(( ${#ACCOUNTS[@]} )) || { echo "no team accounts yet"; exit 2; }

check_account() {
    local U="$1" kind own_subset other_subset own_group other_group slice allow
    id "$U" >/dev/null 2>&1 || { fail "${U}: account does not exist"; return; }
    if id -nG "$U" | tr ' ' '\n' | grep -qx "${AGENTS_GROUP}"; then
        kind=agent; own_group="${AGENTS_GROUP}"; other_group="${OPS_GROUP}"; own_subset="${AGENT_SECRETS}"; other_subset="${OPS_SECRETS}"; slice=agents.slice; allow="${ALLOW_AGENT}"
    elif id -nG "$U" | tr ' ' '\n' | grep -qx "${OPS_GROUP}"; then
        kind=human; own_group="${OPS_GROUP}"; other_group="${AGENTS_GROUP}"; own_subset="${OPS_SECRETS}"; other_subset="${AGENT_SECRETS}"; slice=humans.slice; allow="${ALLOW_OPS}"
    else
        fail "${U}: in neither ${OPS_GROUP} nor ${AGENTS_GROUP}"; return
    fi
    # registry refinement (service / ssh-mode / preloaded)
    local rkind="" rmode="" rpre=0
    if [[ -r "$REGISTRY" ]]; then read -r rkind rmode rpre < <(awk -v n="$U" '$1==n {print $2, $3, $4}' "$REGISTRY" | tail -1) || true; fi
    [[ -z "$rkind" ]] && { rkind="$kind"; rmode=$([[ "$kind" == agent ]] && echo claude || echo ssh); rpre=0; }
    echo "--- ${U} (${rkind}, login-mode ${rmode}$([[ "$rpre" == 1 ]] && echo ', PRELOADED'))"
    # 1
    as_user "$U" test -r "${PROD_SECRETS}" 2>/dev/null && fail "1 ${U} can read ${PROD_SECRETS}" || pass "1 ${U} cannot read ${PROD_SECRETS}"
    as_user "$U" test -x /etc/corporatetraveldc 2>/dev/null && fail "1b ${U} can traverse /etc/corporatetraveldc" || pass "1b ${U} cannot traverse /etc/corporatetraveldc"
    # 2
    if as_user "$U" test -r "${own_subset}" 2>/dev/null; then
        pass "2 ${U} can read ${own_subset}"
        local owner; owner="$(as_user "$U" stat -c '%U:%G %a' "${own_subset}")"
        [[ "$owner" == "root:${own_group} 640" ]] && pass "2b subset is root:${own_group} 0640" || fail "2b subset owner/mode is '${owner}', want root:${own_group} 640"
        local extra; extra="$(as_user "$U" grep -oE '^[A-Z_0-9]+=' "${own_subset}" | tr -d = | sort | comm -23 - <(grep -vE '^\s*(#|$)' "${allow}" | tr -d '?' | sort))"
        [[ -z "$extra" ]] && pass "2c subset carries only allowlisted names" || fail "2c subset has names outside the allowlist: ${extra//$'\n'/ }"
    else
        fail "2 ${U} cannot read ${own_subset}"
    fi
    as_user "$U" test -r "${other_subset}" 2>/dev/null && fail "2d ${U} can read the OTHER group's subset ${other_subset}" || pass "2d ${U} cannot read ${other_subset}"
    # 3
    as_user "$U" sudo -n true 2>/dev/null && fail "3 ${U} has passwordless sudo" || pass "3 ${U} has no sudo"
    id -nG "$U" | tr ' ' '\n' | grep -qx "${other_group}" && fail "3b ${U} is also in ${other_group}" || pass "3b ${U} is not in ${other_group}"
    id -nG "$U" | tr ' ' '\n' | grep -qx "${DEV_GROUP}" && pass "3c ${U} is in ${DEV_GROUP}" || fail "3c ${U} is not in ${DEV_GROUP}"
    id -nG "$U" | tr ' ' '\n' | grep -qxE "wheel|sudo" && fail "3d ${U} is in wheel/sudo" || pass "3d ${U} not in wheel/sudo"
    # 4 (a service runs no user manager; a preloaded account has no linger yet)
    if [[ "$rkind" == service ]]; then
        local sh; sh="$(getent passwd "$U" | cut -d: -f7)"
        [[ "$sh" == *nologin || "$sh" == /bin/false ]] && pass "4 service ${U} has a nologin shell (${sh})" || fail "4 service ${U} shell is ${sh}, want nologin"
    elif [[ "$rpre" == 1 ]]; then
        local ex; ex="$(sudo chage -l "$U" 2>/dev/null | awk -F': ' '/Account expires/ {print $2}')"
        [[ -n "$ex" && "$ex" != never ]] && pass "4 preloaded ${U} is expired (${ex}) until --activate" || fail "4 preloaded ${U} is NOT expired -- it can be used before activation"
        loginctl show-user "$U" -p Linger 2>/dev/null | grep -q 'Linger=no' && pass "4a preloaded ${U} has no linger" || skip "4a linger state for ${U} not readable (never logged in)"
    else
    local props; props="$(sudo runuser -u "${U}" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "${U}")" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "${U}")/bus" systemctl --user show "${slice}" -p CPUWeight,CPUQuotaPerSecUSec,MemoryMax 2>/dev/null | tr '\n' ' ')"
    [[ "$props" == *"CPUWeight="* && "$props" != *"CPUWeight=100 "* ]] && pass "4 ${slice} loaded in ${U}'s manager (${props})" || fail "4 ${slice} in ${U}'s manager: '${props}'"
    # the primary Claude agent owns the always-on remote-control session (renamed 2026-10-05)
    if [[ "$kind" == agent && ( "$U" == ctdc-agent-anthropic-claude || "$U" == ctdc-agent ) ]]; then
        local st; st="$(sudo runuser -u "${U}" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "${U}")" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "${U}")/bus" systemctl --user is-active "${RC_UNIT}" 2>/dev/null || true)"
        [[ "$st" == active ]] && pass "4b ${RC_UNIT} active in ${U}'s manager" || fail "4b ${RC_UNIT} in ${U}'s manager: '${st}'"
        pgrep -u "${OPERATOR_USER}" -f 'claude --remote-control' >/dev/null && fail "4c a remote-control process still runs as ${OPERATOR_USER}" || pass "4c no remote-control process as ${OPERATOR_USER}"
    fi
    # 2026-10-08: a codex-mode agent owns ITS Codex daemon; none may run as the operator
    if [[ "$kind" == agent && "$rmode" == codex ]]; then
        local cu="corporatetraveldc-codex-remote-control.service" cst
        cst="$(sudo runuser -u "${U}" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "${U}")" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "${U}")/bus" systemctl --user is-active "${cu}" 2>/dev/null || true)"
        [[ "$cst" == active ]] && pass "4d ${cu} active in ${U}'s manager" || fail "4d ${cu} in ${U}'s manager: '${cst}'"
        sudo test -f "/home/${U}/.config/systemd/user/${RC_UNIT}" && fail "4e a Claude remote-control unit is installed on codex account ${U}" || pass "4e no Claude remote-control unit on ${U}"
        pgrep -u "${OPERATOR_USER}" -x codex >/dev/null && fail "4f a codex process still runs as ${OPERATOR_USER} (operator-as-agent exception still in use)" || pass "4f no codex process as ${OPERATOR_USER}"
        sudo test -f "/home/${U}/AGENTS.md" && pass "4g pamphlet installed as AGENTS.md for ${U}" || fail "4g no AGENTS.md pamphlet for ${U} (render-onboarding.sh ${U} --install)"
    fi
    fi
    # 5 attribution
    local ak="/home/${U}/.ssh/authorized_keys" nkeys comment
    nkeys="$(as_user "$U" grep -cE '^(ssh-|ecdsa-)' "$ak" 2>/dev/null || echo 0)"
    if [[ "$rkind" == service ]]; then
        [[ "$nkeys" == 0 ]] && pass "5 service ${U} has no inbound key (nothing can SSH in as it)" || fail "5 service ${U} has ${nkeys} inbound key(s) -- a service identity should have none"
    else
        [[ "$nkeys" == 1 ]] && pass "5 ${U} authorized_keys holds exactly one key" || fail "5 ${U} authorized_keys holds ${nkeys} key(s), want 1"
        comment="$(as_user "$U" awk '/^(ssh-|ecdsa-)/{print $3; exit}' "$ak" 2>/dev/null || true)"
        [[ "$comment" == "${U}@"* ]] && pass "5b key comment '${comment}' names the account" || fail "5b key comment '${comment}' does not start with '${U}@'"
    fi
    # the SIGNING identity (every kind): on-box key, registered; inactive only while preloaded
    as_user "$U" test -f "/home/${U}/.ssh/${U}_ed25519" 2>/dev/null && pass "5d ${U} has its signing key ${U}_ed25519" || fail "5d ${U} has no signing key /home/${U}/.ssh/${U}_ed25519"
    local sst; sst="$("${SELF_DIR}/../board-signer-ctl.sh" show "$U" 2>/dev/null | head -1 | awk '{print $1}')"
    case "${sst:-none}" in
        active)   [[ "$rpre" == 1 ]] && fail "5e preloaded ${U} has an ACTIVE signer (should be inactive until --activate)" || pass "5e ${U} board signer active" ;;
        inactive) [[ "$rpre" == 1 ]] && pass "5e preloaded ${U} signer registered inactive" || fail "5e ${U} board signer INACTIVE (the liveness key factor fails)" ;;
        *)        [[ "$rpre" == 1 ]] && skip "5e ${U} signer not registered yet (preloaded; register inactive)" || fail "5e ${U} has no board signer (board-signer-ctl.sh register)" ;;
    esac
    if [[ "$kind" == agent ]]; then
        local gn; gn="$(as_user "$U" git config --global user.name 2>/dev/null || true)"
        [[ "$gn" == "$U" ]] && pass "5c git user.name is '${U}'" || fail "5c git user.name is '${gn}', want '${U}'"
    fi
    # 6
    [[ -z "$(as_user "$U" gpg --batch --list-secret-keys 2>/dev/null)" ]] && pass "6 ${U} has no GPG secret keys" || fail "6 ${U} has GPG secret keys"
    local ophome; ophome="$(getent passwd "${OPERATOR_USER}" | cut -d: -f6)"
    as_user "$U" test -r "${ophome}/.gnupg" 2>/dev/null && fail "6b ${U} can read ${ophome}/.gnupg" || pass "6b ${U} cannot read ${ophome}/.gnupg"
    # git status as another user trips safe.directory / index-lock, which is not a readability question
    as_user "$U" test -r "${REPO_ROOT}/README.md" -a -r "${REPO_ROOT}/.git/HEAD" && pass "6c ${U} can read the repo (shared group)" || fail "6c ${U} cannot read the repo"
    # 2026-10-04 duel containment: the checkout is group READ-only; a team
    # account that can write it can reach root-run scripts and .git/hooks
    as_user "$U" test -w "${REPO_ROOT}/scripts" 2>/dev/null && fail "6d ${U} can WRITE the repo (group write is the duel's top finding)" || pass "6d ${U} cannot write the repo"
    as_user "$U" test -w "${REPO_ROOT}/.git/hooks" 2>/dev/null && fail "6e ${U} can write .git/hooks" || pass "6e ${U} cannot write .git/hooks"
    id -nG "$U" | tr ' ' '\n' | grep -qx systemd-journal && fail "6f ${U} is in systemd-journal (other accounts' argv/unit output)" || pass "6f ${U} not in systemd-journal"
}

for u in "${ACCOUNTS[@]}"; do check_account "$u"; done

echo "--- fleet"
# 7 cross-account key uniqueness (root read of every authorized_keys)
if sudo -n true 2>/dev/null || sudo -v 2>/dev/null; then
    dup="$(for u in "${ACCOUNTS[@]}"; do sudo awk -v u="$u" '/^(ssh-|ecdsa-)/{print $2, u}' "/home/${u}/.ssh/authorized_keys" 2>/dev/null; done | sort | awk '{c[$1]++; who[$1]=who[$1]" "$2} END{for(k in c) if(c[k]>1) print who[k]}')"
    [[ -z "$dup" ]] && pass "7 no two accounts share a public key" || fail "7 accounts sharing one public key:${dup}"
    opk="$(getent passwd "${OPERATOR_USER}" | cut -d: -f6)/.ssh/authorized_keys"
    for u in "${ACCOUNTS[@]}"; do
        shared="$(sudo awk '/^(ssh-|ecdsa-)/{print $2}' "/home/${u}/.ssh/authorized_keys" 2>/dev/null | grep -Fxf - <(awk '/^(ssh-|ecdsa-)/{print $2}' "$opk") || true)"
        [[ -z "$shared" ]] && pass "7b ${u}'s key is not also in the operator's authorized_keys" || fail "7b ${u}'s key is still in the operator's authorized_keys"
    done
else
    skip "7 cross-account key uniqueness needs sudo (run with sudo credentials cached)"
fi
grep -q ' claude-cowork-dispatch$' "$(getent passwd "${OPERATOR_USER}" | cut -d: -f6)/.ssh/authorized_keys" 2>/dev/null && fail "7c operator authorized_keys still carries the claude-cowork-dispatch comment (stage B not run)" || pass "7c operator authorized_keys has no claude-cowork-dispatch entry"
# 8
pm="$(stat -c '%U:%G %a' "${PROD_SECRETS}")"; [[ "$pm" == "${OPERATOR_USER}:${OPERATOR_USER} 600" ]] && pass "8 production secrets still ${pm}" || fail "8 production secrets are ${pm}"
dm="$(stat -c '%U:%G %a' /etc/corporatetraveldc)"; [[ "$dm" == "root:${OPERATOR_USER} 750" ]] && pass "8b /etc/corporatetraveldc still ${dm}" || fail "8b /etc/corporatetraveldc is ${dm}"

echo; echo "failures: ${fails}"
[[ "$fails" -eq 0 ]]
