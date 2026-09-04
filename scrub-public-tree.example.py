#!/usr/bin/env python3
"""scripts/scrub-public-tree.example.py
Reference template for scripts/scrub-public-tree.py -- ships with zero real
values on purpose. Copy this file (and push-public.example.sh) into your own
repo, drop the ".example" suffix, then replace DROP_FILES, SUBSTITUTIONS,
REGEX_SWEEPS, and the verify_scrubbed() allowlists below with your own real
secrets/identifiers. This file is never touched by CTDI's own push-public.sh
-- it exists only as a working reference for other operators running a
similar private<>public repo split.

Mechanism (unchanged from the real script): walks a git tree object via
plumbing commands only (ls-tree / cat-file / hash-object / mktree) -- it
never touches your working directory or checks anything out. The input tree
stays exactly as committed; a new, separate sanitized tree object is built
and returned. Your real branch is never mutated.

Two independent layers, deliberately redundant:
  1. SUBSTITUTIONS / REGEX_SWEEPS -- proactively replace known real values.
  2. verify_scrubbed() -- an ALLOWLIST-based post-scan of the *output*.
     Anything shaped like an email/UUID/IPv4 that isn't on the allowlist
     fails the whole push (non-zero exit). This is the layer that actually
     matters: layer 1 only catches what someone remembered to add; layer 2
     catches everything else by refusing to ship anything unrecognized,
     rather than trusting the substitution table's completeness. Keep both
     layers -- don't rely on layer 1 alone, that's exactly what let real
     values through in the repo this template was extracted from.

Usage: python3 scripts/scrub-public-tree.py <tree-sha>
"""
import re
import subprocess
import sys

# Files to drop entirely from the public tree (relative to repo root, matched
# by basename). Put anything here that must never appear in public history,
# even scrubbed -- full credential files, operator-identifying status pages,
# real config with no safe placeholder form.
DROP_FILES = {
    "secrets.env",            # example: your real credentials file
    "credentials.env",        # example: a second real credentials file
    "STATUS.md",              # example: internal status page with contact info
}

# Public-safe substitutions: real_value -> placeholder. Layer 1 only --
# verify_scrubbed() below is what actually gates the push. Every entry here
# is illustrative, not real -- replace with your own.
SUBSTITUTIONS = {
    # Service account / integration credentials
    b"realuser@yourcompany.com": b"serviceuser@example.com",

    # Internal queue/resource UUIDs
    b"11111111-1111-1111-1111-111111111111": b"00000000-0000-0000-0000-000000000000",

    # Internal hostname prefixes (e.g. a queue-naming convention)
    b"realuser.QUEUE_A": b"serviceuser.QUEUE_A",

    # VPN / mesh-network hostnames (e.g. Tailscale MagicDNS)
    b"real-tailnet-name.ts.net": b"tailxxxxxxx.ts.net",

    # Personal email and real name
    b"jane.doe@yourcompany.com": b"swimuser@example.com",
    b"Jane Doe": b"the operator",

    # Third-party partner/vendor relationship
    b"Real Vendor Name LLC": b"[vendor partner]",

    # Real internal IP or VPN-assigned address
    b"10.x.x.x": b"10.x.x.x",

    # External tunnel/ingress UUID (e.g. Cloudflare Tunnel)
    b"22222222-2222-2222-2222-222222222222": b"00000000-0000-0000-0000-000000000000",
}

# Regex sweeps for shaped tokens/domains -- catches variants the literal
# dict above won't (new subdomains, newly issued tokens of a known prefix
# family, etc.). Each entry: (pattern, replacement).
REGEX_SWEEPS = [
    (re.compile(rb"yourapp_token_[A-Za-z0-9_\-]{10,}"), b"yourapp_token_REDACTED"),
    # General domain sweep -- catches every subdomain of the real business
    # domain (present and future), not just the handful hardcoded above.
    # Preserves the subdomain label, only swaps the root.
    (re.compile(rb"([A-Za-z0-9-]+\.)?yourrealdomain\.com"),
     lambda m: (m.group(1) or b"") + b"example.com"),
]

# ── verify_scrubbed() allowlists ────────────────────────────────────────────
# Anything matching these shapes in the OUTPUT tree must appear on the
# corresponding allowlist, or the push aborts. Add new legitimate
# placeholders/public values here as they come up -- don't loosen the shape
# regexes themselves.
EMAIL_RE = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ALLOWED_EMAILS = {
    b"serviceuser@example.com",
    b"swimuser@example.com",
}
ALLOWED_EMAIL_DOMAIN_SUFFIXES = (b"example.com",)

