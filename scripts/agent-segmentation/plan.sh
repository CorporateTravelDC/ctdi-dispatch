#!/usr/bin/env bash
# scripts/agent-segmentation/plan.sh -- team segmentation on the dispatch Pi:
# HUMAN teammates (group ctdc-ops, humans.slice) and AGENT runtimes (group
# ctdc-agents, agents.slice) as two parallel groups on one shared repo group
# (ctdc-dev). DRY-RUN BY DEFAULT: prints every command it would run with the
# docs/AGENT_SEGMENTATION.md section each one implements.
#
#   scripts/agent-segmentation/plan.sh --dry-run                       # shared steps only
#   scripts/agent-segmentation/plan.sh --dry-run --add-agent ctdc-agent
#   scripts/agent-segmentation/plan.sh --dry-run --add-human alice --ssh-pubkey-file /path/alice.pub
#   scripts/agent-segmentation/plan.sh --dry-run --add-agent ctdc-agent-cowork --login-mode ssh --ssh-pubkey-file ~/.ssh/cowork_ed25519.pub
#   scripts/agent-segmentation/plan.sh --dry-run --add-agent ctdc-agent-openai-codex --preload
#   scripts/agent-segmentation/plan.sh --dry-run --add-service ctdc-agent-llama
#   scripts/agent-segmentation/plan.sh --dry-run --add-service ctdc-agent-dispatch --preload
#   scripts/agent-segmentation/plan.sh --dry-run --rehome-inbound-key ctdc-agent ctdc-agent-cowork
#   scripts/agent-segmentation/plan.sh --dry-run --activate ctdc-agent-openai-codex
#   scripts/agent-segmentation/plan.sh --dry-run --rename-account ctdc-agent ctdc-agent-anthropic-claude
#   sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --add-agent ctdc-agent --only=1,2,3
#
# ACCOUNT KINDS (2026-10-04 17:00, operator-agreed): every team account has a
# kind and a LOGIN MODE the liveness switch reads from the registry
# /etc/ctdc-accounts.conf (root 0644, "name kind login_mode preloaded"):
#   agent  login_mode claude  -- Claude CLI credentials (refresh within 7d)
#   agent  login_mode codex   -- Codex CLI login (~/.codex/auth.json refreshed within 14d)
#                                AND its codex remote-control unit running (2026-10-08)
#   agent  login_mode ssh     -- no Claude creds; a client (Cowork desktop) SSHes
#                                in; alive while last SSH login <= 7d
#   service login_mode none   -- never logs in; nologin shell; alive = signer
#                                key active AND no revocation AND no kill order
#   human  login_mode ssh     -- as before (ctdc-ops)
# --preload creates the account LOCKED (chage -E 0), no linger, signer
# registered INACTIVE; liveness skips it until `--activate NAME` flips it live.
# Group membership stays the authority for agents vs humans; the registry
# refines it.
#
# The shared steps (groups, repo group-write, both secrets subsets) are
# idempotent and are always listed first, so every invocation re-asserts them.
# Account steps follow and are numbered after the shared ones; --only=N,M
# selects by those numbers for the CURRENT invocation (read the dry-run first).
#
# Execution needs root AND --i-am-the-operator: every step here is privileged
# and operator-only by the platform's standing rule (agents relay root
# commands, never run them). Rollback: rollback.sh (--remove-agent /
# --remove-human / --shared). The operator account stays the only manifest
# signer and the only sudo holder; nothing here changes that.
#
# Why this exists (2026-10-03/04): a Cloudflare token (09-22) and a 9-char
# fragment of NWWS_PASSWORD (10-03) reached session transcripts because every
# agent ran as the operator user with the whole secrets file readable; the
# operator then asked for the same separation for human teammates, in parallel.
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/../.." && pwd)"

OPERATOR_USER="corporatetraveldc"
DEV_GROUP="ctdc-dev"        # shared repo READ (humans + agents; group write removed 2026-10-04)
OPS_GROUP="ctdc-ops"        # human teammates
AGENTS_GROUP="ctdc-agents"  # agent runtimes
AGENT_ETC="/etc/ctdc-agent"; AGENT_SECRETS="${AGENT_ETC}/agent-secrets.env"
OPS_ETC="/etc/ctdc-ops";     OPS_SECRETS="${OPS_ETC}/ops-secrets.env"
OPERATOR_HOME="$(getent passwd "${OPERATOR_USER}" | cut -d: -f6)"
COWORK_PUB="${OPERATOR_HOME}/.ssh/cowork_ed25519.pub"
RC_UNIT="corporatetraveldc-claude-remote-control.service"
CODEX_RC_UNIT="corporatetraveldc-codex-remote-control.service"
CODEX_UNIT_SRC="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}/scripts/agent-segmentation/units/${CODEX_RC_UNIT}"
CODEX_ARTIFACTS="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}/config/codex/artifacts.sha256"
REGISTRY="/etc/ctdc-accounts.conf"   # name kind login_mode preloaded(0|1); NOT under /etc/ctdc-agent (0750 root:ctdc-agents -- the operator and ctdc-ops could not read it there)
AGENTS_SLICE_SRC="${REPO_ROOT}/.config/systemd/user/agents.slice"
HUMANS_SLICE_SRC="${REPO_ROOT}/.config/systemd/user/humans.slice"

MODE="dry-run"; OPERATOR_FLAG=0; ONLY=""
ADD_AGENT=""; ADD_HUMAN=""; ADD_SERVICE=""; SSH_PUBKEY_FILE=""
LOGIN_MODE=""; PRELOAD=0; ACTIVATE=""; REHOME_FROM=""; REHOME_TO=""; RENAME_FROM=""; RENAME_TO=""; TO_SERVICE=""
prev=""; prev2=""
for a in "$@"; do
    case "$a" in
        --dry-run) MODE="dry-run" ;;
        --execute) MODE="execute" ;;
        --i-am-the-operator) OPERATOR_FLAG=1 ;;
        --preload) PRELOAD=1 ;;
        --only=*) ONLY="${a#--only=}" ;;
        --add-agent=*) ADD_AGENT="${a#--add-agent=}" ;;
        --add-human=*) ADD_HUMAN="${a#--add-human=}" ;;
        --add-service=*) ADD_SERVICE="${a#--add-service=}" ;;
        --ssh-pubkey-file=*) SSH_PUBKEY_FILE="${a#--ssh-pubkey-file=}" ;;
        --login-mode=*) LOGIN_MODE="${a#--login-mode=}" ;;
        --activate=*) ACTIVATE="${a#--activate=}" ;;
        --only|--add-agent|--add-human|--add-service|--ssh-pubkey-file|--login-mode|--activate|--rehome-inbound-key|--rename-account|--convert-to-service) ;;   # value(s) follow
        -h|--help) sed -n 2,40p "$0"; exit 0 ;;
        *) case "$prev" in
               --only) ONLY="$a" ;;
               --add-agent) ADD_AGENT="$a" ;;
               --add-human) ADD_HUMAN="$a" ;;
               --add-service) ADD_SERVICE="$a" ;;
               --ssh-pubkey-file) SSH_PUBKEY_FILE="$a" ;;
               --login-mode) LOGIN_MODE="$a" ;;
               --activate) ACTIVATE="$a" ;;
               --rehome-inbound-key) REHOME_FROM="$a"; a="--rehome-inbound-key:2" ;;
               --rehome-inbound-key:2) REHOME_TO="$a"; a="" ;;
               --convert-to-service) TO_SERVICE="$a" ;;
               --rename-account) RENAME_FROM="$a"; a="--rename-account:2" ;;
               --rename-account:2) RENAME_TO="$a"; a="" ;;
               *) echo "unknown arg: $a" >&2; exit 2 ;;
           esac ;;
    esac
    prev="$a"
