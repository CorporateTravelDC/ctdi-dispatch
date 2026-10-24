# scripts/lib/sweep_alerts.sh -- sourced by scripts/scheduled-integrity-sweep.sh
#
# 2026-10-23 (operator): alert de-duplication for the integrity sweep. On
# 2026-10-21/22 the sweep paged an identical p5 "BUILD PROVENANCE FAILED" /
# "INTEGRITY SWEEP FAILED" every 15 min for hours (26, later ~13 identical
# pages) with only "check sweep.log" in the body. Now:
#   - the body names the failing check and what failed (image names + reason,
#     file names) -- names only, never a value;
#   - a failure FINGERPRINT (title + body) pages once at its own priority,
#     then at most one reminder every SWEEP_REMIND_SECS (default 2 h) at p4
#     while it persists; a changed fingerprint is a new failure and pages again;
#   - when the check passes again, one "CLEARED" p3 and the state is removed.
# State: one file per alert title under ${SWEEP_ALERT_DIR} (the sweep sets it
# inside its own state dir). The caller provides ntfy_post TITLE MSG PRIORITY
# (the raw sender) and log LEVEL MSG.

SWEEP_REMIND_SECS="${SWEEP_REMIND_SECS:-7200}"

_sweep_now() { echo "${SWEEP_NOW_EPOCH:-$(date +%s)}"; }

_sweep_alert_file() {
    local slug
    slug="$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '_' | tr 'A-Z' 'a-z')"
    echo "${SWEEP_ALERT_DIR}/${slug}.state"
}

_sweep_fmt_time() { TZ=America/New_York date -d "@$1" '+%Y-%m-%d %H:%M %Z' 2>/dev/null || echo "$1"; }

# ntfy_send TITLE MSG [PRIORITY] -- de-duplicated failure alert (see header).
ntfy_send() {
    local title="$1" msg="$2" priority="${3:-2}"
    local f now fp prev_fp="" first="" last="" count=0 k v remind_p
    mkdir -p "${SWEEP_ALERT_DIR}" 2>/dev/null
    f="$(_sweep_alert_file "${title}")"
    now="$(_sweep_now)"
    fp="$(printf '%s\n%s' "${title}" "${msg}" | sha256sum | cut -c1-16)"
    if [[ -f "${f}" ]]; then
        while IFS='=' read -r k v; do
            case "${k}" in
                fp) prev_fp="${v}" ;; first) first="${v}" ;;
                last) last="${v}" ;; count) count="${v}" ;;
            esac
        done < "${f}"
    fi
    if [[ "${prev_fp}" != "${fp}" ]]; then
        ntfy_post "${title}" "${msg}" "${priority}"
        printf 'fp=%s\nfirst=%s\nlast=%s\ncount=1\npriority=%s\ntitle=%s\n' \
            "${fp}" "${now}" "${now}" "${priority}" "${title}" > "${f}"
        return 0
    fi
    count=$((count + 1))
    if (( now - ${last:-0} >= SWEEP_REMIND_SECS )); then
        remind_p=4; (( priority < 4 )) && remind_p="${priority}"
        ntfy_post "${title} (still failing)" \
            "Still failing since $(_sweep_fmt_time "${first}") (${count} consecutive sweeps). ${msg}" \
            "${remind_p}"
        last="${now}"
    else
        log "info" "alert suppressed (repeat of fingerprint ${fp}, ${count} runs): ${title}"
    fi
    printf 'fp=%s\nfirst=%s\nlast=%s\ncount=%s\npriority=%s\ntitle=%s\n' \
        "${fp}" "${first}" "${last}" "${count}" "${priority}" "${title}" > "${f}"
}

# ntfy_clear TITLE -- the check passed; one p3 if TITLE was alerting.
ntfy_clear() {
    local title="$1" f first="" count=0 k v
    f="$(_sweep_alert_file "${title}")"
    [[ -f "${f}" ]] || return 0
    while IFS='=' read -r k v; do
        case "${k}" in first) first="${v}" ;; count) count="${v}" ;; esac
    done < "${f}"
    ntfy_post "CLEARED: ${title}" \
        "Check passes again (was failing since $(_sweep_fmt_time "${first:-0}"), ${count} sweep(s))." 3
    rm -f "${f}"
}

# sweep_prov_summary -- stdin: `provenance.py verify` text output (and
# build-policy.py --quiet output). stdout: one line per failing image,
# "<image>: <reason>[, <reason>]" (names + reason classes only, sorted), plus
# "build policy: <check>" lines for failing policy checks.
sweep_prov_summary() {
    awk '
    function reason(c) {
        if (c == "receipt") return "no receipt"
        if (c == "image") return "image not found"
        if (c == "current source") return "source behind"
        if (c == "source tree") return "built from an uncommitted tree"
        if (c == "source") return "source commit not found"
        if (c == "image id" || c == "sbom digest" || c == "sbom vs artifact" || c == "sbom" ||
            c == "signed manifest" || c == "build input" || c == "build inputs") return "digest mismatch"
        if (c == "commit signature") return "bad signature"
        if (c == "base images") return "unpinned base image"
        return c
    }
    /^build-policy:/ { next }
    /^[A-Z]/ {
        top = $1
        img = ""; pol = ""
        if (top == "FAILED") {
            # provenance rows are "FAILED     <image> [...]" (10-wide status);
            # build-policy rows are "FAILED          <check name>" (15-wide)
            rest = $0; sub(/^FAILED +/, "", rest)
            if (rest ~ /^localhost\// || rest ~ /^sha256:/ || rest ~ /\[/) { split(rest, a, " "); img = a[1] }
            else { pol = rest; out["build policy: " pol] = 1 }
        }
        next
    }
    /^    FAILED/ && img != "" {
        c = substr($0, 20, 18); gsub(/^ +| +$/, "", c)
        r = reason(c)
        if (!((img, r) in seen)) { seen[img, r] = 1; rs[img] = (img in rs) ? rs[img] ", " r : r }
    }
    END {
        for (i in rs) out[i ": " rs[i]] = 1
        for (o in out) print o
    }' | LC_ALL=C sort
}

# sweep_manifest_summary -- stdin: verify-manifest.sh output. stdout: a short
# reason + up to 5 file names (paths only).
sweep_manifest_summary() {
    local in files n
    in="$(cat)"
    if grep -q 'SIGNATURE INVALID' <<<"${in}"; then echo "signature invalid"; return; fi
    if grep -q 'SIGNING KEY NOT PINNED' <<<"${in}"; then echo "signing key not pinned"; return; fi
    if grep -q 'missing .* -- cannot verify' <<<"${in}"; then echo "manifest or signature file missing"; return; fi
    if grep -q 'NOT covered by the signed manifest' <<<"${in}"; then
        files="$(grep '^  ' <<<"${in}" | sed 's/^ *//' | LC_ALL=C sort)"
        n="$(grep -c . <<<"${files}")"
        echo "${n} file(s) not covered by the signed manifest: $(head -5 <<<"${files}" | paste -sd' ')$( (( n > 5 )) && echo ' ...')"
        return
    fi
    files="$(grep -E ': FAILED( open or read)?$' <<<"${in}" | sed -E 's/: FAILED( open or read)?$//' | LC_ALL=C sort)"
    if [[ -n "${files}" ]]; then
        n="$(grep -c . <<<"${files}")"
        echo "${n} file(s) do not match the signed manifest: $(head -5 <<<"${files}" | paste -sd' ')$( (( n > 5 )) && echo ' ...')"
        return
    fi
    echo "verification failed (see sweep.log)"
}
