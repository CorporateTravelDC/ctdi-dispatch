# Retired 2026-10-14 -- GitHub PAT expiry checker

`check-pat-expiry.sh` was meant to page `dispatch-alerts` before the GitHub personal access token in
`~/.secrets/github_pat.token` expired. It never worked and is no longer needed (ntfy topic audit 2026-10-13,
operator 2026-10-14):

- **Never ran:** no timer or cron entry existed.
- **Wrong inputs:** it read `~/.secrets/github.token` and `~/.secrets/ntfy.token`, neither of which exists, and
  expected a `Valid thru …` header the real file does not have.
- **Nothing uses the PAT:** every git push goes over SSH keys, and the `gh` CLI uses its own OAuth login token
  (`~/.config/gh/hosts.yml`). The two tokens were compared by hash and differ, and `GH_TOKEN`/`GITHUB_TOKEN` are not set.
- **The PAT had expired anyway** (Sep 13, 2026). The operator deletes it on GitHub; the local file is kept as an empty placeholder (value shredded, 0600).

`dispatch-alerts` had no other sender and is retired with it. Kept here for the record, not installed or run.