done
n_targets=0; for t in "$ADD_AGENT" "$ADD_HUMAN" "$ADD_SERVICE" "$ACTIVATE" "$REHOME_FROM" "$RENAME_FROM" "$TO_SERVICE"; do [[ -n "$t" ]] && n_targets=$((n_targets+1)); done
(( n_targets > 1 )) && { echo "refusing: one operation per invocation (--add-agent | --add-human | --add-service | --activate | --rehome-inbound-key | --rename-account)" >&2; exit 2; }
[[ -n "$RENAME_FROM" && -z "$RENAME_TO" ]] && { echo "refusing: --rename-account needs FROM and TO" >&2; exit 2; }
[[ -n "$REHOME_FROM" && -z "$REHOME_TO" ]] && { echo "refusing: --rehome-inbound-key needs FROM and TO" >&2; exit 2; }
for n in "$ADD_AGENT" "$ADD_HUMAN" "$ADD_SERVICE" "$ACTIVATE" "$REHOME_FROM" "$REHOME_TO" "$RENAME_FROM" "$RENAME_TO" "$TO_SERVICE"; do
    [[ -z "$n" || "$n" =~ ^[a-z_][a-z0-9_-]{1,31}$ ]] || { echo "refusing: bad account name '$n'" >&2; exit 2; }
    [[ "$n" == "$OPERATOR_USER" || "$n" == root ]] && { echo "refusing: '$n' is not a team account" >&2; exit 2; }
done
case "$LOGIN_MODE" in ""|claude|ssh|codex) ;; *) echo "refusing: --login-mode must be claude, codex or ssh" >&2; exit 2 ;; esac
# 2026-10-08: --login-mode also applies to --activate (switch a preloaded agent's mode, e.g. claude -> codex)
[[ -n "$LOGIN_MODE" && -z "$ADD_AGENT$ACTIVATE" ]] && { echo "refusing: --login-mode applies to --add-agent or --activate only" >&2; exit 2; }
[[ "$LOGIN_MODE" == ssh && -n "$ACTIVATE" ]] && { echo "refusing: switching to ssh mode at activation is not supported (needs an inbound key: use --add-agent)" >&2; exit 2; }
[[ -n "$ADD_AGENT" && -z "$LOGIN_MODE" ]] && LOGIN_MODE=claude
[[ "$LOGIN_MODE" == ssh && -z "$SSH_PUBKEY_FILE" && -n "$ADD_AGENT" ]] && { echo "refusing: --login-mode ssh needs --ssh-pubkey-file (the client's public key, e.g. ~/.ssh/cowork_ed25519.pub)" >&2; exit 2; }
(( PRELOAD )) && [[ -z "$ADD_AGENT$ADD_SERVICE" ]] && { echo "refusing: --preload applies to --add-agent / --add-service" >&2; exit 2; }
if [[ "$MODE" == "execute" ]]; then
    [[ "$OPERATOR_FLAG" -eq 1 ]] || { echo "refusing: --execute requires --i-am-the-operator" >&2; exit 3; }
    [[ "$(id -u)" -eq 0 ]] || { echo "refusing: --execute requires root (run via sudo)" >&2; exit 4; }
    [[ -n "$SSH_PUBKEY_FILE" && ! -r "$SSH_PUBKEY_FILE" ]] && { echo "refusing: cannot read --ssh-pubkey-file $SSH_PUBKEY_FILE" >&2; exit 5; }
    [[ -n "$ACTIVATE" ]] && ! getent passwd "$ACTIVATE" >/dev/null && { echo "refusing: --activate: no such account $ACTIVATE" >&2; exit 5; }
    if [[ -n "$RENAME_FROM" ]]; then
        getent passwd "$RENAME_FROM" >/dev/null || { echo "refusing: --rename-account: no such account $RENAME_FROM" >&2; exit 5; }
        getent passwd "$RENAME_TO" >/dev/null && { echo "refusing: --rename-account: $RENAME_TO already exists" >&2; exit 5; }
        id -nG "$RENAME_FROM" | tr ' ' '\n' | grep -qxE "${AGENTS_GROUP}|${OPS_GROUP}" || { echo "refusing: $RENAME_FROM is not a team account" >&2; exit 5; }
    fi
fi
# registry line: "name kind login_mode preloaded" (idempotent replace); the file
# is root 0644 so the liveness switch, verify.sh and the pamphlet can read it
registry_set() {   # <name> <kind> <login_mode> <preloaded>
    run "touch ${REGISTRY} && sed -i '/^$1 /d' ${REGISTRY} && printf '%s %s %s %s\\n' '$1' '$2' '$3' '$4' >> ${REGISTRY} && chmod 0644 ${REGISTRY} && chown root:root ${REGISTRY}"
}
registry_get() {   # <name> -> "kind login_mode preloaded" or empty
    [[ -r "$REGISTRY" ]] && awk -v n="$1" '$1==n {print $2, $3, $4}' "$REGISTRY" | tail -1 || true
}

step_n=0; CUR_STEP=0
in_scope() { [[ -z "$ONLY" ]] || [[ ",${ONLY}," == *",$1,"* ]]; }
step() {  # step <title> <doc-section>
    step_n=$((step_n+1)); CUR_STEP=$step_n
    if in_scope "$step_n"; then echo; echo "== step ${step_n}: $1"; echo "   doc: $2"; fi
}
run() {   # printed in dry-run, executed in execute mode
    in_scope "$CUR_STEP" || return 0
    printf '   $ %s\n' "$*"
    [[ "$MODE" == "execute" ]] || return 0
    # 2026-10-04 (duel L3): was `eval "$@" || true` -- every failed step was
    # swallowed and the plan exited 0 (stage C's "unit could not be found"
    # carried straight on). Fail fast; steps that may legitimately fail
    # already carry their own `|| true`.
    if ! eval "$@"; then
        printf '\nFAILED at step %s: %s\n' "$CUR_STEP" "$*" >&2
        printf 'nothing after this step ran; fix and re-run with --only=%s (steps are idempotent)\n' "$CUR_STEP" >&2
        exit 1
    fi
}
note() { in_scope "$CUR_STEP" && printf '   # %s\n' "$*" || true; }

