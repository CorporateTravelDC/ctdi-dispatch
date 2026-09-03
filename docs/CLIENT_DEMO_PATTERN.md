# Client demo preview pattern

Generic, reusable pattern for a password-gated public preview of a
prospective/current client's AI-rewritten static site. Generalized
2026-09-03 from an earlier one-off client-preview instance built
2026-08-18 — that instance stayed running exactly as it was and was
**not** migrated to this pattern; there was no need to disrupt a live
client preview to adopt a new convention. It's documented here as the
origin, not as an example instantiation. See that instance's own
still-live unit file for which client and hostname it actually is.

## The pieces

- `.config/containers/systemd/corporatetraveldc-client-demo@.container` —
  the generic Quadlet template. `%i` is the client slug and drives every
  per-client path; there is deliberately no `PublishPort=` in the base
  file, since ports collide across clients and each instance must supply
  its own.
- `.config/systemd/user/corporatetraveldc-client-demo-webdev-expiry@.service`
  / `@.timer` — a generic one-shot + timer pair that strips a time-limited
  `webdev` Basic Auth credential 7 days after the timer instance starts
  (`OnActiveSec=7d`, not a hardcoded calendar date — the timer instance
  itself carries no client-specific state, unlike the original one-off's
  hand-written absolute-date version).
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
  drop-in supplying `PublishPort=`, and enables (does not start) the
  matching expiry timer instance.

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
