#!/usr/bin/env python3
"""Render the same capability guidance for one registered account, without secrets.

Reads registry metadata and passwd home paths only. Does not inspect homes,
credentials, grants, processes, keys or live services. Output is an onboarding
section, not evidence that an account currently has any particular capability.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import pwd
import re


def render(account: str, registry: Path) -> str:
    if not re.fullmatch(r"ctdc-[a-z0-9-]+", account):
        raise ValueError("invalid account name")
    rows = [line.split() for line in registry.read_text().splitlines()
            if line.split() and line.split()[0] == account]
    if len(rows) != 1 or len(rows[0]) != 4:
        raise ValueError("account needs exactly one four-field registry row")
    _, kind, mode, preloaded = rows[0]
    if (kind, mode) not in {("agent", "claude"), ("agent", "codex"),
                           ("agent", "ssh"), ("service", "none")}:
        raise ValueError("unsupported account kind/login mode")
    if preloaded not in {"0", "1"}:
        raise ValueError("invalid preload state")
    home = pwd.getpwnam(account).pw_dir
    if kind == "service":
        skills = ("You are a service identity with no interactive login. No home skill "
                  "copies are installed by the agent-grants reconciler. Use your own "
                  "integration/connector's granted skill instructions; do not borrow "
                  "another account's copies. The registry does not identify the "
                  "physical location of the client running your integration.")
    else:
        base = ".agents" if mode == "codex" else ".claude"
        skills = (f"Your granted home skills belong in `{home}/{base}/skills/`, "
                  "as root-owned copies installed by the grants reconciler. "
                  "Matching names in a legacy directory or connector do not prove "
                  "matching content; compare revisions/hashes against signed `skills/`. "
                  "Do not edit installed copies yourself.")
    lifecycle = {
        "codex": "Codex login refresh within 14 days AND an active remote-control unit or a running codex process.",
        "claude": "Claude login refresh within 7 days; if a remote-control unit is configured, it must be active or a claude process running.",
        "ssh": "Exactly one inbound key and last SSH login within 7 days for an agent.",
        "none": "No interactive-login factor for this service identity.",
    }[mode]
    state = ("This account is preloaded and not activated. The guidance below "
             "describes its future authorized operation; it grants nothing."
             if preloaded == "1" else
             "Registry state is activated; this is not a live liveness or authorization check.")
    return f"""## Your action capabilities after segmentation

Account: `{account}`. Kind: `{kind}`. Login mode: `{mode}`.
This dated correction supersedes older credential/skill-delivery instructions;
operator decisions and verified live state remain authoritative.
{state}

Login liveness for this mode: {lifecycle} Signer, token and kill-order checks
are separate. The configured liveness controller and its authority-to-act gate
remain decisive; this text is not permission to change an account's state.

{skills}

A skill, an API action grant and a connector tool are separate. Reading a skill
does not run it or make its actions available. Check your own current tool list;
the gateway catalog reviewed on 2026-10-29 has eight tools and no watchlist action.

For an authorized on-box request, an operator may separately mint your personal
action-scoped token at `{home}/.config/ctdc/api-token`. Its existence and scopes
are not implied by this pamphlet. A cloud client cannot assume filesystem access
to that account-side path. Account creation does not mint the token, and the env
loader does not load it. The group subset deliberately has no shared admin token.

Use this fallback under your own identity:

1. Use your own connector only when it exposes and authorizes the requested action.
2. With actual on-box access, check your own personal token file: regular file,
   current UID owner, no group/other permissions, no symlink, nonempty. Read its
   value only inside the request process; never print it, source it, put it in
   argv or copy it to a prompt. Dispatch base: `http://100.x.x.x:8000`.
3. Missing file/tool or denied authorization means report the capability gap.
   Never self-mint, broaden scope, use another account or seek shared credentials.
   A 403 alone cannot distinguish expiry/revocation from a tier/scope problem.
4. Confirm the action's own response. After an uncertain write, inspect the target
   if authorized before retrying. A health endpoint may correctly deny a token
   that is scoped to a different action.

A flight milestone watch add requires `watchlist.flight.add` on a personal
admin-tier token; use `added_by={account}`. This scope is for the action, not
one exact flight. It grants no delete, batch add, manual push, approval, deploy
or publish action. A 201 confirms added/refreshed, not milestone/push delivery.
Pushes require separate authorization; report when unavailable.

For Jarvis/vault access use your own connector's `research_read`, `research_list`
and `workspace_contribute` when present. Contributions are create-only and
attributed. The workspace route is available, not a future feature. Do not use
`remember.py`, raw WebDAV, a shared admin credential, or signing helpers while
the no-signing/key-access rule applies. Never approve anything.

The operator chooses any missing grant with `scripts/mint-agent-api-token.sh`
for this account and exact named actions. See `docs/PER_AGENT_API_ACTIONS.md`.
Keep lifecycle/liveness separate from API access; token-file presence proves
neither. A client's vendor name does not determine whether it has shell access.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--account", required=True)
    parser.add_argument("--registry", type=Path, default=Path("/etc/ctdc-accounts.conf"))
    args = parser.parse_args()
    try:
        print(render(args.account, args.registry), end="")
    except (OSError, KeyError, ValueError) as exc:
        parser.exit(2, f"capability section not rendered: {type(exc).__name__}\n")


if __name__ == "__main__":
    main()
