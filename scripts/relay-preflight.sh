#!/bin/bash
# scripts/relay-preflight.sh -- run EVERY check a relay will hit, in the relay's order, on the
# exact tree it will ship, BEFORE the relay is posted (operator 2026-10-15, after seven relays in a
# row failed on something a check could have caught).
#
#   relay-preflight.sh RELAY_FILE [--quick]
# Checks (stop at the first failure, print its fix):
#   1. relay-lint on the relay text                    (SIGPIPE, stdin reads, sudo on user units, ...)
#   2. drift gate, from the main tree                  (what the relay's step 0 runs)
#   3. dependency audit                                (what pre-push blocks on; 2026-10-22)
#   4. apt snapshot pin age                            (oldest ARG APT_SNAPSHOT <= 6 days; 2026-10-22)
#   5. test suite on the guard worktree                (full; --quick = tests touched by the staged diff)
#   6. public scrub verification on the guard's STAGED tree (registration-shaped ids, UUIDs, emails,
#      IPs, live secret values -- what push-public refuses on)
#   7. transcript leak scan                            (what pre-push / push-public refuse on)
#   8. heavy-lock queue (WARNING only, never fails)    (lock held / heavy timer due soon; 2026-10-23)
# Exit 0 only when all pass. Never prompts, never signs, never pushes, never changes live state.
set -uo pipefail
MAIN=/opt/corporatetraveldc/private/ctdi-dispatch-internal
G=/opt/corporatetraveldc/private/ctdi-dispatch-internal-guard
RELAY="${1:?usage: relay-preflight.sh RELAY_FILE [--quick]}"; QUICK="${2:-}"
fail() { echo "PREFLIGHT FAIL [$1]: $2"; exit 1; }
ok() { echo "  ok  $1"; }

python3 "$G/scripts/relay-lint.py" "$RELAY" >/tmp/.preflight-lint.$$ 2>&1 || { cat /tmp/.preflight-lint.$$; rm -f /tmp/.preflight-lint.$$; fail lint "fix the relay text"; }
rm -f /tmp/.preflight-lint.$$; ok "1 relay-lint"

( cd "$MAIN" && bash scripts/check-claude-md-drift.sh >/dev/null 2>&1 ) \
  || { ( cd "$MAIN" && bash scripts/check-claude-md-drift.sh 2>&1 | grep -E '^\[(DRIFT|FAIL)\]' ) || true; fail drift "reconcile, then re-run"; }
ok "2 drift gate"

# 2026-10-26 (operator: "catch this shit"): a batch may not ADD doc-claim misses (paths, units, timer
# schedules, API routes, enabled/disabled state) -- the guard's own checker on both trees, compared.
dc_misses() { python3 "$G/scripts/doc-claims-check.py" --claims-only "$1" 2>/dev/null | sed -n 's/^\[SUMMARY\].*misses=\([0-9]*\).*/\1/p'; }
# a unit whose file THIS batch adds is pending install (the operator installs it after the relay), not drift
dc_pending() { python3 "$G/scripts/doc-claims-check.py" --claims-only "$G" 2>/dev/null | sed -n 's/.*unit not installed: \(.*\)$/\1/p' | sort -u | while read -r u; do
    git -C "$G" diff --cached --name-only --diff-filter=A | grep -qE "(^|/)${u//./\\.}\$" && echo "$u"; done | wc -l; }
before=$(dc_misses "$MAIN"); after=$(( $(dc_misses "$G") - $(dc_pending) ))
[[ -n "$before" && -n "$after" ]] || fail doc-claims "doc-claims-check.py did not run"
(( after <= before )) || { python3 "$G/scripts/doc-claims-check.py" --claims-only "$G" 2>/dev/null | grep '^\[DRIFT\]' | sed -n '1,15p'; fail doc-claims "the batch adds $((after - before)) doc-claim miss(es) ($before -> $after): fix the docs it touches"; }
ok "2b doc claims: ${after} miss(es), none added (main ${before})"

cd "$G" || fail guard "no guard worktree"

# 2026-10-22 (operator): the dependency audit, invoked exactly as scripts/pre-push does, so a relay
# whose push would be refused fails here first. Exit 1 blocks (npm high/critical, fixable Python
# advisory); exit 3 (could not run, offline) warns like pre-push and continues.
if [[ -x "$G/scripts/dependency-audit.py" ]]; then
    "$G/scripts/dependency-audit.py" --repo "$G" >/tmp/.preflight-dep.$$ 2>&1
    dep_rc=$?
    if [[ $dep_rc -eq 1 ]]; then
        cat /tmp/.preflight-dep.$$; rm -f /tmp/.preflight-dep.$$
        fail deps "fix the vulnerable dependencies (pre-push would reject the push)"
    fi
    rm -f /tmp/.preflight-dep.$$
    if [[ $dep_rc -eq 3 ]]; then ok "3 dependency audit: WARNING could not run (offline?) -- pre-push allows this too"
    elif [[ $dep_rc -ne 0 ]]; then fail deps "dependency audit exited $dep_rc"
    else ok "3 dependency audit"; fi
