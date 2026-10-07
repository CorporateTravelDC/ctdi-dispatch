"""common.board_sign -- identity-based board signing (2026-10-04).

Second authentication path for the message board, alongside X-Board-Key
(the master BOARD_KEY and minted scoped tokens, which stay exactly as they
are for platform/API clients). This path has NO shared secret: the poster
signs the request with its own SSH ed25519 key -- one key per account, comment
``<account>@corporatetraveldc-dispatch`` (docs/AGENT_SEGMENTATION.md) -- and
the server verifies against the ``board_signers`` registry (account ->
public key, active flag). Attribution is the account name, so a post is
"signed by ctdc-agent", never "signed under the cowork key".

Wire format
-----------
Headers on the request:
  X-Board-Signer:     <account>
  X-Board-Timestamp:  <unix seconds, integer>
  X-Board-Signature:  <base64 of the armored ``ssh-keygen -Y sign`` output>
Canonical message that is signed (bytes, exactly):
  "<METHOD>\\n<PATH>[?<raw query>]\\n<unix-ts>\\n<sha256-hex(body)>"
  (the query is signed since 2026-10-04, U6; each signature is accepted once)
Signature namespace (``ssh-keygen -Y sign -n``): ``corporatetraveldc-board``.
Replay window: |now - ts| <= REPLAY_WINDOW_S (300 s).

Verification backend
--------------------
The SSHSIG blob produced by ``ssh-keygen -Y sign`` (PROTOCOL.sshsig) is
parsed here and the ed25519 signature is verified with the ``cryptography``
package (added to requirements.txt for the web image; neither ``cryptography``
nor ``ssh-keygen`` existed in the web image before this change, so shelling
out to ``ssh-keygen -Y verify`` was not an option there). On a host that
lacks ``cryptography`` but has ``ssh-keygen`` (the Pi itself), the
``ssh-keygen -Y verify`` fallback is used so the client script can self-test.

SSHSIG layout (all SSH ``string`` = uint32 length + bytes):
  "SSHSIG" | uint32 version=1 | string publickey | string namespace |
  string reserved | string hash_algorithm | string signature
  signed data = "SSHSIG" | string namespace | string reserved |
                string hash_algorithm | string H(message)
  signature  = string "ssh-ed25519" | string <64-byte sig>
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import struct
import subprocess
import tempfile
import time

NAMESPACE = "corporatetraveldc-board"
REPLAY_WINDOW_S = 300
_MAGIC = b"SSHSIG"
_ARMOR_BEGIN = "-----BEGIN SSH SIGNATURE-----"
_ARMOR_END = "-----END SSH SIGNATURE-----"


class BoardSignError(ValueError):
    """Any structural or cryptographic failure. Message is safe to log."""


def canonical_message(method: str, path: str, ts: int, body: bytes, query: str = "") -> bytes:
    """The exact bytes both sides sign/verify.

    2026-10-04 (duel M10 / U6): the RAW query string is part of the signed
    target ("<PATH>?<query>" when a query is present, byte-for-byte as sent).
    Before, only the path was signed, so a signed
    GET /api/v1/vault/research?path=A could be replayed within the window as
    ?path=B -- a read of any other in-scope file under the same signature."""
    target = f"{path}?{query}" if query else path
    return "\n".join([method.upper(), target, str(int(ts)),
                      hashlib.sha256(body or b"").hexdigest()]).encode()


class ReplayCache:
    """Seen-signature LRU (U6): a signature is accepted ONCE inside the
    replay window. Keyed on sha256 of the signature header value; entries
    expire after `window` seconds (the timestamp check already refuses
    anything older), capped at `max_entries` (oldest evicted first).
    In-process: the web app runs one uvicorn worker (src/web/serve.py)."""

    def __init__(self, window: int = REPLAY_WINDOW_S, max_entries: int = 50000):
        from collections import OrderedDict
        self.window = window
        self.max_entries = max_entries
        self._seen: "OrderedDict[str, float]" = OrderedDict()

    def check_and_add(self, signature_b64: str, now: float | None = None) -> bool:
        """True if fresh (and records it); False if already seen in-window."""
        now = time.time() if now is None else now
        cutoff = now - 2 * self.window   # ts may be up to `window` in the future
        while self._seen:
            k, t = next(iter(self._seen.items()))
            if t >= cutoff:
                break
            self._seen.popitem(last=False)
        key = hashlib.sha256(signature_b64.encode("ascii", "replace")).hexdigest()
        if key in self._seen:
            return False
        self._seen[key] = now
        while len(self._seen) > self.max_entries:
            self._seen.popitem(last=False)
        return True


# -- SSH wire helpers --------------------------------------------------------

def _rd_string(buf: bytes, off: int) -> tuple[bytes, int]:
    if off + 4 > len(buf):
        raise BoardSignError("truncated SSHSIG")
    (n,) = struct.unpack(">I", buf[off:off + 4])
    off += 4
    if off + n > len(buf):
        raise BoardSignError("truncated SSHSIG string")
    return buf[off:off + n], off + n


def _wr_string(b: bytes) -> bytes:
    return struct.pack(">I", len(b)) + b


def unarmor(sig_text: str) -> bytes:
    lines = [ln.strip() for ln in sig_text.strip().splitlines()]
    if not lines or lines[0] != _ARMOR_BEGIN or lines[-1] != _ARMOR_END:
        raise BoardSignError("not an armored SSH signature")
    try:
        return base64.b64decode("".join(lines[1:-1]), validate=True)
    except Exception as e:  # noqa: BLE001
        raise BoardSignError(f"bad armor base64: {type(e).__name__}") from None


def parse_sshsig(blob: bytes) -> dict:
    """Decode an SSHSIG blob into its fields (bytes), validating structure."""
    if not blob.startswith(_MAGIC):
        raise BoardSignError("missing SSHSIG magic")
    off = len(_MAGIC)
    if off + 4 > len(blob):
        raise BoardSignError("truncated SSHSIG header")
    (version,) = struct.unpack(">I", blob[off:off + 4])
    off += 4
    if version != 1:
        raise BoardSignError(f"unsupported SSHSIG version {version}")
    pubkey, off = _rd_string(blob, off)
    namespace, off = _rd_string(blob, off)
    reserved, off = _rd_string(blob, off)
    hash_alg, off = _rd_string(blob, off)
    signature, off = _rd_string(blob, off)
    sig_type, soff = _rd_string(signature, 0)
    sig_bytes, _ = _rd_string(signature, soff)
    key_type, koff = _rd_string(pubkey, 0)
    key_bytes, _ = _rd_string(pubkey, koff)
    return {"pubkey_blob": pubkey, "key_type": key_type, "key_bytes": key_bytes,
            "namespace": namespace, "reserved": reserved, "hash_alg": hash_alg,
            "sig_type": sig_type, "sig_bytes": sig_bytes}


def pubkey_line_to_blob(pubkey_line: str) -> tuple[str, bytes, str]:
    """'ssh-ed25519 AAAA... comment' -> (type, raw blob, comment)."""
    parts = pubkey_line.strip().split()
    if len(parts) < 2:
        raise BoardSignError("malformed public key line")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except Exception as e:  # noqa: BLE001
        raise BoardSignError(f"bad public key base64: {type(e).__name__}") from None
    return parts[0], blob, (" ".join(parts[2:]) if len(parts) > 2 else "")


def _signed_data(namespace: bytes, reserved: bytes, hash_alg: bytes, message: bytes) -> bytes:
    alg = hash_alg.decode("ascii", "replace")
    if alg == "sha512":
        h = hashlib.sha512(message).digest()
    elif alg == "sha256":
        h = hashlib.sha256(message).digest()
    else:
        raise BoardSignError(f"unsupported hash algorithm {alg!r}")
    return _MAGIC + _wr_string(namespace) + _wr_string(reserved) + _wr_string(hash_alg) + _wr_string(h)


def _verify_with_cryptography(sig: dict, message: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if sig["key_type"] != b"ssh-ed25519" or sig["sig_type"] != b"ssh-ed25519":
        raise BoardSignError("only ssh-ed25519 keys are accepted")
    if len(sig["key_bytes"]) != 32 or len(sig["sig_bytes"]) != 64:
        raise BoardSignError("malformed ed25519 key/signature length")
    data = _signed_data(sig["namespace"], sig["reserved"], sig["hash_alg"], message)
    try:
        Ed25519PublicKey.from_public_bytes(sig["key_bytes"]).verify(sig["sig_bytes"], data)
        return True
    except InvalidSignature:
        return False


def _verify_with_ssh_keygen(pubkey_line: str, armored: str, message: bytes, signer: str,
                            namespace: str = NAMESPACE) -> bool:
    exe = shutil.which("ssh-keygen")
    if not exe:
        raise BoardSignError("no verification backend (cryptography missing, ssh-keygen missing)")
    with tempfile.TemporaryDirectory() as td:
        allowed = os.path.join(td, "allowed_signers")
        sigf = os.path.join(td, "msg.sig")
        ktype, _, _ = pubkey_line_to_blob(pubkey_line)
        with open(allowed, "w") as fh:
            fh.write(f"{signer} {ktype} {pubkey_line.split()[1]}\n")
        with open(sigf, "w") as fh:
            fh.write(armored if armored.endswith("\n") else armored + "\n")
        r = subprocess.run([exe, "-Y", "verify", "-f", allowed, "-I", signer, "-n", namespace,
                            "-s", sigf], input=message, capture_output=True, timeout=10)
        return r.returncode == 0


KILL_NAMESPACE = "corporatetraveldc-kill"   # scripts/kill-order.sh (2026-10-04)


def verify(pubkey_line: str, signature_b64: str, message: bytes, signer: str = "signer",
           namespace: str = NAMESPACE) -> bool:
    """True iff signature_b64 (base64 of the armored ssh-keygen -Y sign output)
    is a valid signature over `message` by the key in `pubkey_line`, in the
    board namespace. Raises BoardSignError on structural problems; returns
    False on a plain bad signature."""
    try:
        armored = base64.b64decode(signature_b64, validate=True).decode("ascii")
    except Exception as e:  # noqa: BLE001
        raise BoardSignError(f"signature header is not base64 armor: {type(e).__name__}") from None
    sig = parse_sshsig(unarmor(armored))
    if sig["namespace"] != namespace.encode():
        raise BoardSignError("wrong signature namespace")
    _, reg_blob, _ = pubkey_line_to_blob(pubkey_line)
    if sig["pubkey_blob"] != reg_blob:
        # the key embedded in the signature must be the registered one
        return False
    try:
        import cryptography  # noqa: F401
        return _verify_with_cryptography(sig, message)
    except ImportError:
        return _verify_with_ssh_keygen(pubkey_line, armored, message, signer, namespace)


def check_timestamp(ts_header: str, now: float | None = None) -> int:
    try:
        ts = int(ts_header)
    except (TypeError, ValueError):
        raise BoardSignError("X-Board-Timestamp must be integer unix seconds") from None
    now = time.time() if now is None else now
    if abs(now - ts) > REPLAY_WINDOW_S:
        raise BoardSignError(f"timestamp outside the {REPLAY_WINDOW_S}s replay window")
    return ts


# -- client-side helper (tests + scripts/board-sign.sh parity) ---------------

def sign_with_ssh_keygen(private_key_path: str, message: bytes, namespace: str = NAMESPACE) -> str:
    """Produce the base64(armored SSHSIG) header value using ssh-keygen."""
    exe = shutil.which("ssh-keygen")
    if not exe:
        raise BoardSignError("ssh-keygen not available")
    r = subprocess.run([exe, "-Y", "sign", "-f", private_key_path, "-n", namespace],
                       input=message, capture_output=True, timeout=10)
    if r.returncode != 0:
        raise BoardSignError(f"ssh-keygen sign failed: {r.stderr.decode(errors='replace')[:120]}")
    return base64.b64encode(r.stdout).decode("ascii")
