#!/usr/bin/env bash
# One-off: normalize authorship and strip all Claude references from the
# private repo and the public mirror.
#
# Forces EVERY author and committer -- including GitHub <noreply@github.com>
# merge commits -- to a single identity, and removes Co-Authored-By: Claude
# and Claude-Session/session-URL lines from every commit message.
#
# Irreversible: every SHA changes in both repos. A bundle backup is written
# first; restore with `git clone <bundle> <dir>` if this goes wrong.
set -euo pipefail

# ── The one identity every commit is rewritten to. The GitHub ACCOUNT stays
# CorporateTravelDC (that is the username, set by the push credential, not by
# these fields); this is the contributor name/email stamped into each commit.
# GitHub links commits to the account via the email, so this address must stay
# on the CorporateTravelDC account's verified-email list or the commits show
# as unattributed.
IDENT_NAME="[operator LLC] LLC"
IDENT_EMAIL="developer@example.com"

PRIVATE="/opt/corporatetraveldc/private/ctdi-dispatch-internal"
PUBLIC="/opt/corporatetraveldc/public/ctdi-dispatch"
BACKUP_DIR="/var/lib/corporatetraveldc/git-rewrite-backup-$(date +%Y%m%d-%H%M%S)"

command -v git-filter-repo >/dev/null || { echo "XX git-filter-repo not found"; exit 2; }
mkdir -p "${BACKUP_DIR}"

rewrite() {
    local dir="$1" label="$2" remote_url="$3"
    echo "=== ${label}: ${dir} ==="
    cd "${dir}"

    # filter-repo refuses a dirty tree, and silently losing staged work to a
    # history rewrite would be unrecoverable. Fail loudly instead.
    if [[ -n "$(git status --porcelain)" ]]; then
        echo "XX ${label} has uncommitted changes -- commit or stash first, then re-run."
        git status --short
        exit 3
    fi

    echo "--- backup -> ${BACKUP_DIR}/${label}.bundle"
    git bundle create "${BACKUP_DIR}/${label}.bundle" --all >/dev/null

    local before_authors before_trailers
    before_authors="$(git log --format='%an <%ae>' | sort -u | wc -l)"
    before_trailers="$(git log --format=%B | grep -ci 'co-authored-by\|claude' || true)"
    echo "--- before: ${before_authors} distinct author identities, ${before_trailers} Claude/co-author lines"

    git filter-repo --force \
        --name-callback "return b'${IDENT_NAME}'" \
        --email-callback "return b'${IDENT_EMAIL}'" \
        --message-callback '
import re
m = message.decode("utf-8", "replace")
# Any Co-Authored-By naming Claude, in any model/version spelling.
m = re.sub(r"(?im)^\s*Co-Authored-By:\s*Claude.*$\n?", "", m)
# Session trailers and bare session URLs.
m = re.sub(r"(?im)^\s*Claude-Session:\s*.*$\n?", "", m)
m = re.sub(r"(?im)^.*claude\.ai/code/session.*$\n?", "", m)
# "Generated with ..." attribution lines, if any exist.
m = re.sub(r"(?im)^\s*.?.?\s*Generated with .*Claude.*$\n?", "", m)
# Collapse the blank run a removed trailer block leaves behind.
m = re.sub(r"\n{3,}", "\n\n", m)
return m.rstrip().encode("utf-8") + b"\n"
'

    echo "--- after:  $(git log --format='%an <%ae>' | sort -u | wc -l) distinct author identities, $(git log --format=%B | grep -ci 'co-authored-by\|claude' || true) Claude/co-author lines"
    git log --format='%an <%ae>' | sort | uniq -c

    # filter-repo removes remotes by design, so they are restored here rather
    # than left for a later "why is push failing".
    git remote remove origin 2>/dev/null || true
    git remote add origin "${remote_url}"
    echo "--- remote restored: $(git remote get-url origin)"
    echo
}

PRIV_URL="$(cd "${PRIVATE}" && git remote get-url origin)"
PUB_URL="$(cd "${PUBLIC}" && git remote get-url origin)"

rewrite "${PRIVATE}" "private" "${PRIV_URL}"
rewrite "${PUBLIC}"  "public"  "${PUB_URL}"

cat <<EOF
=== rewrite complete -- NOTHING PUSHED YET ===
Backups: ${BACKUP_DIR}

Review, then push (both must be --force; every SHA changed):

  cd ${PRIVATE} && git push --force origin main --tags
  cd ${PUBLIC}  && git push --force origin main --tags

The 2 signed tags in the private repo were rewritten and their signatures
are now invalid -- re-sign them if they are still meaningful.
EOF