UUID_RE = re.compile(
    rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# Only all-zero-with-trailing-digit UUIDs are placeholders; anything else is
# a real UUID that needs a SUBSTITUTIONS entry (and re-running this check).
ALLOWED_UUID_RE = re.compile(rb"00000000-0000-0000-0000-00000000000[0-9a-fA-F]")
ALLOWED_UUIDS = set()  # add specific known-safe UUIDs here (test fixtures, etc.)

IPV4_RE = re.compile(rb"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ALLOWED_IPV4 = {
    b"0.0.0.0", b"127.0.0.0", b"127.0.0.1", b"255.255.255.255",
    b"1.1.1.1", b"8.8.8.8", b"8.8.4.4",  # well-known public resolvers, fine in docs
    # RFC1918 / CGNAT range bases used in code/docs to describe or match a
    # *range*, not a specific device -- not identifying. Adjust to whatever
    # ranges your own repo's code/docs actually reference.
    b"10.x.x.x", b"172.x.x.x", b"192.168.x.x", b"100.64.0.0",
}

# File extensions that are binary/compressed -- byte-level substitution or
# regex scanning on these is unreliable (can silently corrupt the file, or
# produce false-positive noise from compressed internals) and can just as
# easily MISS real sensitive text buried in a compression stream. Treated as
# unsafe-by-default: dropped entirely rather than scrubbed/verified, unless
# manually reviewed and explicitly moved out of this set (or DROP_FILES).
BINARY_SKIP_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
    ".ttf", ".zip", ".tar", ".gz",
}

FORBIDDEN_LITERALS = [
    b"Jane Doe",
    b"Real Vendor Name LLC",
    b"yourrealdomain.com",
    b"real-tailnet-name",
    b"22222222-2222-2222-2222-222222222222",
    b"10.x.x.x",
]

# Binary files that HAVE been manually reviewed for sensitive content and are
# confirmed safe to publish as-is (basename only). Empty by default -- every
# file under BINARY_SKIP_EXTENSIONS is dropped until someone actually opens
# it, reads it, and adds it here deliberately.
REVIEWED_BINARY_OK = set()


def git_out(*args, stdin=None):
    r = subprocess.run(["git"] + list(args), capture_output=True, input=stdin)
    if r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr.decode()}")
    return r.stdout


def scrub_blob(sha):
    content = git_out("cat-file", "blob", sha)
    new = content
    for old, repl in SUBSTITUTIONS.items():
        new = new.replace(old, repl)
    for pattern, repl in REGEX_SWEEPS:
        new = pattern.sub(repl, new)
    if new == content:
        return sha
    r = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input=new, capture_output=True
    )
    return r.stdout.decode().strip()


def scrub_tree(tree_sha, path_prefix=""):
    entries = []
    raw = git_out("ls-tree", tree_sha).decode()
    for line in raw.splitlines():
        mode_type, name = line.split("\t", 1)
        mode, obj_type, sha = mode_type.split()

        rel_path = f"{path_prefix}{name}" if not path_prefix else f"{path_prefix}/{name}"

        # Drop files that must never appear on the public mirror
        if obj_type == "blob" and name in DROP_FILES:
            print(f"[scrub] DROP: {rel_path}", file=sys.stderr)
            continue

        # Drop unreviewed binary/compressed files by default -- see
        # BINARY_SKIP_EXTENSIONS comment above.
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if obj_type == "blob" and ext in BINARY_SKIP_EXTENSIONS and name not in REVIEWED_BINARY_OK:
            print(f"[scrub] DROP (unreviewed binary): {rel_path}", file=sys.stderr)
            continue

        if obj_type == "blob":
            sha = scrub_blob(sha)
        elif obj_type == "tree":
            sha = scrub_tree(sha, rel_path)
            if sha is None:
                # Subtree ended up empty after dropping everything inside it
                # -- git trees can't contain an empty directory, so omit
                # this entry entirely rather than feeding mktree nothing.
                print(f"[scrub] DROP (now-empty dir): {rel_path}", file=sys.stderr)
                continue

        entries.append(f"{mode} {obj_type} {sha}\t{name}")

    if not entries:
        return None

    return git_out(
        "mktree",
        stdin=("\n".join(entries) + "\n").encode()
    ).decode().strip()


def verify_scrubbed(tree_sha):
    """Last line of defense: scan the ALREADY-SCRUBBED output tree and
    refuse to proceed if anything sensitive-shaped survived. This does not
    depend on SUBSTITUTIONS/REGEX_SWEEPS having been complete -- it
    independently re-checks the result against allowlists."""
    raw = git_out("ls-tree", "-r", tree_sha).decode()
    violations = []
    for line in raw.splitlines():
        mode_type, path = line.split("\t", 1)
        mode, obj_type, sha = mode_type.split()
        if obj_type != "blob":
            continue
        content = git_out("cat-file", "blob", sha)

        for literal in FORBIDDEN_LITERALS:
            if literal in content:
                violations.append(f"{path}: forbidden literal {literal!r}")

        for m in EMAIL_RE.finditer(content):
            addr = m.group(0)
            if addr in ALLOWED_EMAILS or addr.endswith(ALLOWED_EMAIL_DOMAIN_SUFFIXES):
                continue
            violations.append(f"{path}: unrecognized email {addr!r}")

        for m in UUID_RE.finditer(content):
            uid = m.group(0)
            if ALLOWED_UUID_RE.fullmatch(uid) or uid in ALLOWED_UUIDS:
                continue
            violations.append(f"{path}: unrecognized UUID {uid!r}")

        for m in IPV4_RE.finditer(content):
            ip = m.group(0)
            if ip not in ALLOWED_IPV4:
                violations.append(f"{path}: unrecognized IPv4 {ip!r}")

    if violations:
        print("[scrub] VERIFICATION FAILED -- refusing to push. "
              "Add a SUBSTITUTIONS/REGEX_SWEEPS entry (or allowlist the "
              "value if it's genuinely safe) and re-run:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    result_tree = scrub_tree(sys.argv[1])
    verify_scrubbed(result_tree)
    print(result_tree)
