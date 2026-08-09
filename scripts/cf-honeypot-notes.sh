#!/bin/bash
# scripts/cf-honeypot-notes.sh -- builds a safely-escaped Cloudflare IP
# Access Rule JSON payload for a honeypot-triggered fail2ban ban, with a
# best-effort classification (AI-crawler vs vuln-scanner/bot) and the
# trapped path + user-agent, so bans are legible/queryable later in the
# Cloudflare dashboard (trend analysis, not just a bare IP + generic note).
#
# Security note (why this is NOT invoked with fail2ban's <matches> tag):
# fail2ban substitutes tags into actionban BEFORE bash ever sees the
# command line -- <matches> is the raw, attacker-controlled honeypot.log
# line (path + User-Agent are both attacker-supplied). Splicing that
# directly into a shell command string, even inside quotes, is a classic
# fail2ban misconfiguration: a crafted UA containing a quote or $(...)
# can break out of the quoting and execute arbitrary commands as whatever
# user fail2ban runs actions as (root, on the ban path). Only <ip> is
# passed here -- fail2ban regex-validates that as a well-formed IP address
# before substitution, so it carries no injection risk. This script
# re-reads the matching honeypot.log line itself, as DATA (into a bash
# variable via grep), and only ever hands that data to jq via --arg (a
# real argument, not shell-interpolated) to build the JSON body. Nothing
# attacker-controlled is ever re-embedded into a shell command.
set -uo pipefail

ip="${1:?usage: cf-honeypot-notes.sh <ip>}"
LOG="/var/log/nginx/honeypot.log"

# Most recent trap hit from this IP -- treated as data, never eval'd.
line="$(grep -F -- "${ip} - [" "${LOG}" 2>/dev/null | tail -n1)"

# sed (POSIX BRE), not grep -P: PCRE's \K/lookahead need grep's JIT compiler,
# which requires the `execmem` permission under fail2ban_t's SELinux domain --
# a real, avoidable grant. sed never JIT-compiles, so this gets the same
# extraction without needing that permission at all (see docs/HONEYPOT_FAIL2BAN.md
# postmortem -- audit2allow's first pass over-granted execmem for exactly this).
path="$(printf '%s' "${line}" | sed -n 's/.*"[A-Z]* \([^ ]*\) HTTP\/[0-9.]*".*/\1/p')"
ua="$(printf '%s' "${line}" | sed -n 's/.*ua="\([^"]*\)".*/\1/p')"
path="${path:-unknown}"
ua="${ua:-unknown}"

# Category per docs/HONEYPOT_FAIL2BAN.md's own (a)/(b) trap split.
category="scanner-or-bot"
case "${path}" in
    /ai-agents-keep-out/*|/internal-do-not-index/*) category="ai-crawler-ignored-directive" ;;
esac

# Best-effort UA fingerprint -- purely additive context for dashboard trends,
# never used for the category decision above (path is ground truth for that).
ua_lc="${ua,,}"
ua_hint="unclassified"
case "${ua_lc}" in
    *gptbot*|*ccbot*|*bytespider*|*claudebot*|*google-extended*|*anthropic-ai*|*perplexitybot*) ua_hint="ai-crawler-ua" ;;
    *ahrefsbot*|*semrushbot*|*mj12bot*|*dotbot*|*petalbot*)                                       ua_hint="seo-crawler-ua" ;;
    *nmap*|*sqlmap*|*nikto*|*masscan*|*zgrab*|*nuclei*)                                            ua_hint="scanner-tool-ua" ;;
    *python-requests*|*go-http-client*|*curl*|*wget*|*libwww-perl*)                                 ua_hint="generic-script-ua" ;;
esac

ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# jq --arg does the JSON string-escaping -- no hand-rolled quoting, ever.
jq -n --arg ip "${ip}" --arg ts "${ts}" --arg cat "${category}" \
      --arg hint "${ua_hint}" \
      --arg path "$(printf '%.200s' "${path}")" \
      --arg ua "$(printf '%.200s' "${ua}")" \
      '{mode: "block",
        configuration: {target: "ip", value: $ip},
        notes: ("honeypot | " + $ts + " | " + $cat + " (" + $hint + ") | path=" + $path + " | ua=" + $ua)}'
