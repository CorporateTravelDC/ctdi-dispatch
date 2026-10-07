#!/usr/bin/env bash
# scripts/agent-segmentation/rollback.sh -- reverse plan.sh, per account or the
# shared steps, last step first. DRY-RUN BY DEFAULT; --execute needs root and
# --i-am-the-operator.
#
#   scripts/agent-segmentation/rollback.sh --dry-run --remove-agent ctdc-agent
#   scripts/agent-segmentation/rollback.sh --dry-run --remove-human alice
#   scripts/agent-segmentation/rollback.sh --dry-run --shared        # groups + repo + subsets (after all accounts are gone)
#   sudo scripts/agent-segmentation/rollback.sh --execute --i-am-the-operator --remove-agent ctdc-agent
#
# Production is never touched by plan.sh (the env split is a copy, the repo
# only gains a group), so rollback is: stop the account, put the remote-control
# unit and the cowork key back under the operator (ctdc-agent only), drop its
# sudoers file, remove the account; --shared then removes the subsets, un-shares
# the repo and deletes the groups. docs/AGENT_SEGMENTATION.md "Rollback".
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
OPERATOR_USER="corporatetraveldc"; DEV_GROUP="ctdc-dev"; OPS_GROUP="ctdc-ops"; AGENTS_GROUP="ctdc-agents"
AGENT_ETC="/etc/ctdc-agent"; OPS_ETC="/etc/ctdc-ops"
OPERATOR_HOME="$(getent passwd "${OPERATOR_USER}" | cut -d: -f6)"
COWORK_PUB="${OPERATOR_HOME}/.ssh/cowork_ed25519.pub"
RC_UNIT="corporatetraveldc-claude-remote-control.service"

REGISTRY="/etc/ctdc-accounts.conf"
MODE="dry-run"; OPERATOR_FLAG=0; RM_AGENT=""; RM_HUMAN=""; SHARED=0; prev=""
for a in "$@"; do
    case "$a" in
        --dry-run) MODE="dry-run" ;; --execute) MODE="execute" ;;
        --i-am-the-operator) OPERATOR_FLAG=1 ;;
        --shared) SHARED=1 ;;
        --remove-agent=*|--remove-service=*) RM_AGENT="${a#*=}" ;;   # a service is an agent-group account with no login; same removal
        --remove-human=*) RM_HUMAN="${a#--remove-human=}" ;;
        --remove-agent|--remove-service|--remove-human) ;;
        -h|--help) sed -n 2,15p "$0"; exit 0 ;;
        *) case "$prev" in --remove-agent|--remove-service) RM_AGENT="$a" ;; --remove-human) RM_HUMAN="$a" ;; *) echo "unknown arg: $a" >&2; exit 2 ;; esac ;;
    esac
    prev="$a"
done
for n in "$RM_AGENT" "$RM_HUMAN"; do
    [[ -z "$n" || "$n" =~ ^[a-z_][a-z0-9_-]{1,31}$ ]] || { echo "refusing: bad account name '$n'" >&2; exit 2; }
    [[ "$n" == "$OPERATOR_USER" || "$n" == root ]] && { echo "refusing: '$n' is not a team account" >&2; exit 2; }
done
[[ -n "$RM_AGENT$RM_HUMAN" || "$SHARED" -eq 1 ]] || { echo "nothing to do: --remove-agent NAME | --remove-service NAME | --remove-human NAME | --shared" >&2; exit 2; }
if [[ "$MODE" == "execute" ]]; then
    [[ "$OPERATOR_FLAG" -eq 1 ]] || { echo "refusing: --execute requires --i-am-the-operator" >&2; exit 3; }
    [[ "$(id -u)" -eq 0 ]] || { echo "refusing: --execute requires root" >&2; exit 4; }
fi
run() { printf '   $ %s\n' "$*"; [[ "$MODE" == "execute" ]] && eval "$@" || true; }
step() { echo; echo "== $1"; }

echo "team segmentation ROLLBACK -- mode: ${MODE}"

if [[ -n "$RM_AGENT" ]]; then
    U="$RM_AGENT"; H="/home/${U}"
    if [[ "$U" == "ctdc-agent" ]]; then
        step "remote-control unit back under the operator"
        run "runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/$(id -u ${U}) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u ${U})/bus systemctl --user disable --now ${RC_UNIT} || true"
        run "sudo -u ${OPERATOR_USER} XDG_RUNTIME_DIR=/run/user/$(id -u ${OPERATOR_USER}) systemctl --user enable --now ${RC_UNIT}"
        step "cowork ssh key back to the operator (original comment)"
        run "grep -qF \"\$(cut -d' ' -f1,2 ${COWORK_PUB})\" ${OPERATOR_HOME}/.ssh/authorized_keys || cat ${COWORK_PUB} >> ${OPERATOR_HOME}/.ssh/authorized_keys"
        step "sudoers entry"
        run "rm -f /etc/sudoers.d/50-${U}"
    fi
    step "stop and remove agent/service account ${U} (its generated keypair dies with the home)"
    run "loginctl disable-linger ${U} || true"
    run "loginctl terminate-user ${U} || true"
    run "userdel --remove ${U} || true"
    run "[ -f ${REGISTRY} ] && sed -i '/^${U} /d' ${REGISTRY} || true"
    echo "   # deactivate ${U}'s board signer and revoke its tokens: scripts/board-signer-ctl.sh deactivate ${U} removed; scripts/board-signer-ctl.sh revoke-tokens ${U}"
fi

if [[ -n "$RM_HUMAN" ]]; then
    U="$RM_HUMAN"
    step "stop and remove human account ${U}"
    run "loginctl disable-linger ${U} || true"
    run "loginctl terminate-user ${U} || true"
    run "userdel --remove ${U} || true"
    run "[ -f ${REGISTRY} ] && sed -i '/^${U} /d' ${REGISTRY} || true"
    echo "   # revoke ${U}'s personal API token (auth_tokens) and Nextcloud app password separately -- those are not files"
fi

if (( SHARED )); then
    step "shared: subsets, repo un-share, groups (only once every team account is gone)"
    run "rm -rf ${AGENT_ETC} ${OPS_ETC}"
    run "sudo -u ${OPERATOR_USER} git -C ${REPO_ROOT} config --unset core.sharedRepository || true"
    run "chgrp -R ${OPERATOR_USER} ${REPO_ROOT}"
    run "find ${REPO_ROOT} -type d -perm -g+s -exec chmod g-s {} +"
    run "gpasswd -d ${OPERATOR_USER} ${DEV_GROUP} || true"
    run "groupdel ${AGENTS_GROUP} || true"
    run "groupdel ${OPS_GROUP} || true"
    run "groupdel ${DEV_GROUP} || true"
fi

echo
echo "rotate nothing on rollback -- keys rotated during migration stay rotated."
[[ "$MODE" == "dry-run" ]] && echo "(dry-run: nothing was executed)"
exit 0
