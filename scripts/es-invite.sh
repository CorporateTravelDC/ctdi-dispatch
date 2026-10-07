#!/usr/bin/env bash
# scripts/es-invite.sh -- publisher CLI for Executive Standard reader access
# (common/es_invites.py; the same actions are on the phone console at /console).
#
#   es-invite.sh invite EMAIL [--label L] [--days N] [--devices N] [--source S] [--campaign C]
#   es-invite.sh invite --label L [...]            invite without an email (a named guest)
#                                                  permanent unless --days; prints the link ONCE
#   es-invite.sh invite --batch FILE [--days N] [--devices N] [--campaign C]
#                                                  one invite per 'email[,name]' line (# comments ok);
#                                                  nothing issued if any line is bad; repeats and live
#                                                  invites skipped; links go to ~/es-invites-<UTC>.csv (0600)
#   es-invite.sh reissue GRANT                     new link (old one dies, devices stay signed in)
#   es-invite.sh promo LABEL --uses N [--code-days N<=30] [--access-days N<=30] [--campaign C]
#                                                  prints the code ONCE (ES-XXXX-XXXX-XX + a link)
#   es-invite.sh list [--all]                      readers (grants)
#   es-invite.sh promos [--all]
#   es-invite.sh revoke GRANT|EMAIL [reason]       ends the link AND every signed-in device
#   es-invite.sh sign-out GRANT|EMAIL              ends devices only (a permanent link signs in again)
#   es-invite.sh revoke-promo PROMO [--readers]    stop the code (--readers: end everyone it let in)
#   es-invite.sh freeze | thaw                     pause / resume ALL new sign-ins
#   es-invite.sh kill-all                          freeze + sign out every device (links survive)
#   es-invite.sh retire-legacy                     revoke the pre-invite ?token grants (exec_standard_tokens)
#   es-invite.sh status                            summary + recent activity
#
# Runs inside the web container (scripts/lib/gov-exec.sh): the operator's
# rootless podman, so no agent account can issue or revoke reader access.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "${REPO_ROOT}/scripts/lib/gov-exec.sh"
usage() { sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }
CMD="${1:-}"; shift || true
[[ -n "$CMD" ]] || usage
ACTOR="${SUDO_USER:-${USER:-operator}}"
if [[ "$CMD" == invite && " $* " == *" --batch "* ]]; then
  # the list travels in the environment (never argv); links go to a 0600 file, not the screen
  args=(); file=""
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == --batch ]]; then file="${2:-}"; shift 2 || true; else args+=("$1"); shift; fi
  done
  [[ -r "$file" ]] || { echo "refused: cannot read batch file '$file'" >&2; exit 64; }
  ES_BATCH="$(cat -- "$file")"; export ES_BATCH
  # unique per run (mktemp, 0600): a second run in the same second can never overwrite or delete this one
  OUT="$(umask 077; mktemp "${HOME}/es-invites-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.csv")"
  ( umask 077; GOV_PASS_ENV=ES_BATCH gov_py "$ACTOR" batch "${args[@]}" >"$OUT" <<'PYEOF'
import argparse, csv, os, sys
from common import es_invites as es
actor, rest = sys.argv[1], sys.argv[3:]
ap = argparse.ArgumentParser(prog="es-invite.sh invite --batch")
ap.add_argument("--days", type=float); ap.add_argument("--devices", type=int, default=es.INVITE_DEFAULT_DEVICES)
ap.add_argument("--source", default="direct"); ap.add_argument("--campaign")
a = ap.parse_args(rest)
try:
    r = es.invite_batch(actor, os.environ.get("ES_BATCH", ""), source=a.source, campaign=a.campaign, days=a.days,
                        max_devices=a.devices)
except es.InviteError as e:
    sys.exit(f"refused: {e.detail}")
w = csv.writer(sys.stdout)
w.writerow(["email", "name", "grant", "link", "expires"])
for i in r["issued"]:
    w.writerow([i["email"], i["name"], i["grant"], i["url"], i["expires_at"] or "permanent"])
print(f"issued {len(r['issued'])} invite(s); skipped {len(r['skipped'])}", file=sys.stderr)
for k in r["skipped"]:
    print(f"  skipped {k['email']}: {k['why']}", file=sys.stderr)
PYEOF
  ) || { rm -f "$OUT"; exit 1; }
  unset ES_BATCH
  echo "links (shown nowhere else): $OUT -- send each reader their own line, then delete the file"
  exit 0
fi
gov_py "$ACTOR" "$CMD" "$@" <<'PYEOF'
import argparse, sys, time
from common import db, es_invites as es