echo "team segmentation plan -- mode: ${MODE}  repo: ${REPO_ROOT}"
echo "design: docs/AGENT_SEGMENTATION.md ('Team segmentation: humans and agents')"
[[ -n "$ADD_AGENT" ]] && echo "account: AGENT ${ADD_AGENT} (groups ${DEV_GROUP},${AGENTS_GROUP}; agents.slice; login-mode ${LOGIN_MODE}; preload ${PRELOAD}${SSH_PUBKEY_FILE:+; inbound key file: ${SSH_PUBKEY_FILE}})"
[[ -n "$ADD_HUMAN" ]] && echo "account: HUMAN ${ADD_HUMAN} (groups ${DEV_GROUP},${OPS_GROUP}; humans.slice; key file: ${SSH_PUBKEY_FILE:-<none given>})"
[[ -n "$ADD_SERVICE" ]] && echo "account: SERVICE ${ADD_SERVICE} (groups ${DEV_GROUP},${AGENTS_GROUP}; nologin; signing key only; preload ${PRELOAD})"
[[ -n "$ACTIVATE" ]] && echo "operation: ACTIVATE preloaded account ${ACTIVATE}"
[[ -n "$REHOME_FROM" ]] && echo "operation: REHOME inbound key ${REHOME_FROM} -> ${REHOME_TO} (${REHOME_FROM} keeps its own generated key as its single inbound key)"
[[ -n "$RENAME_FROM" ]] && echo "operation: RENAME account ${RENAME_FROM} -> ${RENAME_TO} (uid, groups, keys, signer and grants carried over)"
[[ -n "$TO_SERVICE" ]] && echo "operation: CONVERT ${TO_SERVICE} to a service identity (no login; liveness = signer + tokens + kill orders)"
[[ -z "$ADD_AGENT$ADD_HUMAN$ADD_SERVICE$ACTIVATE$REHOME_FROM$RENAME_FROM$TO_SERVICE" ]] && echo "account: none -- shared steps only (add --add-agent NAME | --add-human NAME | --add-service NAME | --activate NAME | --rehome-inbound-key FROM TO)"

# ----------------------------------------------------------------- shared --
step "groups: ${DEV_GROUP} (repo read), ${OPS_GROUP} (humans), ${AGENTS_GROUP} (agents)" "'Groups and accounts'"
run "getent group ${DEV_GROUP} >/dev/null || groupadd ${DEV_GROUP}"
run "getent group ${OPS_GROUP} >/dev/null || groupadd ${OPS_GROUP}"
run "getent group ${AGENTS_GROUP} >/dev/null || groupadd ${AGENTS_GROUP}"
run "usermod -aG ${DEV_GROUP} ${OPERATOR_USER}"
note "the operator joins ${DEV_GROUP} only (group READ of the repo); ${OPS_GROUP}/${AGENTS_GROUP} are for the team accounts. No wheel/sudo for any team account."

step "repo: group READ only (the 2026-10-04 containment, re-asserted every run)" "'Repo and signing'"
# 2026-10-04 (adversarial duel, top finding): group WRITE on the operator's
# checkout let ctdc-agent reach root-run scripts, the signing trust anchor,
# .git/hooks and the operator's Stop hook. The operator contained it by hand
# (chmod -R g-w, core.sharedRepository=false). This step used to re-apply
# g+rwX + sharedRepository=group on EVERY invocation, i.e. any later --add-*
# run would have reopened the hole; it now re-asserts the contained state.
run "chgrp -R ${DEV_GROUP} ${REPO_ROOT}"
run "chmod -R g+rX,g-w ${REPO_ROOT}"
run "find ${REPO_ROOT} -type d -exec chmod g+s {} +"
run "sudo -u ${OPERATOR_USER} git -C ${REPO_ROOT} config core.sharedRepository false"
note "team accounts READ the checkout (and may clone it into their own home to prepare patches); they never write it. Contributions go through the signed workspace route; code lands only via the operator's signed commit"
note "GPG key + passphrase stay with the operator: no team account can commit-sign or sign the manifest"

step "secrets subsets: agent profile -> ${AGENT_SECRETS}, ops profile -> ${OPS_SECRETS}" "'Secrets: two allowlists'"
note "/etc/corporatetraveldc is root:${OPERATOR_USER} 0750 -- team accounts cannot traverse it, so each group gets its own dir"
run "install -d -m 0750 -o root -g ${AGENTS_GROUP} ${AGENT_ETC}"
run "install -d -m 0750 -o root -g ${OPS_GROUP} ${OPS_ETC}"
run "python3 ${SELF_DIR}/secrets-subset.py --profile agent --check"
run "python3 ${SELF_DIR}/secrets-subset.py --profile agent"
run "python3 ${SELF_DIR}/secrets-subset.py --profile ops --check"
run "python3 ${SELF_DIR}/secrets-subset.py --profile ops"
note "production dispatch-secrets.env is never modified (rollback stays trivial); re-run this step after any rotation"

