# Tailnet Migration Inventory -- method (template)

> Generalized, sanitized template of a private document
> (`docs/TAILNET_MIGRATION_INVENTORY.md`). The private file is a dated audit
> trail of one real migration; this template keeps the method and drops every
> real inventory row, suffix, address and device name.
> Verified against the private source on 2026-10-06 22:45Z / 18:45 ET.

Use this when moving a dispatch node (and its peers) from one tailnet to
another: a rebuild after a Tailnet Lock lockout, an account change, or a
consolidation. Placeholders: `<old>.ts.net` / `<new>.ts.net` (the MagicDNS
suffixes), `192.0.2.10` (the node's tailnet address), `<node>` (its machine
name), `<prefix>` (your unit/service name prefix).

## 1. What changes and what does not

| Reference type | Example | Needs change? |
|---|---|---|
| MagicDNS suffix | `<node>.<old>.ts.net` | **Always** |
| Tailnet IP (100.64.0.0/10) | `192.0.2.10:8000` | Only if the node does **not** keep its address on rejoin. In the reference migration every device rejoined with its old address, so every IP-only reference needed no change. Confirm with `tailscale ip -4` before deciding. |
| TLS certs issued for the old FQDN | nginx tailnet vhost, NTS cert for chrony | **Always** -- reissue for the new FQDN (`tailscale cert`) and confirm the renew unit names the new FQDN |
| Tailnet policy (tag owners, SSH rules) | `tailscale/policy.hujson` | **Always** -- the new tailnet starts empty; apply the tracked policy before the first `tailscale up` with tags |
| Placeholder-only templates | `dispatch.env.example`, scrubbed public copies | No |

## 2. Where references live (inventory these buckets separately)

1. **LIVE-ETC** -- live config outside the repo: `/etc/<prefix>/*.env`,
   `/etc/nginx/conf.d/`, `/etc/systemd/system/` (unit files *and* drop-ins,
   including `Description=` lines), resolver config (`/etc/unbound/`), and
   the user Quadlet dir `~/.config/containers/systemd/` (`PublishPort=` bound
   to the tailnet address, `Environment=*_BASE_URL=`).
2. **INTERNAL-REPO** -- the private platform repo: quadlets, nginx vhost
   copies, cert-renew and network scripts, the public-tree scrubber,
   `src/common/config.py`-style defaults, skills, docs.
3. **SIBLING-REPOS** -- every other private repo on the box (DNS/host
   hardening, website repos) that names the node address or suffix.
4. **PUBLIC-REPO-REAL** -- public mirrors that accidentally carry a real
   address or suffix. Each hit is a leak to fix by parameterizing (an env
   var with a placeholder default), not by swapping one real value for
   another.
5. **PLACEHOLDER-ONLY** -- files that already use placeholders. List them
   once for completeness; no action.

## 3. How to grep

Run as the operator (no sudo needed for the user trees); keep the output
as the dated pre-migration snapshot.

```sh
OLD_SUFFIX='<old>.ts.net'
NODE_IP='192.0.2.10'
PAT="${OLD_SUFFIX//./\\.}|${NODE_IP//./\\.}"

# LIVE-ETC
grep -rnE "$PAT" /etc/<prefix> /etc/nginx/conf.d /etc/systemd/system \
  /etc/unbound ~/.config/containers/systemd 2>/dev/null

# INTERNAL-REPO and SIBLING-REPOS (one per repo)
git -C <repo> grep -nE "$PAT"

# PUBLIC-REPO-REAL
git -C <public-mirror> grep -nE "$PAT"
```

Record counts per bucket (`lines, files`). A count, not just a list, is what
lets the post-cutover check prove nothing was missed.

## 4. Cutover order

1. Export and commit the current tailnet policy; prepare it for the new
   tailnet.
2. Confirm which Tailnet Lock signers will exist on the new tailnet and that
   at least one is online (see `docs/HEADLESS_ACCESS.example.md`).
3. Join each device to the new tailnet in **one clean pass** per device
   (one `tailscale up`, one auth approval, one lock signature).
4. Apply the policy (tag owners + SSH rule) before advertising tags.
5. Swap the suffix in INTERNAL-REPO and SIBLING-REPOS; set the deployment's
   suffix variable (e.g. `TAILSCALE_DOMAIN_SUFFIX`) in the live env file.
6. Add the **new** suffix to the public-tree scrubber's literal list and keep
   the **old** one there too.
7. Parameterize any PUBLIC-REPO-REAL hit.
8. Reissue certs for the new FQDN; restart (not just reload) nginx if the
   cert path or vhost `server_name` changed; confirm the cert-renew timer
   fires and names the new FQDN.
9. Sign, rebuild and restart per the normal deploy order.

## 5. Post-cutover verification

- `tailscale status` -- this node on `<node>.<new>.ts.net`, every expected
  peer present (peer names can drift from earlier records after device-side
  renames; match by address, not by name).
- `grep -rl '<old>.ts.net' /etc/nginx/conf.d /etc/systemd/system` -> no
  matches.
- `git grep -l '<old>.ts.net'` in the private repo -> only expected,
  legitimate hits: the dated inventory itself, historical narrative in the
  access doc, and the scrubber's literal list. Do not "fix" those.
- Public mirrors: zero hits for either suffix or the node address.
- The tailnet HTTPS vhost answers on the new name (probe from another
  tailnet device -- a node may not resolve its own MagicDNS name).

## 6. Keeping the private record honest

The real inventory is a dated historical record, not live state. When it is
re-verified later, add a short STATUS block with the targeted re-checks
(etc-dir residue, repo-wide count of the old suffix, `tailscale status`) and
mark the untouched historical grep dump `[UNVERIFIED]` rather than implying
it was re-run. Settings that were renamed by unrelated later migrations
(for example an LLM endpoint variable) should be noted as historical where
they appear, not silently edited.
