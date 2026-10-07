# scripts/lib/gov-exec.sh -- sourced helper (a library in the repo, not an env
# file): run a Python heredoc against common.governance with the deployed
# database. Production (postgres): inside the running web container, i.e. the
# SIGNED image's code and its own DB credentials -- no password ever passes
# through this shell. Dev/tests (DISPATCH_DB_BACKEND=sqlite, or
# GOV_EXEC_LOCAL=1): the checkout's src/ directly.
#   gov_py ARG... <<'PYEOF'   (sys.argv[1:] = ARG...)
# GOV_PASS_ENV="NAME ..." passes those variables into the container BY NAME
# (podman exec -e NAME): the values travel in the environment, never argv --
# for data that must stay off the process list (e.g. an invite batch).
GOV_WEB_CONTAINER="${GOV_WEB_CONTAINER:-systemd-corporatetraveldc-web}"
gov_py() {
  if [[ "${GOV_EXEC_LOCAL:-0}" == 1 || "${DISPATCH_DB_BACKEND:-}" == sqlite ]]; then
    PYTHONPATH="${REPO_ROOT}/src" python3 - "$@"
  else
    local -a envs=()
    local n
    for n in ${GOV_PASS_ENV:-}; do envs+=(-e "$n"); done
    podman exec -i "${envs[@]}" "$GOV_WEB_CONTAINER" python3 - "$@"
  fi
}
