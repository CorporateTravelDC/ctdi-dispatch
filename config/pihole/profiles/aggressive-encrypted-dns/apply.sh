#!/usr/bin/env bash
# config/pihole/profiles/aggressive-encrypted-dns/apply.sh -- OPT-IN Pi-hole
# profile: HaGeZi Encrypted DNS Bypass adlist + dnsmasq `filter-rr=HTTPS`.
# A more aggressive measure, NOT applied by default; see README.md.
#
# 2026-10-24 (operator 2026-10-23). Root only. Modes:
#   (none) / --dry-run   print exactly what would change, change nothing
#   --apply              add the adlist row if absent (then `pihole -g`), merge
#                        filter-rr=HTTPS into misc.dnsmasq_lines if missing
#                        (then restart pihole-FTL)
#   --revert             remove only this adlist row / only that one line
#   --revert --dry-run   plan the revert
# Idempotent and minimal: every existing dnsmasq line is kept, `pihole -g`
# and the FTL restart run only when something actually changed, no other
# Pi-hole setting is read or written. Every command is echoed; dry-run
# prefixes the ones it would run with "would run:".
#
# Test hooks (tests/scripts/test_pihole_aggressive_profile.py): PATH stubs
# for pihole / pihole-FTL / sqlite3 / systemctl / dig, PIHOLE_GRAVITY_DB, and
# PIHOLE_PROFILE_TEST_ALLOW_NONROOT=1, which is honoured ONLY in dry-run.
set -euo pipefail

ADLIST_URL='https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/doh.txt'
ADLIST_COMMENT='Encrypted DNS bypass (DoH/DoT/DoQ) -- aggressive profile'
DNSMASQ_LINE='filter-rr=HTTPS'
CFG_KEY='misc.dnsmasq_lines'
GRAVITY_DB="${PIHOLE_GRAVITY_DB:-/etc/pihole/gravity.db}"

usage() {
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
}


# 2026-10-24 (operator): aggressive is the DEFAULT on our deployment and an OPT-IN toggle for anyone deploying the
# public repo. One setting, CTDC_AGGRESSIVE_PRIVACY, in the deployment's dispatch.env (the live /etc copy is
# authoritative; parsed, never sourced). Ours = 1; config/dispatch.env.example ships 0. A command-line flag
# overrides it either way. The environment variable of the same name overrides the file (tests).
ENV_FILE=${CTDC_ENV_FILE:-/etc/corporatetraveldc/dispatch.env}
aggressive_setting() {
    local v="${CTDC_AGGRESSIVE_PRIVACY:-}"
    if [[ -z "$v" && -r "$ENV_FILE" ]]; then v=$(grep -m1 '^CTDC_AGGRESSIVE_PRIVACY=' "$ENV_FILE" | cut -d= -f2-); fi
    [[ "$v" == 1 || "$v" == true || "$v" == yes ]] && echo 1 || echo 0
}
ACTION=apply
DRY=1
FORCE=0
explicit=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --apply)   ACTION=apply;  explicit=1 ;;
        --revert)  ACTION=revert; explicit=1 ;;
        --force-aggressive) FORCE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $arg" >&2; usage >&2; exit 2 ;;
    esac
done
# --apply / --revert without --dry-run means do it; --dry-run anywhere wins.
if [[ $explicit -eq 1 ]]; then
    for arg in "$@"; do [[ $arg == --dry-run ]] && DRY=1 && break; DRY=0; done
fi

if [[ ${EUID} -ne 0 ]]; then
    if [[ $DRY -eq 1 && ${PIHOLE_PROFILE_TEST_ALLOW_NONROOT:-0} == 1 ]]; then
        echo "note: non-root dry-run permitted by PIHOLE_PROFILE_TEST_ALLOW_NONROOT=1 (tests only)"
    else
        echo "refusing: apply.sh must run as root (sudo bash $0 ${*:-})" >&2
        exit 1
    fi
fi

# The whole profile is the aggressive measure: applying it needs the deployment setting or an explicit override.
# --revert is always allowed.
if [[ $ACTION == apply && $DRY -eq 0 && $(aggressive_setting) != 1 && $FORCE -ne 1 ]]; then
    echo "not applying: this deployment has CTDC_AGGRESSIVE_PRIVACY off (opt-in). Set CTDC_AGGRESSIVE_PRIVACY=1 in $ENV_FILE," >&2
    echo "or pass --force-aggressive. Showing the plan only." >&2
    DRY=1
fi

mode_label="DRY-RUN ($ACTION plan)"; [[ $DRY -eq 1 ]] || mode_label="$ACTION"
echo "== aggressive-encrypted-dns profile: $mode_label =="
echo "   gravity db: $GRAVITY_DB"

