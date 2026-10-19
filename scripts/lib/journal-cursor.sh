# scripts/lib/journal-cursor.sh -- read only the journal entries that are new
# since the previous run (sourced by the timer jobs that read the journal).
#
# Lean-out L2 / F4 (2026-10-19). swim-session-health ran
# `journalctl --user -u 'corporatetraveldc-ingest-*' -S <last run>` every
# 5 min: a glob unit match makes journalctl enumerate every unit name in every
# journal file, so each run memory-mapped 2.5-4.5 GB of the 3.8 GB user
# journal (~1 TB/day mapped, evicting Postgres's page cache). Readers now name
# their units EXACTLY and resume from a saved cursor, so journalctl seeks
# straight to the first unread entry.
#
# jc_read OUT CURSOR_FILE SINCE MAX_AGE_S [journalctl args...]
#   * CURSOR_FILE non-empty and last read <= MAX_AGE_S ago:
#       journalctl --cursor-file=CURSOR_FILE ARGS          (entries after it)
#   * otherwise (first run, or the job was off for a long time):
#       journalctl -S SINCE --show-cursor ARGS             (SINCE as before)
#     and the trailing "-- cursor: ..." line is moved from OUT into
#     CURSOR_FILE. journalctl refuses --since together with --cursor-file, so
#     the two modes are separate calls, never one.
#   CURSOR_FILE's mtime is the time of the last successful read (touched every
#   run, even when there was nothing new and journalctl left it alone).
#   OUT always exists afterwards; the return value is journalctl's.
#   The cursor is a journal position (boot id, seqnum), never printed.
#   JOURNALCTL (default journalctl) is a test hook.

jc_read() {
    local out="$1" cf="$2" since="$3" maxage="$4"; shift 4
    local jc="${JOURNALCTL:-journalctl}" now mt rc last tmp
    now=$(date +%s)
    : > "$out"
    if [[ -s "$cf" ]] && mt=$(stat -c %Y "$cf" 2>/dev/null) && (( now - mt <= maxage )); then
        "$jc" --no-pager --cursor-file="$cf" "$@" > "$out" 2>/dev/null
        rc=$?
        (( rc == 0 )) && touch "$cf" 2>/dev/null
        return "$rc"
    fi
    rm -f "$cf"
    tmp="${out}.jc"
    "$jc" --no-pager -S "$since" --show-cursor "$@" > "$tmp" 2>/dev/null
    rc=$?
    last=$(tail -n 1 "$tmp" 2>/dev/null)
    if [[ "$last" == "-- cursor: "* ]]; then
        ( umask 077; printf '%s\n' "${last#-- cursor: }" > "$cf" )
        head -n -1 "$tmp" > "$out"
    else
        cat "$tmp" > "$out"
    fi
    rm -f "$tmp"
    return "$rc"
}
