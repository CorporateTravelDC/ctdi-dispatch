# dns-leak-watch

**DNS leak watch.** Flags destinations a watched phone connects to through this box that no DNS answer handed out to
anyone: the **hard-coded IP** class. It also has two context tiers, other-client and cached-stale.

It's also the **root collector** for DNS anomaly watch's **DNS bypass** class: plain DNS to an outside resolver, port
853, and DoH to a known encrypted resolver. That evidence is exported in `latest.json` and is reported there, not here.

Everything else is in [`docs/DNS_LEAK_WATCH.md`](../../docs/DNS_LEAK_WATCH.md): what each class proves, the false
positives, both setups (Tailscale exit node, or LAN gateway), how to read the report, and the privacy notes.

    sudo config/dns-leak-watch/apply.sh init-conf --clients "<v4>,<v6>" --iface tailscale0 --group <group> [--metered "<if>"]   # or --tailnet [--quarantine] for every tailnet device
    sudo config/dns-leak-watch/apply.sh install
    sudo config/dns-leak-watch/apply.sh release <host>|--all     # lift a connection-check quarantine
    sudo config/dns-leak-watch/apply.sh status
    sudo config/dns-leak-watch/apply.sh remove