# sql_lit: a SQL string literal with single quotes doubled -- the only
# values that reach sqlite3 are the two constants above, but never trust a
# bare interpolation into SQL.
sql_lit() { local s=${1//\'/\'\'}; printf "'%s'" "$s"; }

# run CMD...: echo, then execute unless dry-run.
run() {
    if [[ $DRY -eq 1 ]]; then
        echo "   would run: $*"
    else
        echo "+ $*"
        "$@"
    fi
}
# read CMD...: echo and execute in every mode (read-only commands).
read_cmd() { echo "+ $*" >&2; "$@"; }

# --- 1. adlist row ----------------------------------------------------------
lit_url=$(sql_lit "$ADLIST_URL")
lit_comment=$(sql_lit "$ADLIST_COMMENT")
count=$(read_cmd sqlite3 "$GRAVITY_DB" "SELECT count(*) FROM adlist WHERE address = $lit_url AND type = 0;" | tr -d '[:space:]')
count=${count:-0}
adlist_changed=0
case "$ACTION" in
    apply)
        if [[ $count -eq 0 ]]; then
            run sqlite3 "$GRAVITY_DB" "INSERT INTO adlist (address, enabled, comment, type) VALUES ($lit_url, 1, $lit_comment, 0);"
            adlist_changed=1
        else
            echo "   adlist already present ($count row) -- no change"
        fi ;;
    revert)
        if [[ $count -gt 0 ]]; then
            run sqlite3 "$GRAVITY_DB" "DELETE FROM adlist WHERE address = $lit_url AND type = 0;"
            adlist_changed=1
        else
            echo "   adlist not present -- nothing to remove"
        fi ;;
esac
if [[ $adlist_changed -eq 1 ]]; then
    run pihole -g
else
    echo "   pihole -g not needed"
fi

# --- 2. misc.dnsmasq_lines ---------------------------------------------------
# pihole-FTL prints the array CLI-style, "[ a, b ]" (unquoted, "[]" when
# empty, possibly ANSI-coloured when non-default); the setter takes JSON.
# parse_lines accepts both shapes and yields one line per element.
parse_lines() {
    sed -e 's/\x1b\[[0-9;]*m//g' \
        | tr -d '\n' \
        | sed -e 's/^[[:space:]]*\[[[:space:]]*//' -e 's/[[:space:]]*\][[:space:]]*$//' \
              -e 's/","/"\n"/g' -e 's/, /\n/g' \
        | sed -e 's/^[[:space:]]*"\{0,1\}//' -e 's/"\{0,1\}[[:space:]]*$//' -e '/^$/d'
}
to_json() {
    local out='[' first=1 item esc
    for item in "$@"; do
        esc=${item//\\/\\\\}; esc=${esc//\"/\\\"}
        if [[ $first -eq 1 ]]; then first=0; else out+=','; fi
        out+="\"$esc\""
    done
    printf '%s]' "$out"
}

raw=$(read_cmd pihole-FTL --config "$CFG_KEY")
mapfile -t CUR < <(printf '%s\n' "$raw" | parse_lines)
echo "   current $CFG_KEY: $(to_json ${CUR[@]+"${CUR[@]}"})"
NEW=()
present=0
for line in ${CUR[@]+"${CUR[@]}"}; do
    if [[ $line == "$DNSMASQ_LINE" ]]; then
        present=1
        [[ $ACTION == revert ]] && continue
    fi
    NEW+=("$line")
done
lines_changed=0
case "$ACTION" in
    apply)
        if [[ $present -eq 0 ]]; then NEW+=("$DNSMASQ_LINE"); lines_changed=1
        else echo "   $DNSMASQ_LINE already present -- no change"; fi ;;
    revert)
        if [[ $present -eq 1 ]]; then lines_changed=1
        else echo "   $DNSMASQ_LINE not present -- nothing to remove"; fi ;;
esac
if [[ $lines_changed -eq 1 ]]; then
    new_json=$(to_json ${NEW[@]+"${NEW[@]}"})
    echo "   merged $CFG_KEY: $new_json"
    run pihole-FTL --config "$CFG_KEY" "$new_json"
    run systemctl restart pihole-FTL
    [[ $DRY -eq 1 ]] || sleep 3   # 2026-10-24: FTL needs ~2 s before it answers again
else
    echo "   pihole-FTL restart not needed"
fi

# --- 3. verify --------------------------------------------------------------
a_ans=$(read_cmd dig +short @127.0.0.1 dns.google | tr '\n' ' ' | sed 's/ *$//')
h_ans=$(read_cmd dig +short @127.0.0.1 cloudflare-ech.com HTTPS | tr '\n' ' ' | sed 's/ *$//')
ok=1
if [[ $ACTION == apply ]]; then
    if [[ " $a_ans " == *" 0.0.0.0 "* ]]; then echo "   CHECK dns.google -> ${a_ans}: PASS"; else echo "   CHECK dns.google -> ${a_ans:-<empty>}: FAIL (expected 0.0.0.0)"; ok=0; fi
    if [[ -z $h_ans ]]; then echo "   CHECK cloudflare-ech.com HTTPS -> <empty>: PASS"; else echo "   CHECK cloudflare-ech.com HTTPS -> ${h_ans:0:60}: FAIL (expected empty)"; ok=0; fi
else
    if [[ -n $a_ans && " $a_ans " != *" 0.0.0.0 "* ]]; then echo "   CHECK dns.google -> ${a_ans}: PASS (resolves again)"; else echo "   CHECK dns.google -> ${a_ans:-<empty>}: FAIL (expected a real address after revert)"; ok=0; fi
    if [[ -n $h_ans ]]; then echo "   CHECK cloudflare-ech.com HTTPS -> record present: PASS"; else echo "   CHECK cloudflare-ech.com HTTPS -> <empty>: FAIL (expected a record after revert)"; ok=0; fi
fi
if [[ $ok -eq 1 ]]; then
    echo "RESULT: PASS"
elif [[ $DRY -eq 1 ]]; then
    echo "RESULT: FAIL (dry-run -- this is the current state, not an error; nothing was changed)"
else
    echo "RESULT: FAIL"
    exit 1
fi