# ------------------------------------------------------------ agent account --
if [[ -n "$ADD_AGENT" ]]; then
    U="$ADD_AGENT"; H="/home/${U}"
    step "agent account ${U} (login-mode ${LOGIN_MODE}$( (( PRELOAD )) && echo ", PRELOADED" ))" "'Groups and accounts' (agent) / 'Account kinds'"
    run "getent passwd ${U} >/dev/null || useradd --create-home --home-dir ${H} --shell /bin/bash --groups ${DEV_GROUP},${AGENTS_GROUP} --comment 'agent runtime, no sudo' ${U}"
    note "NOT systemd-journal (duel 2026-10-04: the journal carries other accounts' argv and unit output; the operator removed ctdc-agent from it). Diagnostics the agent needs come from the board / status endpoints"
    if (( PRELOAD )); then
        run "chage -E 0 ${U}"
        note "PRELOADED: account exists but is expired (chage -E 0), no linger, signer registered INACTIVE; the liveness switch skips it. Later: plan.sh --activate ${U}"
    else
        run "loginctl enable-linger ${U}"
    fi
    registry_set "${U}" agent "${LOGIN_MODE}" "${PRELOAD}"

    step "agent home ${H}: CLI state, agents.slice, skills by grant" "'Agent home' / 'Skills: grants and clawback'"
    run "install -d -m 0700 -o ${U} -g ${U} ${H}/.claude ${H}/.config/systemd/user ${H}/.local/bin"
    run "install -m 0644 -o ${U} -g ${U} ${AGENTS_SLICE_SRC} ${H}/.config/systemd/user/agents.slice"
    run "${REPO_ROOT}/scripts/skill-grants.sh grant ${U} '*'"
    run "${REPO_ROOT}/scripts/skill-grants.sh apply --execute"
    note "2026-10-04: skills are GRANTED, not copied by the account -- every tracked + pinned vendor skill by default (grant ${U} *); claw back with: sudo scripts/skill-grants.sh deny ${U} <skill> [--task ID] [--until ISO+offset]. apply also merges the context-guardian hooks into ${H}/.claude/settings.json (as ${U}). The hourly corporatetraveldc-skill-grants.timer keeps it reconciled."
    if [[ "$LOGIN_MODE" == codex ]]; then
        note "login-mode codex: no Claude CLI; the Codex CLI and its remote-control unit are installed below (codex_setup)"
    elif [[ "$LOGIN_MODE" == claude ]]; then
        note "PREREQ: install the claude CLI for ${U} (${H}/.local/bin/claude, same method as the operator), then one-time auth AND the workspace-trust prompt for ${H}: sudo -u ${U} -i claude (accept 'Yes, I trust this folder', /exit) -- a headless unit parks on that dialog otherwise (2026-10-04 16:25)"
        note "Codex: not part of the first migration (operator 2026-10-03); later = install its CLI for ${U} + remote-access flag, no re-migration"
    else
        note "login-mode ssh: no Claude CLI, no credentials file; the client (e.g. the Cowork desktop app) SSHes in as ${U} with the inbound key below and the liveness switch counts its last SSH login (<= 7d)"
    fi

    step "agent env: the subset, never the production file" "'Secrets: two allowlists'"
    run "printf '%s\n' 'export WITH_DISPATCH_ENV_FILES=${AGENT_SECRETS}' > ${H}/.config/ctdc-env.sh && chown ${U}:${U} ${H}/.config/ctdc-env.sh"
    note "agent tooling runs scripts via scripts/with-dispatch-env.sh (verbatim loader); with that export it reads ${AGENT_SECRETS} -- never source"

    step "attribution: ${U}'s OWN ssh identity + git author (one key per account, comment = account@host)" "'Attribution'"
    run "install -d -m 0700 -o ${U} -g ${U} ${H}/.ssh"
    if [[ "$U" == "ctdc-agent" ]]; then
        note "ctdc-agent is the exception: the existing claude-cowork key becomes THIS account's key (moved, not copied) -- its private half stays with the Cowork client; the comment is rewritten so the audit trail names the account, not 'cowork'"
        run "awk '{\$NF=\"${U}@corporatetraveldc-dispatch\"; print}' ${COWORK_PUB} > ${H}/.ssh/authorized_keys && chown ${U}:${U} ${H}/.ssh/authorized_keys && chmod 0600 ${H}/.ssh/authorized_keys"
        run "sed -i.bak-$(date +%Y%m%d) '/ claude-cowork-dispatch\$/d' ${OPERATOR_HOME}/.ssh/authorized_keys"
        # 2026-10-04 16:44 (first-contact): the cowork key's PRIVATE half lives with the
        # Cowork client, so the account had nothing to SIGN with (board posts, kill
        # orders). Inbound identity (cowork pubkey in authorized_keys) and on-box
        # signing identity are separate: generate the signing key too and register
        # THAT as the board signer. authorized_keys stays exactly one key.
        run "[ -f ${H}/.ssh/${U}_ed25519 ] || sudo -u ${U} ssh-keygen -q -t ed25519 -N '' -C '${U}@corporatetraveldc-dispatch' -f ${H}/.ssh/${U}_ed25519"
        note "register the SIGNING key: (as the operator, NOT under sudo -- board-signer-ctl talks to the operator's rootless podman) T=\$(mktemp); sudo cat ${H}/.ssh/${U}_ed25519.pub > \$T; ${REPO_ROOT}/scripts/board-signer-ctl.sh register ${U} \$T --kind agent; rm -f \$T"
        note "the operator's own keys are untouched; the cowork pubkey leaves the operator's authorized_keys for good"
    else
        run "[ -f ${H}/.ssh/${U}_ed25519 ] || sudo -u ${U} ssh-keygen -q -t ed25519 -N '' -C '${U}@corporatetraveldc-dispatch' -f ${H}/.ssh/${U}_ed25519"
        if [[ -n "$SSH_PUBKEY_FILE" ]]; then
            # inbound identity comes from the client (login-mode ssh, e.g. the Cowork
            # desktop app's key): installed as this account's SINGLE authorized key
            # with the comment rewritten so the audit trail names the account.
            run "awk '{\$NF=\"${U}@corporatetraveldc-dispatch\"; print}' ${SSH_PUBKEY_FILE} > ${H}/.ssh/authorized_keys && chown ${U}:${U} ${H}/.ssh/authorized_keys && chmod 0600 ${H}/.ssh/authorized_keys"
            note "inbound key = the client's (${SSH_PUBKEY_FILE}), comment rewritten; signing key = ${H}/.ssh/${U}_ed25519 (generated here). Two identities, see docs/BOARD_SIGNING.md"
        else
            run "install -m 0600 -o ${U} -g ${U} ${H}/.ssh/${U}_ed25519.pub ${H}/.ssh/authorized_keys"
            run "cat ${H}/.ssh/${U}_ed25519.pub"
            note "register that public key wherever this agent's client connects from; NEVER copy the operator's or another agent's key into this account"
        fi
        if (( PRELOAD )); then note "register the SIGNING key INACTIVE: (as the operator, NOT under sudo -- board-signer-ctl talks to the operator's rootless podman) T=\$(mktemp); sudo cat ${H}/.ssh/${U}_ed25519.pub > \$T; ${REPO_ROOT}/scripts/board-signer-ctl.sh register ${U} \$T --kind agent && ${REPO_ROOT}/scripts/board-signer-ctl.sh deactivate ${U} preloaded; rm -f \$T"
        else note "register the SIGNING key: (as the operator, NOT under sudo -- board-signer-ctl talks to the operator's rootless podman) T=\$(mktemp); sudo cat ${H}/.ssh/${U}_ed25519.pub > \$T; ${REPO_ROOT}/scripts/board-signer-ctl.sh register ${U} \$T --kind agent; rm -f \$T"; fi
    fi
    run "sudo -u ${U} git config --global user.name '${U}' && sudo -u ${U} git config --global user.email '${U}@corporatetraveldc-dispatch.invalid'"
    note "commits an agent prepares carry the account as author; signing (GPG) stays operator-only, so nothing lands unsigned"

    if [[ "$U" == "ctdc-agent" ]]; then

        step "sudoers: operator may become ${U}; ${U} has no sudo" "'sudo: none for team accounts'"
        run "printf '%s\n' '# operator drives agents as ${U}; the agent user itself has NO sudo' '${OPERATOR_USER} ALL=(${U}) NOPASSWD: ${REPO_ROOT}/scripts/agent-run.sh' > /etc/sudoers.d/50-${U}"
        run "chmod 0440 /etc/sudoers.d/50-${U} && visudo -cf /etc/sudoers.d/50-${U}"

        step "remote-control unit moves to the ${U} user manager (NOT from the remote-control session)" "'First agent: remote-control hand-over'"
        run "install -m 0644 -o ${U} -g ${U} ${OPERATOR_HOME}/.config/systemd/user/${RC_UNIT} ${H}/.config/systemd/user/${RC_UNIT}"
        # persistent session name in the mobile app, derived from the ACCOUNT name
        # (operator convention 2026-10-04: "(ACCOUNT) Corporate Travel Dispatch Remote
        # Control" -- name agent accounts by vendor, e.g. ctdc-agent-openai-codex)
        run "install -d -m 0700 -o ${U} -g ${U} ${H}/.config/systemd/user/${RC_UNIT}.d"
        run "printf '%s\\n' '[Service]' 'ExecStart=' 'ExecStart=/usr/bin/python3 -c \"import pty,sys; pty.spawn(sys.argv[1:])\" %h/.local/bin/claude --remote-control \"($(echo ${U} | tr a-z A-Z)) Corporate Travel Dispatch Remote Control\"' > ${H}/.config/systemd/user/${RC_UNIT}.d/name.conf && chown ${U}:${U} ${H}/.config/systemd/user/${RC_UNIT}.d/name.conf"
        run "sudo -u ${OPERATOR_USER} XDG_RUNTIME_DIR=/run/user/$(id -u ${OPERATOR_USER}) systemctl --user disable --now ${RC_UNIT}"
        run "runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/$(id -u ${U} 2>/dev/null || echo UID) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u ${U} 2>/dev/null || echo UID)/bus systemctl --user daemon-reload"
        run "runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/$(id -u ${U} 2>/dev/null || echo UID) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u ${U} 2>/dev/null || echo UID)/bus systemctl --user enable --now ${RC_UNIT}"

        step "agents.slice live in the ${U} manager" "'Resource isolation'"
        run "runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/$(id -u ${U} 2>/dev/null || echo UID) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u ${U} 2>/dev/null || echo UID)/bus systemctl --user show agents.slice -p CPUWeight,CPUQuotaPerSecUSec,MemoryMax"

        step "rotate every key an agent could read before the split" "'Rotation'"
        note "operator action, names only: DISPATCH_ADMIN_TOKEN, NTFY_TOKEN, NEXTCLOUD_APP_PASSWORD, BOARD_KEY (NWWS already decided 10-03); then re-run the shared secrets step"
    elif [[ "$LOGIN_MODE" == codex ]]; then
        codex_setup "${U}" "${H}"
    elif [[ "$LOGIN_MODE" == ssh ]]; then
        step "additional agent ${U} (login-mode ssh): slice + env only -- no remote-control unit, the client connects over SSH" "'Account kinds'"
        run "runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/$(id -u ${U} 2>/dev/null || echo UID) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u ${U} 2>/dev/null || echo UID)/bus systemctl --user daemon-reload || true"
        note "scheduled client tasks (Cowork) run as ${U} under its subset (${AGENT_SECRETS}); nothing here starts a Claude session on the box"
    else
        step "additional agent ${U}: slice + env + its OWN remote-control unit (installed disabled)" "'Adding a second agent'"
        # 2026-10-04 (operator): every agent account gets its own pairing, named
        # from the account -- "(ACCOUNT) Corporate Travel Dispatch Remote Control".
        # Installed DISABLED: enable only after the account's one-time login and
        # workspace-trust accept (see step 5), else the unit parks on the dialog.
        run "install -m 0644 -o ${U} -g ${U} ${OPERATOR_HOME}/.config/systemd/user/${RC_UNIT} ${H}/.config/systemd/user/${RC_UNIT}"
        run "install -d -m 0700 -o ${U} -g ${U} ${H}/.config/systemd/user/${RC_UNIT}.d"
        run "printf '%s\\n' '[Service]' 'ExecStart=' 'ExecStart=/usr/bin/python3 -c \"import pty,sys; pty.spawn(sys.argv[1:])\" %h/.local/bin/claude --remote-control \"($(echo ${U} | tr a-z A-Z)) Corporate Travel Dispatch Remote Control\"' > ${H}/.config/systemd/user/${RC_UNIT}.d/name.conf && chown ${U}:${U} ${H}/.config/systemd/user/${RC_UNIT}.d/name.conf"
        run "runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/$(id -u ${U} 2>/dev/null || echo UID) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u ${U} 2>/dev/null || echo UID)/bus systemctl --user daemon-reload || true"
        note "after login + trust: runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/$(id -u ${U} 2>/dev/null || echo UID) systemctl --user enable --now ${RC_UNIT}"
        note "the ssh key from step 7 is this account's own; no sudoers entry (operator drives it with sudo -u ${U} / agent-run.sh); a non-Claude agent (codex, grok, ...) swaps the ExecStart in name.conf for its own CLI"
    fi
