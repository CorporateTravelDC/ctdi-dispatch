# scripts/lib/secret_input.sh -- read a secret from the terminal and refuse any
# control or escape character (2026-10-19, operator, Gate-1b).
#
# Why: a value pasted from a phone can carry an ESC sequence, a CR or a TAB that
# lands in an env file or a secret file and breaks the consumer (or smuggles a
# terminal escape into the operator's screen when a log shows it). Every relay
# hand step that captures a secret either uses secret_read, or runs secret_check
# on the variable before writing it (scripts/relay-lint.py enforces one or the
# other).
#
# Usage (source it; nothing here prints a value):
#   source scripts/lib/secret_input.sh
#   secret_read VAR 'Prompt: '        # read -rs from /dev/tty, then secret_check
#   read -rs -p 'Prompt: ' VAR </dev/tty; secret_check VAR
# Both return non-zero on a refused value (the variable is emptied), so a relay
# under `set -e` stops there.
# SECRET_INPUT_TTY overrides /dev/tty (tests only).

secret_check() {
    local __sc_name="$1"
    local LC_ALL=C
    if [[ ! "$__sc_name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
        printf '%s\n' "secret_input: bad variable name" >&2
        return 2
    fi
    case "${!__sc_name-}" in
        *[[:cntrl:]]*)
            printf '%s\n' "secret_input: ${__sc_name} contains a control or escape character (ESC, CR, TAB, any byte < 0x20 or 0x7f) -- refused, nothing written" >&2
            printf -v "$__sc_name" '%s' ''
            return 1 ;;
    esac
    return 0
}

secret_read() {
    local __sr_name="$1" __sr_prompt="${2:-Secret: }" __sr_val=""
    if [[ ! "$__sr_name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
        printf '%s\n' "secret_input: bad variable name" >&2
        return 2
    fi
    IFS= read -rs -p "$__sr_prompt" __sr_val <"${SECRET_INPUT_TTY:-/dev/tty}" || { printf '\n' >&2; return 1; }
    printf '\n' >&2
    printf -v "$__sr_name" '%s' "$__sr_val"
    __sr_val=""
    secret_check "$__sr_name"
}
