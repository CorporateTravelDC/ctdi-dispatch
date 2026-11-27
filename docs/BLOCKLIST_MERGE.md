# Blocklist merge

`config/pihole/profiles/merged-blocklists/` replaces the separate [Blocklist Project](https://github.com/blocklistproject/Lists)
adlists in Pi-hole with **one merged pair of lists** that sits next to [HaGeZi's lists](https://github.com/hagezi/dns-blocklists)
(Pro, Threat Intelligence Feeds, Encrypted DNS). It keeps what both projects block, and makes the Blocklist Project
part smaller only where there is evidence an entry adds nothing.

## Why (measured 2026-11-27)

- Swapping Blocklist Project for HaGeZi looked like a like-for-like trade. It is not: of the 3,272,232 entries in
  Blocklist Project's malware, abuse, fraud, phishing, ads, crypto and ransomware lists, HaGeZi Pro + TIF block only
  17% (directly or through a parent domain). 2,706,102 would have gone unblocked.
- Of a random 2,000 of those, resolved directly through Unbound: 51.0% NXDOMAIN, **37.5% still resolved**, 6.4%
  SERVFAIL, 3.5% no address, 1.6% timeout. Roughly a million live names, so dropping them was not an option.
- Keeping both meant about 5.3M gravity entries, which slows Pi-hole's lists pages and every gravity run.

## What the merged list keeps and leaves out

Each Blocklist Project entry (all 16 lists, about 4.7M names) is:

| Step | What happens | 2026-11-27 |
|---|---|---|
| covered | HaGeZi already blocks it (the name itself, or a parent under HaGeZi's wildcard) | 560,521 left out |
| folded | it is under another listed entry: written once as `\|\|parent^` (Pi-hole blocks the parent and every subdomain) | 389,075 children folded into 330,067 parents |
| platform guard | its registrable domain has `PLATFORM_MIN` (100) or more listed subdomains: a shared platform (free hosting, social sites); **never folded**, every entry stays exact | 324 platforms |
| pruned | its registrable domain does not exist (below) | grows as the checks run |
| kept | everything else, as an exact name | |

Before any pruning, the merged list is 3,764,572 entries; with HaGeZi's 1.35M, about 5.1M in gravity: the same as
before the swap, but with both projects' coverage. The size saving comes from pruning, as the checks complete.

## Pruning: does the domain still exist?

The hourly checker (`ctdc-blmerge-check.timer`) asks for the **DS record of each registrable domain** (the domain
someone registers, from the Public Suffix List). A DS record is answered by the *parent* zone, the TLD's servers, so:
- a registered domain answers NOERROR (with or without a DS record): **alive**;
- an unregistered one answers NXDOMAIN: no name under it can resolve, so none of its entries can do harm;
- the domain's own nameservers, which a malicious operator may watch, **never see the check**.

A domain is **dead** after `PRUNE_CONFIRM` (2) NXDOMAIN answers at least `PRUNE_MIN_DAYS` (7) apart. SERVFAIL and
timeouts are no evidence either way. Dead domains are rechecked every `DEAD_RECHECK_DAYS` (30); **one** NOERROR
brings a domain back (`revived`) at the next daily build. A dead domain that **any device here looks up** (Pi-hole's
log, yesterday and today) jumps to the front of the check queue, and a push names it.

The checker runs as the `unbound` user, because the DNS lock lets only Unbound reach outside DNS, and uses its **own
libunbound resolver and cache**, so millions of checks never push real traffic out of the production Unbound cache.
Pace: `CHECK_PER_DAY` (100,000) at `CHECK_QPS` (20) a second; skipped above `LOAD_MAX` and on a metered uplink. A
first full pass over the ~2.2M registrable domains takes about three weeks, and confirmations follow a week later.

Only registrable domains are pruned. A dead host under a live domain stays in the list: that is the conservative
side of the trade.

## The archive and the longitudinal record

- `OUT_DIR/archive/YYYY-MM-snapshot.tsv.gz`: every Blocklist Project entry HaGeZi does not cover, with its source
  list(s) and registrable domain, once a month; `YYYY-MM-DD-added/removed.tsv.gz` daily diffs between snapshots.
- `CHECK_DIR/results.sqlite`: per registrable domain its state, NXDOMAIN count and dates, last alive, checks; and
  every state transition with its date. This is the evidence for "as of DATE, this domain did (not) exist".
- `OUT_DIR/report.txt` and `latest.json` (schema 1): the counts above, the checks by state, the last 30 days of
  transitions, and the gravity status.

## Self-test

After each build (`ExecStartPost`): positive controls (merged entries that are themselves a confirmed-alive
registrable domain) must be **blocked by Pi-hole**; negative controls (entries under a confirmed-dead domain) must
**not resolve at all** through Unbound directly, whether or not anything blocks them. A failure is pushed. The control
names come from the private state at run time; no real list content is ever in the repository.

## Upstream reports (drafts; the operator files them)

`blocklist_merge.py contrib` writes, to `OUT_DIR/contrib/DATE/`:
- `blp-dead-DATE.csv`: Blocklist Project entries whose registrable domain is confirmed non-existent, with the first
  and last NXDOMAIN dates and the check count, for a pruning report to Blocklist Project;
- `hagezi-candidates-DATE.csv`: entries only Blocklist Project lists, under a domain confirmed alive, that a device
  here looked up: candidates to propose to HaGeZi.

## Switching Pi-hole over

`apply.sh switch` refuses unless the HaGeZi lists the merge relies on (`HAGEZI_LISTS`) are enabled adlists (otherwise
what it leaves out would go unblocked). It switches every enabled Blocklist Project adlist off (recording the ids),
adds the two merged lists as `file://` adlists, runs `pihole -g`, and **undoes itself** if Pi-hole then reports fewer
than 1,000 entries from them. `apply.sh revert` restores the recorded adlists. The daily build runs `pihole -g` only
when the lists changed and the load is at most `GRAVITY_LOAD_MAX`; otherwise Pi-hole's own next gravity run picks
them up.

## Credits

The [Block List Project](https://github.com/blocklistproject/Lists) and [HaGeZi](https://github.com/hagezi)
(Gerd, [hagezi/dns-blocklists](https://github.com/hagezi/dns-blocklists)) maintain the lists this merges; the
[Public Suffix List](https://publicsuffix.org/) (Mozilla) defines registrable domains.
