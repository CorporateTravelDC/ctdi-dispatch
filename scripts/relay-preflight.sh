#!/bin/bash
# scripts/relay-preflight.sh -- run EVERY check a relay will hit, in the relay's order, on the
# exact tree it will ship, BEFORE the relay is posted (operator 2026-10-15, after seven relays in a
# row failed on something a check could have caught).
#
#   relay-preflight.sh RELAY_FILE [--quick]
# Checks (stop at the first failure, print its fix):
#   1. relay-lint on the relay text                    (SIGPIPE, stdin reads, sudo on user units, ...)
#   2. drift gate, from the main tree                  (what the relay's step 0 runs)
#   3. test suite on the guard worktree                (full; --quick = tests touched by the staged diff)
#   4. public scrub verification on the guard's STAGED tree (registration-shaped ids, UUIDs, emails,
#      IPs, live secret values -- what push-public refuses on)
#   5. transcript leak scan                            (what pre-push / push-public refuse on)
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

cd "$G" || fail guard "no guard worktree"
if [[ "$QUICK" == "--quick" ]]; then
    mapfile -t T < <(git diff --cached --name-only | grep -E '^tests/.*\.py$' || true)
    [[ ${#T[@]} -gt 0 ]] || T=(tests/scripts)
else
    T=(tests)
fi
out=$(PYTHONPATH=src timeout 1800 nice -n 10 python3 -m pytest -q -p no:cacheprovider -p no:warnings "${T[@]}" 2>&1 | tail -3)
echo "$out" | grep -qE "^[0-9]+ passed" && ! echo "$out" | grep -qE "failed|error" || { echo "$out"; fail tests "fix the failing tests"; }
ok "3 tests: $(echo "$out" | grep -E 'passed' | tail -1)"

tree=$(git write-tree) || fail scrub "cannot write the staged tree (stage with git add -A first)"
python3 scripts/scrub-public-tree.py "$tree" >/dev/null 2>/tmp/.preflight-scrub.$$ \
  || { grep -E "VERIFICATION|unrecognized|forbidden|live value|registration|LADD" /tmp/.preflight-scrub.$$; rm -f /tmp/.preflight-scrub.$$; fail scrub "fix or allowlist (exact values only) in the guard"; }
rm -f /tmp/.preflight-scrub.$$; ok "4 public scrub verification on the staged tree"

python3 "$MAIN/scripts/leak-guard/transcript_leak_scan.py" scan >/tmp/.preflight-leak.$$ 2>&1 \
  || { cat /tmp/.preflight-leak.$$; rm -f /tmp/.preflight-leak.$$; fail transcripts "redact on the box (transcript_leak_scan.py redact)"; }
rm -f /tmp/.preflight-leak.$$; ok "5 transcript leak scan"
echo "PREFLIGHT PASS -- the relay may be posted"