actor, cmd, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
when = lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"
ap = argparse.ArgumentParser(prog=f"es-invite.sh {cmd}")
try:
    if cmd == "invite":
        ap.add_argument("email", nargs="?"); ap.add_argument("--label"); ap.add_argument("--days", type=float)
        ap.add_argument("--devices", type=int, default=es.INVITE_DEFAULT_DEVICES)
        ap.add_argument("--source", default="direct"); ap.add_argument("--campaign")
        a = ap.parse_args(rest)
        r = es.invite_issue(actor, a.email, a.label, source=a.source, campaign=a.campaign, days=a.days,
                            max_devices=a.devices)
        print(f"invited {a.email or a.label} as {r['grant']} -- "
              f"{'expires ' + when(r['expires_at']) if r['expires_at'] else 'permanent until you revoke it'}")
        print(f"link (shown once): {r['url']}")
    elif cmd == "reissue":
        ap.add_argument("grant"); a = ap.parse_args(rest)
        r = es.invite_reissue(actor, a.grant)
        print(f"new link for {r['grant']} (the old one no longer works): {r['url']}")
    elif cmd == "promo":
        ap.add_argument("label"); ap.add_argument("--uses", type=int, required=True)
        ap.add_argument("--code-days", type=float, default=es.PROMO_DEFAULT_CODE_DAYS)
        ap.add_argument("--access-days", type=int, default=es.PROMO_DEFAULT_ACCESS_DAYS)
        ap.add_argument("--campaign"); a = ap.parse_args(rest)
        r = es.promo_create(actor, a.label, uses=a.uses, code_days=a.code_days, access_days=a.access_days,
                            campaign=a.campaign)
        print(f"promo {r['promo']} ({a.label}): {a.uses} use(s), code works until {when(r['expires_at'])}, "
              f"each use = {a.access_days} day(s) of access")
        print(f"code (shown once): {r['code']}\nlink:              {r['url']}")
    elif cmd == "list":
        ap.add_argument("--all", action="store_true"); a = ap.parse_args(rest)
        for g in es.grants(include_revoked=a.all):
            who = g["email"] or g["label"] or "-"
            print(f"{g['id']:<13} {g['kind']:<6} {g['status']:<9} {who[:30]:<30} dev {g['devices']}/{g['max_devices']}"
                  f" claims {g['claims']:<3} seen {when(g['last_seen'])}  exp {when(g['expires_at']) if g['expires_at'] else 'never'}")
    elif cmd == "promos":
        ap.add_argument("--all", action="store_true"); a = ap.parse_args(rest)
        for p in es.promos(include_revoked=a.all):
            print(f"{p['id']:<11} {p['status']:<8} {(p['label'] or '')[:28]:<28} {p['uses']}/{p['max_uses']} used"
                  f"  code until {when(p['expires_at'])}  access {p['access_days']}d")
    elif cmd == "revoke":
        ap.add_argument("ref"); ap.add_argument("reason", nargs="?", default="revoked by publisher"); a = ap.parse_args(rest)
        r = es.revoke_grant(actor, a.ref, a.reason)
        print(f"revoked {r['grant']}; {r['devices_signed_out']} device(s) signed out")
    elif cmd == "sign-out":
        ap.add_argument("ref"); a = ap.parse_args(rest)
        print(f"{es.sign_out(actor, a.ref)} device(s) signed out")
    elif cmd == "revoke-promo":
        ap.add_argument("promo"); ap.add_argument("--readers", action="store_true"); a = ap.parse_args(rest)
        r = es.revoke_promo(actor, a.promo, readers=a.readers)
        print(f"promo {r['promo']} stopped; {r['readers_ended']} reader(s) ended")
    elif cmd in ("freeze", "thaw"):
        getattr(es, cmd)(actor); print("new sign-ins " + ("PAUSED" if cmd == "freeze" else "resumed"))
    elif cmd == "kill-all":
        print(f"FROZEN; {es.kill_all(actor)} device(s) signed out. Links survive: thaw to let readers back in.")
    elif cmd == "retire-legacy":
        with db.conn() as c:
            n = c.execute("UPDATE exec_standard_tokens SET status = 'revoked' WHERE status = 'active'").rowcount
        es._event(actor, "retire-legacy", "", f"{n} pre-invite token(s) revoked")
        print(f"{n} pre-invite token(s) revoked (their es_auth cookies stop working now)")
    elif cmd == "status":
        s = es.summary()
        print(f"{'FROZEN -- no new sign-ins' if s['frozen'] else 'open'}; invites {s['invites']}, promo readers "
              f"{s['promo_readers']}, devices {s['devices']}, live promos {s['live_promos']}")
        for e in es.events(15):
            print(f"  {when(e['ts'])}  {e['actor'] or '-':<14} {e['action']:<13} {e['target'] or '':<13} {e['detail'] or ''}")
    else:
        sys.exit(f"unknown command {cmd!r} (es-invite.sh with no arguments for help)")
except es.InviteError as e:
    sys.exit(f"refused: {e.detail}")
PYEOF
