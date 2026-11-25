#!/usr/bin/env bash
# scripts/backup/proton_session.sh -- 2026-11-25: the Proton Drive CLI session for the off-box sync, encrypted at rest.
#
# The official CLI keeps its session in PROTON_DRIVE_CACHE_DIR when PROTON_DRIVE_CREDENTIALS_STORE=unsafe_file (the
# desktop keychain does not exist headless). This script keeps that session ONLY as a systemd-creds --user blob
# (bound to this user and this machine's credential key; a copy of the blob elsewhere decrypts to nothing), and
# opens it into a fresh 0700 folder on /run/user (RAM) for one run. The CLI rewrites the session while it works
# (token refresh), so `save` re-encrypts it after every run; a run that never saves leaves the previous blob.
#
#   proton_session.sh open CRED DIR    decrypt CRED into DIR (created, must not exist)
#   proton_session.sh save CRED DIR    encrypt DIR's session files back into CRED (atomic), then wipe DIR
#   proton_session.sh wipe DIR         remove DIR without saving
# Nothing here prints a session value.
set -euo pipefail
NAME=ctdc-proton-session
die() { echo "proton_session: $*" >&2; exit 1; }
under_run_user() { [[ "$1" == "/run/user/$(id -u)/"* ]] || die "$1 must be under /run/user/$(id -u)/ (RAM)"; }

cmd="${1:-}"
case "$cmd" in
  open)
    cred="$2"; dir="$3"; under_run_user "$dir"
    [[ -f "$cred" ]] || die "no saved session at $cred (sign in with the push relay first)"
    [[ ! -e "$dir" ]] || die "$dir already exists"
    mkdir -m 0700 "$dir"
    systemd-creds --user decrypt --name="$NAME" "$cred" - | tar -x -C "$dir" -f - \
      || { rm -rf -- "$dir"; die "could not decrypt $cred"; }
    ;;
  save)
    cred="$2"; dir="$3"; under_run_user "$dir"
    [[ -d "$dir" ]] || die "$dir does not exist"
    mapfile -t files < <(cd "$dir" && find . -maxdepth 1 -type f -name '*.json' -printf '%f\n' | sort)
    printf '%s\n' "${files[@]}" | grep -qx 'auth-session.json' || die "no auth-session.json in $dir: not saving"
    install -d -m 0700 "$(dirname "$cred")"
    umask 077
    tar -c -C "$dir" -f - "${files[@]}" | systemd-creds --user encrypt --name="$NAME" - "${cred}.new"
    mv -f "${cred}.new" "$cred"
    rm -rf -- "$dir"
    ;;
  wipe)
    dir="$2"; under_run_user "$dir"; rm -rf -- "$dir"
    ;;
  *) echo "usage: $0 open|save CRED DIR | wipe DIR" >&2; exit 64 ;;
esac