fi

# ---------------------------------------------------------- service account --
if [[ -n "$ADD_SERVICE" ]]; then
    U="$ADD_SERVICE"; H="/home/${U}"
    step "service account ${U} (kind service, login-mode none$( (( PRELOAD )) && echo ", PRELOADED" ))" "'Account kinds' (service)"
    run "getent passwd ${U} >/dev/null || useradd --create-home --home-dir ${H} --shell /usr/sbin/nologin --groups ${DEV_GROUP},${AGENTS_GROUP} --comment 'service identity, never logs in, no sudo' ${U}"
    note "nologin shell: a service identity signs, it never gets a session; no linger, no user manager, no Claude CLI, no remote-control unit"
    (( PRELOAD )) && run "chage -E 0 ${U}"
    registry_set "${U}" service none "${PRELOAD}"

    step "service env: the agent subset, never the production file" "'Secrets: two allowlists'"
    run "install -d -m 0700 -o ${U} -g ${U} ${H}/.config"
    run "printf '%s\n' 'export WITH_DISPATCH_ENV_FILES=${AGENT_SECRETS}' > ${H}/.config/ctdc-env.sh && chown ${U}:${U} ${H}/.config/ctdc-env.sh"
    note "the service's own units (llama server, the future dispatch panel) run its commands via scripts/with-dispatch-env.sh, which also finds the subset on its own"

    step "attribution: ${U}'s SIGNING key only (no inbound key unless --ssh-pubkey-file)" "'Attribution' / 'Account kinds'"
    run "install -d -m 0700 -o ${U} -g ${U} ${H}/.ssh"
    run "[ -f ${H}/.ssh/${U}_ed25519 ] || sudo -u ${U} ssh-keygen -q -t ed25519 -N '' -C '${U}@corporatetraveldc-dispatch' -f ${H}/.ssh/${U}_ed25519"
    if [[ -n "$SSH_PUBKEY_FILE" ]]; then
        run "awk '{\$NF=\"${U}@corporatetraveldc-dispatch\"; print}' ${SSH_PUBKEY_FILE} > ${H}/.ssh/authorized_keys && chown ${U}:${U} ${H}/.ssh/authorized_keys && chmod 0600 ${H}/.ssh/authorized_keys"
    else
        run "rm -f ${H}/.ssh/authorized_keys"
        note "no inbound key: nothing can SSH in as ${U}; the only way to act as it is a unit running as ${U} or the operator's sudo -u"
    fi
    run "sudo -u ${U} git config --global user.name '${U}' && sudo -u ${U} git config --global user.email '${U}@corporatetraveldc-dispatch.invalid'"
    if (( PRELOAD )); then note "register the SIGNING key INACTIVE: (as the operator, NOT under sudo -- board-signer-ctl talks to the operator's rootless podman) T=\$(mktemp); sudo cat ${H}/.ssh/${U}_ed25519.pub > \$T; ${REPO_ROOT}/scripts/board-signer-ctl.sh register ${U} \$T --kind service --role service && ${REPO_ROOT}/scripts/board-signer-ctl.sh deactivate ${U} preloaded; rm -f \$T"
    else note "register the SIGNING key: (as the operator, NOT under sudo -- board-signer-ctl talks to the operator's rootless podman) T=\$(mktemp); sudo cat ${H}/.ssh/${U}_ed25519.pub > \$T; ${REPO_ROOT}/scripts/board-signer-ctl.sh register ${U} \$T --kind service --role service; rm -f \$T"; fi
    note "liveness for a service = signer key active AND no token revocation (24h) AND no kill order -- there is no login factor to go stale"
    if [[ "$U" == ctdc-agent-llama ]]; then
        note "llama as a council/arena participant (Wave 2): after the signer is registered, sudo systemctl enable --now corporatetraveldc-llama-council.timer (unit installed by install-root-copies.sh; runs scripts/llama-council-responder.py AS ${U}, draft-only)"
    fi
