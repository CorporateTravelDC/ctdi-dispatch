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


class PublicIdentityAllowlist20261024(unittest.TestCase):
    """2026-10-24 (operator): exactly two identities -- the security-contact mailbox
    and the legal company name (2026-10-25: with or without the comma) -- pass the public scrub unchanged; every other
    form (bare company name, abbreviation, any other company-domain mailbox) is
    still scrubbed, and verify_scrubbed() still refuses a non-allowed address.
    Scrub-sensitive negative inputs are assembled at run time so this file never
    carries a literal the public leak gate would (rightly) refuse."""

    EMAIL = "developer@csexecutiveservices.com"
    COMPANY = "CS Executive Services LLC"

    @staticmethod
    def _domain() -> str:
        return "csexecutiveservices" + ".com"

    @classmethod
    def _bare_company(cls) -> str:
        return cls.COMPANY[: -len(" LLC")]

    def test_allowed_identities_survive_the_scrub(self):
        m = _load_scrubber()
        for text in (f"Email **{self.EMAIL}** for reports.",
                     f"Contact {self.EMAIL}.",
                     f"Licensor {self.COMPANY}; see LICENSE.",
                     f"Licensor: {self._bare_company()}, LLC"):
            self.assertEqual(m._apply_substitutions(text.encode()), text.encode(), text)

    def test_every_other_form_is_still_scrubbed(self):
        m = _load_scrubber()
        bare = self._bare_company()
        abbrev = bare[:7]  # "[operator LLC abbreviation]"
        other = "someone-else@" + self._domain()
        lookalike_prefix = "x" + self.EMAIL
        lookalike_suffix = self.EMAIL + ".evil"
        out = m._apply_substitutions(
            f"{bare}; {abbrev}; {other}; {lookalike_prefix}; {lookalike_suffix}; www.{self._domain()}".encode()
        ).decode()
        # ".evil" joined at run time: the expected output is itself email-shaped
        # and off the public leak gate's allowlist, so it must not sit in tracked text
        self.assertEqual(
            out,
            "[operator LLC]; [operator LLC abbreviation]; someone-else@example.com; "
            "xdeveloper@example.com; developer@example.com" + ".evil; www.example.com",
        )
        # 2026-10-25: the comma form is allow-listed; a comma form missing LLC is not
        self.assertEqual(m._apply_substitutions(f"{bare}, LLC".encode()), f"{bare}, LLC".encode())
        self.assertEqual(m._apply_substitutions(f"{bare}, Inc".encode()), b"[operator LLC], Inc")

    def test_allowed_email_is_on_the_verify_allowlist_and_others_are_not(self):
        m = _load_scrubber()
        self.assertIn(self.EMAIL.encode(), m.ALLOWED_EMAILS)
        self.assertNotIn(("someone-else@" + self._domain()).encode(), m.ALLOWED_EMAILS)
        self.assertNotIn(("embargo@" + self._domain()).encode(), m.ALLOWED_EMAILS)
        # the masking used by the forbidden-literal check hides ONLY the exact matches
        masked = m._protect_public_identities(
            f"{self.COMPANY} {self.EMAIL} {self._bare_company()} x{self.EMAIL}".encode())
        self.assertNotIn(self.COMPANY.encode(), masked)
        self.assertIn(self._bare_company().encode(), masked)
        self.assertIn(self._domain().encode(), masked)  # from the look-alike only

    def _verify(self, content: str):
        """Run verify_scrubbed() over a one-file tree in a throwaway repo."""
        m = _load_scrubber()
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)
            Path(d, "SECURITY.md").write_text(content)
            cwd = os.getcwd()
            os.chdir(d)
            try:
                subprocess.run(["git", "add", "SECURITY.md"], check=True)
                tree = subprocess.run(["git", "write-tree"], check=True,
                                      capture_output=True, text=True).stdout.strip()
                m.verify_scrubbed(tree)
            finally:
                os.chdir(cwd)

    def test_verify_scrubbed_passes_allowed_identities(self):
        self._verify(f"Email {self.EMAIL} -- the security contact for {self.COMPANY}.\n")
        self._verify(f"Licensor: {self._bare_company()}, LLC\n")

    def test_verify_scrubbed_still_fails_other_company_addresses(self):
        with self.assertRaises(SystemExit):
            self._verify(f"Email someone-else@{self._domain()}.\n")
        with self.assertRaises(SystemExit):
            self._verify(f"{self._bare_company()} runs this.\n")
        with self.assertRaises(SystemExit):
            self._verify(f"Email x{self.EMAIL}.\n")
