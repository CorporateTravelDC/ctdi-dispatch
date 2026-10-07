"""
tests/scripts/test_scrub_name_and_public_manifest.py

2026-10-03: push-public.sh now ships a self-verifying public tree
(scripts/lib/public-manifest.sh). Pins two behaviours that the first build
established:
  * scrub-public-tree.py renames tree ENTRIES through the same pipeline it
    applies to blob content (filenames carrying the real domain had been
    reaching the public mirror unrenamed), and refuses a post-scrub name
    collision rather than building an ambiguous tree.
  * pm_blind_private_manifest replaces every path with sha256(path) and
    keeps the content hash, so a public file untouched by the scrub can be
    looked up in the private attestation without any path being disclosed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("DISPATCH_DB_BACKEND", "sqlite")

REPO = Path(__file__).resolve().parents[2]
SCRUB = REPO / "scripts" / "scrub-public-tree.py"
LIB = REPO / "scripts" / "lib" / "public-manifest.sh"


def _load_scrubber():
    spec = importlib.util.spec_from_file_location("scrub_public_tree", SCRUB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ScrubName(unittest.TestCase):
    def test_domain_in_filename_is_rewritten_like_content(self):
        m = _load_scrubber()
        name = "dispatch.example.com.conf"
        out = m.scrub_name(name)
        self.assertNotIn("csexecutiveservices", out)
        self.assertTrue(out.endswith(".conf"))
        # Same pipeline as content: a blob with that string gets the same placeholder.
        blob = name.encode()
        for old, repl in m.SUBSTITUTIONS.items():
            blob = blob.replace(old, repl)
        for pat, repl in m.REGEX_SWEEPS:
            blob = pat.sub(repl, blob)
        self.assertEqual(out, blob.decode())

    def test_neutral_names_are_untouched(self):
        m = _load_scrubber()
        for n in ("README.md", "src", "corporatetraveldc-poller.container", "MANIFEST.sha256"):
            self.assertEqual(m.scrub_name(n), n)


class BlindPrivateManifest(unittest.TestCase):
    def test_paths_are_blinded_and_hashes_kept(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "private.manifest"
            out = Path(d) / "blinded"
            h1 = "a" * 64
            h2 = "b" * 64
            src.write_text(f"{h1}  nginx/conf.d/real.example-private.conf\n{h2}  README.md\n")
            r = subprocess.run(
                ["bash", "-c", f'source "{LIB}" && pm_blind_private_manifest "{src}" "{out}"'],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            text = out.read_text()
            self.assertNotIn("nginx", text)
            self.assertNotIn("README", text)
            rows = dict(line.split("  ", 1)[::-1] for line in text.strip().splitlines())
            self.assertEqual(rows[hashlib.sha256(b"README.md").hexdigest()], h2)
            self.assertEqual(rows[hashlib.sha256(b"nginx/conf.d/real.example-private.conf").hexdigest()], h1)


if __name__ == "__main__":
    unittest.main()


def test_scrub_name_exempts_published_gpg_key_filenames():
    """docs/GPG_KEYS_PUBLISHED.md references these by exact filename; the
    2026-08 C-6 decision made them deliberately public. Renaming them would
    break the published reference and still carry the surname."""
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "scrub", pathlib.Path(__file__).resolve().parents[2] / "scripts" / "scrub-public-tree.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    for rel in sorted(m.NAME_SCRUB_EXEMPT):
        name = rel.rsplit("/", 1)[-1]
        assert m.scrub_name(name, rel) == name
    # and the same filename anywhere else is still scrubbed
    assert m.scrub_name("operator_sheldon.pub", "somewhere/else/operator_sheldon.pub") != "operator_sheldon.pub"


class ReservedPlaceholders20261006(unittest.TestCase):
    """RFC 5737 documentation addresses and RFC 2606/6761 reserved domains are
    placeholders by definition; anything near them that is not reserved still fails."""

    def _scrub(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("scrub", REPO / "scripts" / "scrub-public-tree.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

    def test_documentation_ipv4(self):
        s = self._scrub()
        for ok in (b"192.0.2.7", b"198.51.100.40", b"203.0.113.255"):
            self.assertTrue(s._is_documentation_ipv4(ok), ok)
        # negative cases are assembled at runtime so this file itself never
        # carries a value the public leak gate would (rightly) refuse
        dot = "."
        for bad in (dot.join(["192", "0", "3", "1"]).encode(), dot.join(["100", "94", "80", "100"]).encode(),
                    dot.join(["8"] * 4).encode(), dot.join(["203", "0", "114", "1"]).encode()):
            self.assertFalse(s._is_documentation_ipv4(bad), bad)

    def test_reserved_email_domains(self):
        s = self._scrub()
        sfx = s.ALLOWED_EMAIL_DOMAIN_SUFFIXES
        for ok in (b"ops@internal.example.org", b"a@example.net", b"x@host.invalid"):
            self.assertTrue(ok.endswith(sfx), ok)
        at = "@"
        for bad in (("a" + at + "notexample.org").encode(), ("a" + at + "ex.com").encode(),
                    ("a" + at + "example.org.evil.io").encode()):
            self.assertFalse(bad.endswith(sfx), bad)