fi

# 2026-10-08: Codex for a codex-mode agent -- the CLI is COPIED from the operator's
# verified standalone install (digest pinned in config/codex/artifacts.sha256),
# never fetched with the vendor's curl|sh installer; then its own remote-control
# unit (installed disabled until the account has logged in).
codex_setup() {   # <user> <home>
    local U="$1" H="$2" ver line src sum
    line=$(grep -v '^#' "$CODEX_ARTIFACTS" | grep -m1 'codex-' || true)
    sum=${line%% *}; ver=$(echo "$line" | awk '{print $2}' | sed -E 's/^codex-([^-]+)-(.*)$/\1-\2/')
    src="${OPERATOR_HOME}/.codex/packages/standalone/releases/${ver}/bin/codex"
    # 2026-10-08: the WHOLE standalone package, not the binary alone -- the binary
    # refuses to run without it ("this CLI has no complete local package"; the first
    # activation failed that way). Checked file by file against the signed manifest.
    # SUPERSEDED 2026-10-08: install -m 0755 <binary> ~/.local/bin/codex
    local rel="${OPERATOR_HOME}/.codex/packages/standalone/releases/${ver}" pk="${H}/.codex/packages/standalone"
    local man; man="$(dirname "$CODEX_ARTIFACTS")/package-${ver}.sha256"
    step "Codex CLI for ${U}: verified copy of the ${ver} standalone package (no download)" "'Account kinds' (codex)"
    run "echo '${sum}  ${src}' | sha256sum --check --strict --quiet"
    run "(cd ${rel} && grep -v '^#' ${man} | sha256sum --check --strict --quiet)"
    run "install -d -m 0700 -o ${U} -g ${U} ${H}/.local/bin ${H}/.codex ${H}/.codex/packages ${pk} ${pk}/releases ${H}/.config/systemd/user"
    run "cp -a --no-preserve=ownership ${rel} ${pk}/releases/ && chown -R ${U}:${U} ${H}/.codex/packages"
    run "diff -r ${rel} ${pk}/releases/${ver}"
    run "ln -sfn ${pk}/releases/${ver} ${pk}/current && ln -sfn ${pk}/current/bin/codex ${H}/.local/bin/codex && chown -h ${U}:${U} ${pk}/current ${H}/.local/bin/codex"
    step "Codex remote-control unit for ${U} (installed DISABLED until it has logged in)" "'Account kinds' (codex)"
    run "install -m 0644 -o ${U} -g ${U} ${CODEX_UNIT_SRC} ${H}/.config/systemd/user/${CODEX_RC_UNIT}"
    # unconditional (idempotent): the operator cannot see into the account's home in a
    # dry run, and a dry run must show every step the execute run takes
    run "runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/$(id -u ${U} 2>/dev/null || echo UID) systemctl --user disable --now ${RC_UNIT} 2>/dev/null || true"
    run "rm -f ${H}/.config/systemd/user/${RC_UNIT} && rm -rf ${H}/.config/systemd/user/${RC_UNIT}.d"
    note "no Claude remote-control unit on a codex account: this account runs Codex, not Claude"
    run "runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/$(id -u ${U} 2>/dev/null || echo UID) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u ${U} 2>/dev/null || echo UID)/bus systemctl --user daemon-reload || true"
    note "then, in the SAME sitting (liveness checks hourly): sudo -u ${U} -i codex login --device-auth   (approve the code on your phone or laptop)"
    note "then: runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/\$(id -u ${U}) systemctl --user enable --now ${CODEX_RC_UNIT}   and pair: sudo -u ${U} -i codex remote-control pair"
}

# ------------------------------------------------------------------ activate --
if [[ -n "$ACTIVATE" ]]; then
    U="$ACTIVATE"; H="/home/${U}"
    reg=$(registry_get "$U"); rkind=${reg%% *}; rmode=$(echo "$reg" | awk '{print $2}')
    if [[ -z "$reg" ]]; then   # not in the registry: infer from the shell (a nologin account is a service), else agent/claude
        case "$(getent passwd "$U" 2>/dev/null | cut -d: -f7)" in *nologin|/bin/false) rkind=service; rmode=none; reg="(inferred from nologin shell) service none" ;; *) rkind=agent; rmode=claude ;; esac
    fi
    step "activate preloaded account ${U} (registry: ${reg:-not listed -- defaults agent claude})" "'Account kinds' (preload / activate)"
    run "chage -E -1 ${U}"
    run "usermod -U ${U} 2>/dev/null || usermod -p '*' ${U}"
    if [[ "${rkind:-agent}" != service ]]; then run "loginctl enable-linger ${U}"; fi
    if [[ -n "$LOGIN_MODE" ]]; then note "login mode ${rmode:-claude} -> ${LOGIN_MODE} (registry)"; rmode="$LOGIN_MODE"; fi
    registry_set "${U}" "${rkind:-agent}" "${rmode:-claude}" 0
    [[ "$rmode" == codex ]] && codex_setup "${U}" "${H}"
    note "then: ${REPO_ROOT}/scripts/board-signer-ctl.sh activate ${U} activated"
    case "${rmode:-claude}" in
        claude) note "then the normal first-contact steps: install the CLI for ${U}, sudo -u ${U} -i claude (auth + trust), enable its remote-control unit: runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/\$(id -u ${U} 2>/dev/null || echo UID) systemctl --user enable --now ${RC_UNIT}" ;;
        ssh)    note "then the client SSHes in as ${U}; its last login is its liveness" ;;
        codex)  note "codex: CLI + unit installed above; log in and enable in the same sitting (notes above)" ;;
        *)      note "service: nothing else -- it is live as soon as its signer is active" ;;
    esac
