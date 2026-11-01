"""One installed platform context for native and authenticated connector readers."""
import hashlib
import os
from pathlib import Path
import re
import stat

RESOURCE = "platform-context"
CONTEXT_PATH = Path("/srv/ctdc-context/PLATFORM_CONTEXT.md")
ROOT = Path(__file__).resolve().parents[2]
SOURCE_REL = "config/platform-context.md"
MAX_BYTES = 1 << 20


class ContextUnavailable(RuntimeError):
    pass


def _read_regular(path: Path, *, owners=None) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            st = os.fstat(handle.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
                raise ContextUnavailable("shared context is not a bounded regular file")
            if st.st_mode & 0o022:
                raise ContextUnavailable("shared context must not be group/world writable")
            if owners is not None and st.st_uid not in owners:
                raise ContextUnavailable("shared context has an unexpected owner")
            data = handle.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ContextUnavailable("shared context exceeds its size limit")
            return data
    except OSError as exc:
        raise ContextUnavailable("shared context file unavailable") from exc


def _source_digest(root: Path) -> str:
    try:
        matches = [line.split("  ", 1)[0]
                   for line in (root / "MANIFEST.sha256").read_text().splitlines()
                   if line.endswith("  " + SOURCE_REL)]
    except OSError as exc:
        raise ContextUnavailable("shared context manifest unavailable") from exc
    if len(matches) != 1 or not re.fullmatch(r'[0-9a-f]{64}', matches[0]):
        raise ContextUnavailable("shared context manifest entry unavailable")
    return matches[0]


def signed_source(root: Path = ROOT) -> bytes:
    """Bind content to its manifest entry; signature validation is the native
    install/web-startup gate. This reader never invokes a signing/key agent.
    """
    data = _read_regular(root / SOURCE_REL)
    if _source_digest(root) != hashlib.sha256(data).hexdigest():
        raise ContextUnavailable("shared context source differs from its manifest")
    return data


def read_context(path: Path = CONTEXT_PATH, *, root: Path = ROOT,
                 owners=frozenset({0, 1000}), require_source: bool = True) -> dict:
    if path.parent.is_symlink():
        raise ContextUnavailable("shared context directory must not be a symlink")
    data = _read_regular(path, owners=owners)
    # The web image carries the signed manifest, without a second context
    # copy. Host readers also verify the versioned source file itself.
    expected = hashlib.sha256(signed_source(root)).hexdigest() if require_source else _source_digest(root)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ContextUnavailable("installed shared context differs from signed source")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContextUnavailable("shared context is not UTF-8") from exc
    return {"resource": RESOURCE, "source_kind": "shared_file", "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(), "content": content}
