#!/usr/bin/env bash
# new-client-demo.sh -- scaffold a new password-gated client demo preview
# from the generic corporatetraveldc-client-demo@.container pattern
# (generalized 2026-09-03 from an earlier one-off client-preview container
# -- see that container file's header comment for the history; that
# earlier instance was left untouched, not migrated).
#
# Creates:
#   - /home/corporatetraveldc/demos/<slug>/{site,auth}/ (site/ left for you
#     to populate; auth/.htpasswd left for you to create with htpasswd)
#   - /home/corporatetraveldc/demos/<slug>/nginx.conf, rendered from
#     scripts/templates/client-demo-nginx.conf.tmpl
#   - the symlink corporatetraveldc-client-demo@<slug>.container ->
#     corporatetraveldc-client-demo@.container (podman-systemd.unit(5)'s
#     documented instanced-template pattern)
#   - a per-instance drop-in supplying PublishPort= (ports collide across
#     clients, so every instance needs its own -- pick one not already in
#     use; this script does not check for collisions)
#   - a per-instance expiry-timer drop-in pinning an absolute
#     OnCalendar= expiry (creation time + 7 days, America/New_York),
#     overriding the shared template's OnActiveSec=7d -- see the comment
#     at the drop-in generation below for why the monotonic form is
#     broken across reboots
#   - enables (does not start) the matching webdev-credential-expiry timer
#     instance
#
# Does NOT: create the Cloudflare Tunnel route (still a manual step, same
# as every prior demo), start any unit, or write .htpasswd for you.
#
# Usage: new-client-demo.sh <slug> <port> ["Display Name"]
#   slug         short identifier, used in paths/unit names (e.g. acme-livery)
#   port         host port for this instance's PublishPort= (e.g. 8086)
#   display-name optional; defaults to slug. Used in the nginx auth realm.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUADLET_DIR="${HOME}/.config/containers/systemd"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

SLUG="${1:?usage: new-client-demo.sh <slug> <port> [\"Display Name\"]}"
PORT="${2:?usage: new-client-demo.sh <slug> <port> [\"Display Name\"]}"
DISPLAY_NAME="${3:-$SLUG}"

if [[ ! "$SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    echo "ERROR: slug must be lowercase alnum/hyphens (got: $SLUG)" >&2
    exit 1
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
    echo "ERROR: port must be numeric (got: $PORT)" >&2
    exit 1
fi

DEMO_DIR="/home/corporatetraveldc/demos/${SLUG}"

if [[ -e "$DEMO_DIR" ]]; then
    echo "ERROR: ${DEMO_DIR} already exists -- refusing to overwrite an existing demo." >&2
    exit 1
fi

echo "[new-client-demo] creating ${DEMO_DIR}/{site,auth}..."
mkdir -p "${DEMO_DIR}/site" "${DEMO_DIR}/auth"

echo "[new-client-demo] rendering nginx.conf (port=${PORT}, display-name='${DISPLAY_NAME}')..."
sed -e "s/__PORT__/${PORT}/g" \
    -e "s/__SLUG__/${SLUG}/g" \
    -e "s/__DISPLAY_NAME__/${DISPLAY_NAME}/g" \
    "${REPO_DIR}/scripts/templates/client-demo-nginx.conf.tmpl" \
    > "${DEMO_DIR}/nginx.conf"

echo "[new-client-demo] linking corporatetraveldc-client-demo@${SLUG}.container..."
ln -s "corporatetraveldc-client-demo@.container" \
    "${QUADLET_DIR}/corporatetraveldc-client-demo@${SLUG}.container"

echo "[new-client-demo] writing port drop-in..."
mkdir -p "${QUADLET_DIR}/corporatetraveldc-client-demo@${SLUG}.container.d"
cat > "${QUADLET_DIR}/corporatetraveldc-client-demo@${SLUG}.container.d/10-instance.conf" <<EOF
[Container]
PublishPort=127.0.0.1:${PORT}:${PORT}
EOF

# Expiry-timer drop-in (added 2026-09-03). The shared template's
# OnActiveSec=7d is monotonic, measured from timer-unit ACTIVATION -- and
# because the timer is enabled into timers.target, every reboot
# re-activates it and restarts the 7-day countdown from zero.
# Persistent=true does not rescue it: per systemd.timer(5), Persistent=
# only takes effect for OnCalendar= timers. Net effect: a box that
# reboots more often than every 7 days would NEVER fire the expiry, and
# the time-limited webdev credential would stay live indefinitely on a
# Tunnel-exposed site. Fix: pin each instance to an absolute calendar
# date -- creation time + 7 days, America/New_York -- via the same
# instanced-template + drop-in convention as the port drop-in above.
# OnActiveSec= (empty) clears the template's monotonic setting;
# Persistent=true inherited from the template now genuinely applies, so
# an expiry moment slept/powered-off through still fires on next boot.
EXPIRY_LOCAL="$(TZ=America/New_York date -d '+7 days' '+%Y-%m-%d %H:%M:%S')"
echo "[new-client-demo] writing expiry-timer drop-in (fires ${EXPIRY_LOCAL} America/New_York)..."
mkdir -p "${SYSTEMD_USER_DIR}/corporatetraveldc-client-demo-webdev-expiry@${SLUG}.timer.d"
cat > "${SYSTEMD_USER_DIR}/corporatetraveldc-client-demo-webdev-expiry@${SLUG}.timer.d/10-instance.conf" <<EOF
[Timer]
# Written by new-client-demo.sh for '${SLUG}' -- absolute expiry replacing
# the template's reboot-resettable OnActiveSec=7d (Persistent= is a no-op
# on monotonic timers per systemd.timer(5); with OnCalendar= it works).
OnActiveSec=
OnCalendar=${EXPIRY_LOCAL} America/New_York
EOF

echo "[new-client-demo] enabling webdev-credential-expiry timer for '${SLUG}' (not started)..."
systemctl --user daemon-reload
systemctl --user enable "corporatetraveldc-client-demo-webdev-expiry@${SLUG}.timer" 2>&1 || true

cat <<EOF

[new-client-demo] Scaffolding done for '${SLUG}'. Remaining manual steps:
  1. Populate ${DEMO_DIR}/site/ with the actual site content.
  2. Create ${DEMO_DIR}/auth/.htpasswd (e.g. htpasswd -c ... owner, htpasswd ... webdev).
  3. systemctl --user daemon-reload
  4. systemctl --user start corporatetraveldc-client-demo@${SLUG}.service
  5. systemctl --user start corporatetraveldc-client-demo-webdev-expiry@${SLUG}.timer
     (the 'webdev' credential expires at ${EXPIRY_LOCAL} America/New_York,
     pinned by this instance's timer drop-in; survives reboots -- skip if
     there's no time-limited credential to expire for this client)
  6. Add the Cloudflare Tunnel route for this demo's public hostname ->
     127.0.0.1:${PORT} (manual, same as every prior demo).
EOF