fi

# -------------------------------------------------------- rehome inbound key --
if [[ -n "$REHOME_FROM" ]]; then
    F="$REHOME_FROM"; T="$REHOME_TO"; FH="/home/${F}"; TH="/home/${T}"
    step "rehome inbound key: ${F}'s current authorized key becomes ${T}'s (comment rewritten); ${F} gets its OWN generated public key as its single inbound key" "'Account kinds' (re-home)"
    note "why: ctdc-agent inherited the Cowork client's key at stage B; a dedicated Cowork account takes it over so the client lands as ${T}, and 'one key per account' holds for both"
    run "install -d -m 0700 -o ${T} -g ${T} ${TH}/.ssh"
    run "awk '{\$NF=\"${T}@corporatetraveldc-dispatch\"; print}' ${FH}/.ssh/authorized_keys > ${TH}/.ssh/authorized_keys && chown ${T}:${T} ${TH}/.ssh/authorized_keys && chmod 0600 ${TH}/.ssh/authorized_keys"
    run "[ -f ${FH}/.ssh/${F}_ed25519 ] || sudo -u ${F} ssh-keygen -q -t ed25519 -N '' -C '${F}@corporatetraveldc-dispatch' -f ${FH}/.ssh/${F}_ed25519"
    run "install -m 0600 -o ${F} -g ${F} ${FH}/.ssh/${F}_ed25519.pub ${FH}/.ssh/authorized_keys"
    note "${F}'s board signer is unchanged (it already signs with ${F}_ed25519); ${T} still needs its own signing key + registration from its --add-agent step"
    note "verify: scripts/agent-segmentation/verify.sh --user ${F}; --user ${T}; the Cowork client now authenticates as ${T}"
fi

# ------------------------------------------------------------ rename account --
# 2026-10-05 (operator): accounts follow ctdc-agent-<vendor>-<product>, so
# ctdc-agent -> ctdc-agent-anthropic-claude and ctdc-agent-cowork ->
# ctdc-agent-anthropic-cowork. A rename keeps the uid (file ownership, the
# Claude login, liveness history) and carries every NAME-keyed piece over:
# passwd/group, home, key filenames + comments, git author, registry line,
# skill grants, sudoers, remote-control session label, liveness state files,
# the Claude CLI's per-project state (workspace trust is keyed by the home
# path), the board signer (operator step, printed) and the pamphlet.
if [[ -n "$RENAME_FROM" ]]; then
    F="$RENAME_FROM"; T="$RENAME_TO"; FH="/home/${F}"; TH="/home/${T}"
    FU=$(id -u "$F" 2>/dev/null || echo UID)
    step "rename ${F} -> ${T}: stop everything running as ${F} (its sessions drop)" "'Account kinds' (rename)"
    run "loginctl disable-linger ${F} || true"
    run "loginctl terminate-user ${F} || true"
    run "systemctl stop user@${FU}.service || true"
    run "for i in \$(seq 1 20); do pgrep -u ${FU} >/dev/null || break; sleep 1; done; ! pgrep -u ${FU} >/dev/null || { echo 'processes still running as ${F}:'; ps -o pid,cmd -u ${FU}; false; }"

    step "rename ${F} -> ${T}: passwd, group, home (uid ${FU} unchanged)" "'Account kinds' (rename)"
    run "usermod -l ${T} ${F}"
    run "groupmod -n ${T} ${F}"
    run "usermod -d ${TH} -m ${T}"
    note "home moved by rename on the same filesystem: birth time (the liveness 'created' clock) and every file's owner (uid) are unchanged"

    step "rename ${F} -> ${T}: keys, git author, Claude CLI state" "'Attribution'"
    run "[ ! -f ${TH}/.ssh/${F}_ed25519 ] || { mv ${TH}/.ssh/${F}_ed25519 ${TH}/.ssh/${T}_ed25519 && mv ${TH}/.ssh/${F}_ed25519.pub ${TH}/.ssh/${T}_ed25519.pub; }"
    run "[ ! -f ${TH}/.ssh/${T}_ed25519.pub ] || sed -i -E 's/ ${F}@corporatetraveldc-dispatch\$/ ${T}@corporatetraveldc-dispatch/' ${TH}/.ssh/${T}_ed25519.pub"
    run "[ ! -f ${TH}/.ssh/authorized_keys ] || sed -i -E 's/ ${F}@corporatetraveldc-dispatch\$/ ${T}@corporatetraveldc-dispatch/' ${TH}/.ssh/authorized_keys"
    run "runuser -u ${T} -- git config --global user.name '${T}' && runuser -u ${T} -- git config --global user.email '${T}@corporatetraveldc-dispatch.invalid'"
    # exact-path forms only: "/home/ctdc-agent" is a prefix of "/home/ctdc-agent-cowork"
    run "for f in ${TH}/.claude.json ${TH}/.claude/settings.json; do [ -f \"\$f\" ] && runuser -u ${T} -- sed -i -e 's#\"${FH}\"#\"${TH}\"#g' -e 's#${FH}/#${TH}/#g' \"\$f\"; done; true"
    run "[ ! -d ${TH}/.claude/projects/-home-${F} ] || mv ${TH}/.claude/projects/-home-${F} ${TH}/.claude/projects/-home-${T}"
    # 2026-10-05: ~/.local/bin/claude is an ABSOLUTE symlink into the old home
    # (/home/F/.local/share/claude/versions/X); after the move it dangled and the
    # remote-control unit crash-looped "FileNotFoundError". Re-point every
    # symlink in the home that still targets /home/F/.
    run "find ${TH} -xdev -type l -lname '${FH}/*' -print0 | while IFS= read -r -d '' l; do t=\$(readlink \"\$l\"); ln -sfn \"${TH}/\${t#${FH}/}\" \"\$l\"; chown -h ${T}:${T} \"\$l\"; done"
    note "workspace trust and session history are keyed by the home path; carried over so the remote-control unit does not park on the trust dialog"

    step "rename ${F} -> ${T}: registry, skill grants, sudoers, liveness state" "'Account kinds' (rename)"
    run "[ ! -f ${REGISTRY} ] || sed -i -E 's/^${F} /${T} /' ${REGISTRY}"
    run "[ ! -f /etc/ctdc-skill-grants.conf ] || sed -i -E 's/^((grant|deny)[[:space:]]+)${F}([[:space:]]|\$)/\\1${T}\\3/' /etc/ctdc-skill-grants.conf"
    run "if [ -f /etc/sudoers.d/50-${F} ]; then sed -e 's/(${F})/(${T})/g' -e 's/as ${F};/as ${T};/' -e 's/user ${F} /user ${T} /' /etc/sudoers.d/50-${F} > /etc/sudoers.d/50-${T} && chmod 0440 /etc/sudoers.d/50-${T} && visudo -cf /etc/sudoers.d/50-${T} && rm -f /etc/sudoers.d/50-${F}; fi"
    run "for d in /var/lib/corporatetraveldc/team-liveness/lastlogin /var/lib/corporatetraveldc/team-liveness/inert /var/lib/ctdc-liveness/orders; do [ ! -e \$d/${F} ] || mv \$d/${F} \$d/${T}; done"

    step "rename ${F} -> ${T}: remote-control session label, user manager back up" "'Account kinds' (rename)"
    RCD="${TH}/.config/systemd/user/${RC_UNIT}.d"
    run "[ ! -f ${RCD}/name.conf ] || printf '%s\\n' '[Service]' 'ExecStart=' 'ExecStart=/usr/bin/python3 -c \"import pty,sys; pty.spawn(sys.argv[1:])\" %h/.local/bin/claude --remote-control \"($(echo ${T} | tr a-z A-Z)) Corporate Travel Dispatch Remote Control\"' > ${RCD}/name.conf"
    run "[ ! -f ${RCD}/name.conf ] || chown ${T}:${T} ${RCD}/name.conf"
    run "[ \"\$(awk -v n=${T} '\$1==n {print \$2}' ${REGISTRY} 2>/dev/null)\" = service ] || { loginctl enable-linger ${T}; systemctl start user@${FU}.service; }"
    note "2026-10-05: enable-linger on a just-renamed account wrote the linger file but logind did not start the manager (user@1001 stayed down) -- hence the explicit start"
    run "/usr/local/libexec/ctdc/skill-grants.sh apply --execute || true"
    note "the remote-control unit (if enabled) starts with the user manager; the mobile app shows the new session name -- the old one stays as an offline entry you can remove"

    step "rename ${F} -> ${T}: board signer + pamphlet (operator, NOT under sudo for the signer)" "'Attribution'"
    note "${REPO_ROOT}/scripts/board-signer-ctl.sh rename ${F} ${T}"
    note "sudo ${SELF_DIR}/render-onboarding.sh ${T} --install && ${SELF_DIR}/verify.sh --user ${T}"