else
    fail deps "scripts/dependency-audit.py missing or not executable"
fi

# 2026-10-22 (operator): the Debian snapshot pin (ARG APT_SNAPSHOT in the Containerfiles) expires
# around 7 days; on 2026-10-21 an expired pin broke a rollout build silently. Fail when the oldest
# pin is older than 6 days, so it is moved (pin-base-images.py --apt-snapshot now) before a rollout.
snap=$(python3 "$G/scripts/build/pin-base-images.py" --list 2>/dev/null | awk -F'\t' '$2=="APT_SNAPSHOT"{print $3}' | sort | sed -n '1p')
[[ "$snap" =~ ^([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z$ ]] \
  || fail apt-snapshot "no ARG APT_SNAPSHOT=YYYYMMDDTHHMMSSZ found (pin-base-images.py --list)"
snap_epoch=$(date -u -d "${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]} ${BASH_REMATCH[4]}:${BASH_REMATCH[5]}:${BASH_REMATCH[6]}" +%s) \
  || fail apt-snapshot "cannot parse APT_SNAPSHOT $snap"
snap_age_d=$(( ($(date -u +%s) - snap_epoch) / 86400 ))
(( $(date -u +%s) - snap_epoch <= 6 * 86400 )) \
  || fail apt-snapshot "oldest APT_SNAPSHOT $snap is ${snap_age_d} days old (expires ~7): python3 scripts/build/pin-base-images.py --apt-snapshot now, review, rebuild"
ok "4 apt snapshot pin $snap (${snap_age_d} days old)"

if [[ "$QUICK" == "--quick" ]]; then
    # --diff-filter=d: a batch that deletes or retires a test file must not hand pytest a missing path
    # ("no tests ran" = exit 5 = a false FAIL; seen 2026-10-20 on the freeze sweep)
    mapfile -t T < <(git diff --cached --name-only --diff-filter=d | grep -E '^tests/.*\.py$' | grep -vE '/(conftest|__init__)\.py$' || true)
    [[ ${#T[@]} -gt 0 ]] || T=(tests/scripts)
else
    T=(tests)
fi
out=$(PYTHONPATH=src timeout 1800 nice -n 10 python3 -m pytest -q -p no:cacheprovider -p no:warnings "${T[@]}" 2>&1 | tail -3)
echo "$out" | grep -qE "^[0-9]+ passed" && ! echo "$out" | grep -qE "failed|error" || { echo "$out"; fail tests "fix the failing tests"; }
ok "5 tests: $(echo "$out" | grep -E 'passed' | tail -1)"

tree=$(git write-tree) || fail scrub "cannot write the staged tree (stage with git add -A first)"
python3 scripts/scrub-public-tree.py "$tree" >/dev/null 2>/tmp/.preflight-scrub.$$ \
  || { grep -E "VERIFICATION|unrecognized|forbidden|live value|registration|LADD" /tmp/.preflight-scrub.$$; rm -f /tmp/.preflight-scrub.$$; fail scrub "fix or allowlist (exact values only) in the guard"; }
rm -f /tmp/.preflight-scrub.$$; ok "6 public scrub verification on the staged tree"

# 2026-10-20: through the dispatch env with --require-ladd (an unreadable LADD list fails the preflight).
nice -n 19 "$MAIN/scripts/with-dispatch-env.sh" python3 "$MAIN/scripts/leak-guard/transcript_leak_scan.py" scan --require-ladd \
  >/tmp/.preflight-leak.$$ 2>&1 \
  || { cat /tmp/.preflight-leak.$$; rm -f /tmp/.preflight-leak.$$; fail transcripts "redact on the box (transcript_leak_scan.py redact)"; }
rm -f /tmp/.preflight-leak.$$; ok "7 transcript leak scan"

# 2026-10-23 (operator): on 2026-10-22 a relay posted right after the "backup done" ping refused
# because the hourly stack-refresh tripwire took the heavy-work turn first. Non-blocking: the relay
# text may be fine; this only says whether NOW is a good time to run it.
if hl=$(bash "$G/scripts/heavy-lock-status.sh" "${PREFLIGHT_HEAVY_WINDOW_MIN:-30}" 2>&1 </dev/null); then
    ok "8 heavy-lock queue clear"
else
    echo "  WARN 8 heavy-lock queue busy (not a failure -- wait before running the relay):"
    echo "$hl" | sed 's/^/        /'
fi
echo "PREFLIGHT PASS -- the relay may be posted"
