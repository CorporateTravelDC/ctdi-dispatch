# scripts/lib/gov-exec.sh -- sourced helper (a library in the repo, not an env
# file): run a Python heredoc against common.governance with the deployed
# database. Production (postgres): inside the running web container, i.e. the
# SIGNED image's code and its own DB credentials -- no password ever passes
# through this shell. Tests (DISPATCH_DB_BACKEND=sqlite with
# CTDC_ALLOW_SQLITE_FOR_TESTS=1, or GOV_EXEC_LOCAL=1): the checkout's src/
# directly. 2026-10-20 (platform SQLite retired, operator 2026-10-19): a sqlite
# backend WITHOUT the test flag refuses (rc 70) instead of running locally.
#   gov_py ARG... <<'PYEOF'   (sys.argv[1:] = ARG...)
# GOV_PASS_ENV="NAME ..." passes those variables into the container BY NAME
# (podman exec -e NAME): the values travel in the environment, never argv --
# for data that must stay off the process list (e.g. an invite batch).
GOV_WEB_CONTAINER="${GOV_WEB_CONTAINER:-systemd-corporatetraveldc-web}"
gov_py() {
  if [[ "${DISPATCH_DB_BACKEND:-}" == sqlite && "${CTDC_ALLOW_SQLITE_FOR_TESTS:-}" != 1 ]]; then
    echo "gov-exec: DISPATCH_DB_BACKEND=sqlite is retired (platform SQLite, 2026-10-19); test suite only" >&2
    return 70
  fi
  if [[ "${GOV_EXEC_LOCAL:-0}" == 1 || "${DISPATCH_DB_BACKEND:-}" == sqlite ]]; then
    PYTHONPATH="${REPO_ROOT}/src" python3 - "$@"
  else
    local -a envs=()
    local n
    for n in ${GOV_PASS_ENV:-}; do envs+=(-e "$n"); done
    podman exec -i "${envs[@]}" "$GOV_WEB_CONTAINER" python3 - "$@"
  fi
}