fi

# ------------------------------------------------------- convert to service --
# 2026-10-05 (operator): Cowork lives in Anthropic's cloud and reaches the
# platform only over HTTPS (board + vault research with its own minted board
# token); it never SSHes in, so a login-mode-ssh account can never satisfy its
# liveness login factor and would go inert after the grace. A service identity
# never logs in: alive = signer active AND no token revocation AND no kill order.
if [[ -n "$TO_SERVICE" ]]; then
    U="$TO_SERVICE"; H="/home/${U}"; UU=$(id -u "$U" 2>/dev/null || echo UID)
    step "convert ${U} to a service identity" "'Account kinds' (service)"
    run "loginctl disable-linger ${U} || true"
    run "loginctl terminate-user ${U} || true"
    run "systemctl stop user@${UU}.service || true"
    run "usermod -s /usr/sbin/nologin ${U}"
    run "rm -f ${H}/.ssh/authorized_keys"
    registry_set "${U}" service none 0
    note "the signing key ${H}/.ssh/${U}_ed25519 and the board signer stay (identity anchor; liveness key factor). The client's credential is a minted board token labelled ${U} -- scripts/board-mint-nonce.py --label ${U} -- so the liveness switch's revoke-tokens covers it"
    note "then: sudo ${SELF_DIR}/render-onboarding.sh ${U} --install && ${SELF_DIR}/verify.sh --user ${U}"
    note "a CLOUD client cannot read its home: publish the pamphlet where it can -- ${SELF_DIR}/publish-pamphlet-to-vault.sh ${U}"
fi

# ------------------------------------------------------------ human account --
if [[ -n "$ADD_HUMAN" ]]; then
    U="$ADD_HUMAN"; H="/home/${U}"
    step "human account ${U}" "'Groups and accounts' (human)"
    run "getent passwd ${U} >/dev/null || useradd --create-home --home-dir ${H} --shell /bin/bash --groups ${DEV_GROUP},${OPS_GROUP} --comment 'team member, no sudo' ${U}"
    run "loginctl enable-linger ${U}"
    registry_set "${U}" human ssh 0
    note "no wheel, no sudo; root actions stay with the operator"

    step "ssh: ${U}'s OWN public key (never the operator's or the cowork key)" "'Adding a human teammate'"
    run "install -d -m 0700 -o ${U} -g ${U} ${H}/.ssh"
    if [[ -n "$SSH_PUBKEY_FILE" ]]; then
        run "install -m 0600 -o ${U} -g ${U} ${SSH_PUBKEY_FILE} ${H}/.ssh/authorized_keys"
    else
        note "no --ssh-pubkey-file given: create ${H}/.ssh/authorized_keys (0600, owner ${U}) from the teammate's key before they log in"
    fi

    step "human home ${H}: humans.slice + env" "'Resource isolation' (humans.slice)"
    run "install -d -m 0700 -o ${U} -g ${U} ${H}/.config/systemd/user"
    run "install -m 0644 -o ${U} -g ${U} ${HUMANS_SLICE_SRC} ${H}/.config/systemd/user/humans.slice"
    run "runuser -u ${U} -- env XDG_RUNTIME_DIR=/run/user/$(id -u ${U} 2>/dev/null || echo UID) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u ${U} 2>/dev/null || echo UID)/bus systemctl --user daemon-reload || true"
    run "printf '%s\n' 'export WITH_DISPATCH_ENV_FILES=${OPS_SECRETS}' '# interactive work lands in humans.slice (docs/AGENT_SEGMENTATION.md)' '[ -n \"\$PS1\" ] && [ -z \"\$CTDC_IN_SLICE\" ] && command -v systemd-run >/dev/null && exec env CTDC_IN_SLICE=1 systemd-run --user --quiet --scope --slice=humans.slice bash -l' > ${H}/.config/ctdc-env.sh && chown ${U}:${U} ${H}/.config/ctdc-env.sh"
    run "grep -q ctdc-env.sh ${H}/.bash_profile 2>/dev/null || printf '%s\n' '. ~/.config/ctdc-env.sh' >> ${H}/.bash_profile && chown ${U}:${U} ${H}/.bash_profile"
    note "the login shell re-execs itself inside humans.slice once (CTDC_IN_SLICE guard); runtime loosen: systemctl --user set-property --runtime humans.slice CPUQuota=300%"
    note "personal credentials, not shared ones: issue ${U} an API token (auth_tokens row, own tier) and a personal Nextcloud account/app password; the ops subset carries only NTFY_TOKEN + PG facts"
fi

echo
echo "verify afterwards: ${SELF_DIR}/verify.sh [--user NAME]    rollback: ${SELF_DIR}/rollback.sh --execute --i-am-the-operator --remove-agent NAME | --remove-human NAME | --remove-service NAME | --shared"
[[ "$MODE" == "dry-run" ]] && echo "(dry-run: nothing was executed)"
exit 0
