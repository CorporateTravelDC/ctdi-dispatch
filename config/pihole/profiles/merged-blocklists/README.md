# merged-blocklists

**One merged Blocklist Project list for Pi-hole, next to HaGeZi, with dead domains pruned by evidence.** Keeps the
coverage of both projects while gravity carries fewer entries. Nothing is deleted: everything left out is archived,
and every existence check is kept as a dated record.

Everything else is in [`docs/BLOCKLIST_MERGE.md`](../../../../docs/BLOCKLIST_MERGE.md): the numbers behind it, how
each entry is kept or left out, the checker and its privacy properties, the self-test and the upstream reports.

    sudo config/pihole/profiles/merged-blocklists/apply.sh install            # engine, config, units, first build
    sudo config/pihole/profiles/merged-blocklists/apply.sh switch --dry-run   # which adlists it would switch
    sudo config/pihole/profiles/merged-blocklists/apply.sh switch             # BLP adlists off, merged pair on, pihole -g
    sudo config/pihole/profiles/merged-blocklists/apply.sh revert             # back to the separate BLP adlists
    sudo config/pihole/profiles/merged-blocklists/apply.sh status
