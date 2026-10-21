# Client demo preview pattern

Verified against HEAD 2c3f81b and live state on 2026-10-06 18:25Z / 14:25 ET.

Generic, reusable pattern for a password-gated public preview of a
prospective/current client's AI-rewritten static site. Generalized
2026-09-03 from an earlier one-off client-preview instance built
2026-08-18 — that instance was **not** migrated to this pattern. It and a
second one-off (v1 of the same site, built 2026-09-06) both run today as
their own Quadlets, `corporatetraveldc-ccw-demo.container` (:8085) and
`corporatetraveldc-ccw-preview1.container` (:8086), both now tracked in
`.config/containers/systemd/`. Their shared webdev-credential expiry timer
(`corporatetraveldc-ccw-demo-webdev-expiry.timer`, live-only, untracked)
fired 2026-08-25 04:10Z.

**Current state of this pattern:** the template and expiry units are
installed, but **no instance of it exists** — no
`corporatetraveldc-client-demo@<slug>` container or timer is installed. A
fixed-hostname successor, the "demo portals"
(`corporatetraveldc-demo-portal-{client,personal}.container`, ports 8088 and
8087, swapped with `scripts/demo-portal.sh`), is tracked since 2026-09-18 ~~but
not installed,~~ (corrected 2026-10-15: installed 2026-10-12 and both
`corporatetraveldc-demo-portal-client` and `-personal` are active, bound to
127.0.0.1 and the Tailscale address only) has no tunnel ingress (no 8087/8088
route in the tracked `cloudflared/` or `nginx/` config), and its header points at a
`docs/DEMO_PORTALS.md` that does not exist (still missing 2026-10-15).

2026-10-16: the two portal quadlets carry no `WantedBy=` (anti-boot-storm
rule) and their headers claimed `stack-boot-ctl.sh`'s ORDER would start them
— it never listed either, so after every reboot both stayed down until
started by hand. New
`.config/systemd/user/corporatetraveldc-demo-portals-boot.service` (oneshot,
`After=`/`Requires=corporatetraveldc-boot-stagger.service`, the end of the
serialized rollout; `WantedBy=default.target`) starts both portals with
`systemctl --user start --no-block` once the whole stack is up. Test:
`tests/scripts/test_demo_portals_boot.py`.

Rechecked 2026-10-15 01:45Z: still no `corporatetraveldc-client-demo@<slug>`
instance; `corporatetraveldc-ccw-demo` and `corporatetraveldc-ccw-preview1` are
active; the `corporatetraveldc-ccw-demo-webdev-expiry.timer` last fired
2026-08-25 04:10Z and has no next run.

## The pieces

- `.config/containers/systemd/corporatetraveldc-client-demo@.container` —
  the generic Quadlet template. `%i` is the client slug and drives every
  per-client path; there is deliberately no `PublishPort=` in the base
  file, since ports collide across clients and each instance must supply
  its own.
- `.config/systemd/user/corporatetraveldc-client-demo-webdev-expiry@.service`
  / `@.timer` — a generic one-shot + timer pair that strips a time-limited
  `webdev` Basic Auth credential 7 days after the instance is scaffolded.
  The base `@.timer`'s own `OnActiveSec=7d` is **not** what enforces the
  deadline (found 2026-09-03: it's monotonic from unit activation and
  resets on every reboot, so a box rebooting more often than weekly would
  never fire it) — the generator now pins the real per-instance deadline
  via a `10-instance.conf` timer drop-in setting an absolute
  `OnCalendar=<creation+7d> America/New_York`, the same reboot-proof
  semantics the original one-off's hand-written absolute-date version had.
- `scripts/client-demo-webdev-expire.sh <slug>` — the script the expiry
  service calls.
- `scripts/templates/client-demo-nginx.conf.tmpl` — nginx config template
  (Basic Auth realm, cache headers, `/.well-known/` dotfile handling),
  rendered per-instance by the generator.
- `scripts/new-client-demo.sh <slug> <port> ["Display Name"]` — scaffolds
  a new instance: creates `/home/corporatetraveldc/demos/<slug>/{site,auth}`,
  renders `nginx.conf`, symlinks the container instance to the template
  (`podman-systemd.unit(5)`'s documented instanced-template pattern —
  `foo@<instance>.container` as a symlink to `foo@.container`), writes a
  `corporatetraveldc-client-demo@<slug>.container.d/10-instance.conf`
  drop-in supplying `PublishPort=`, writes a matching
  `corporatetraveldc-client-demo-webdev-expiry@<slug>.timer.d/10-instance.conf`
  drop-in pinning the absolute 7-day `OnCalendar=` deadline, and enables
  (does not start) the timer instance.

## What the generator does NOT do

- Doesn't populate `site/` — the actual site content is still built and
  placed by hand (or by whatever AI-rewrite workflow produced it, same as
  every prior demo).
- Doesn't create `auth/.htpasswd` — run `htpasswd` yourself.
- Doesn't start anything — review what got scaffolded first.
- Doesn't touch the Cloudflare Tunnel config — adding the public hostname
  route (`<slug>-preview.example.com` → `127.0.0.1:<port>`,
  same shape as every prior demo) is still a manual step.
- Doesn't check for port collisions across existing client-demo
  instances — pick an unused one yourself.

A client preview is a static site and carries no flight data. The dispatch demo is separate: public
callers only ever reach the demo runner (cleared, sanitized windows), never the live panel, and aircraft
on the FAA's LADD privacy list get synthetic registrations, 15-minute bucketed times behind a hidden
rotating per-leg offset, and a dark point at the inner marker / threshold / touchdown -- see
`docs/dispatch-runner-design.md` "Public = demo, tailnet = live" and `docs/LADD_CUI_HANDLING.md`
"Demo timing anti-correlation".

## Using it

```
scripts/new-client-demo.sh acme-livery 8086 "Acme Livery Service"
# follow the printed next-steps (populate site/, htpasswd, daemon-reload,
# start the .service and the expiry .timer, add the Tunnel route)
```

To remove an instance: stop and disable both units
(`corporatetraveldc-client-demo@<slug>.service`,
`corporatetraveldc-client-demo-webdev-expiry@<slug>.timer`), remove the
symlink and its `.d/` drop-in directory, remove
`/home/corporatetraveldc/demos/<slug>/`, and drop the Cloudflare Tunnel
route.

Validated 2026-09-03 by scaffolding and dry-run-resolving a disposable
`zzz-validation-test` instance (confirmed the symlink + drop-in resolve
correctly via `podman quadlet -dryrun -user`, `PublishPort` came through
from the instance drop-in) — never started, fully removed afterward.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-core.md`.


### Client demo preview pattern

~~Generic, reusable pattern for a password-gated public preview of a prospective/current client's AI-rewritten static site. Generalized 2026-09-03 from an earlier one-off client-preview instance built 2026-08-18 — that instance stayed running exactly as it was and was **not** migrated to this pattern; there was no need to disrupt a live client preview to adopt a new convention. It's documented here as the origin, not as an example instantiation. See that instance's own still-live unit file for which client and hostname it actually is.~~
