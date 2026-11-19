#!/usr/bin/env python3
"""scripts/build/apt-snapshot-guard.py -- watch the Debian snapshot pin's expiry and prepare the next
pin before it lapses (2026-11-18, operator).

Why: every local image pins `ARG APT_SNAPSHOT=...` (docs/REPRODUCIBLE_BUILDS.md). The suites that
carry a Valid-Until (trixie-updates, trixie-security) expire about 7 days after the snapshot. On
2026-10-21 and again on 2026-11-18 an expired pin stopped a rollout mid-way (11-18: at
ingest-notam, every later unit not restarted). relay-preflight.sh check 4 catches an old pin, but
only when a relay calls it.

What it does (it PREPARES and ALERTS; it never signs, commits or pushes -- the operator does):
  run (timer, every 6 h)
    1. read the pin from the Containerfiles; fetch each suite's InRelease at that snapshot from
       snapshot.debian.org; earliest Valid-Until = the pin's expiry. State JSON in STATE_DIR.
    2. expiry within WARN_HOURS (48): one ops-health alert (priority 3).
    3. expiry within PREFETCH_HOURS (24): pick a candidate snapshot (now, to the hour), check each
       suite is signed by the Debian archive keyring (gpgv) and valid for MIN_VALID_DAYS (5), then in
       a disposable clone of the checkout move the pin with pin-base-images.py and drop a
       hash-pinned patch + a ready relay in /srv/ctdc-handoff/aptsnap-<candidate>-from-<user>/.
       Alert priority 4 naming the relay; priority 5 at URGENT_HOURS (6) and once expired.
       Idempotent: one prepared relay per pin. A candidate that fails a check drops nothing and
       alerts with the reason.
    Network failure: the state says "unverified" and the last known expiry (same pin) still drives
    the thresholds; nothing is blocked.
  --check-rollout   exit 2 when the pin's expiry is past or within ROLLOUT_REFUSE_HOURS (2); prints
                    the prepared relay. Unverifiable (network down, no cache) -> warning, exit 0.
  --status          print the state.

Test seams (all optional): APT_GUARD_NOW (epoch), APT_GUARD_FIXTURES (dir: <snapshot>/<suite>.InRelease
instead of the network), APT_GUARD_GPGV (path, or "skip"), APT_GUARD_KEYRING, APT_GUARD_NTFY_LOG
(append alerts to this file instead of sending), APT_GUARD_REPO, APT_GUARD_STATE_DIR,
APT_GUARD_DROP_DIR, APT_GUARD_PODMAN ("skip").
ASCII output only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("APT_GUARD_REPO") or HERE.parent.parent)
STATE_DIR = Path(os.environ.get("APT_GUARD_STATE_DIR", "/var/lib/corporatetraveldc/apt-snapshot-guard"))
DROP_DIR = Path(os.environ.get("APT_GUARD_DROP_DIR", "/srv/ctdc-handoff"))
ENV_FILE = Path("/etc/corporatetraveldc/dispatch.env")
SECRETS_FILE = Path("/etc/corporatetraveldc/dispatch-secrets.env")
SNAPSHOT_BASE = "https://snapshot.debian.org"
# (archive, suite): the suites the images' apt-get update reads (trixie base image)
SUITES = [("debian", "trixie"), ("debian", "trixie-updates"), ("debian-security", "trixie-security")]
PIN_RE = re.compile(r"^ARG APT_SNAPSHOT=(\d{8}T\d{6}Z)$", re.M)
FROM_RE = re.compile(r"^FROM (docker\.io/library/python:[^@\s]+@sha256:[0-9a-f]{64})", re.M)
DEFAULT_KEYRINGS = [Path("/usr/share/keyrings/debian-archive-keyring.gpg")]


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


WARN_HOURS = env_int("APT_GUARD_WARN_HOURS", 48)
PREFETCH_HOURS = env_int("APT_GUARD_PREFETCH_HOURS", 24)
URGENT_HOURS = env_int("APT_GUARD_URGENT_HOURS", 6)
REFUSE_HOURS = env_int("APT_GUARD_ROLLOUT_REFUSE_HOURS", 2)
MIN_VALID_DAYS = env_int("APT_GUARD_MIN_VALID_DAYS", 5)


def now_epoch() -> int:
    v = os.environ.get("APT_GUARD_NOW")
    return int(v) if v else int(dt.datetime.now(dt.timezone.utc).timestamp())


def iso(epoch: int | None) -> str | None:
    if epoch is None:
        return None
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def log(msg: str) -> None:
    line = f"[{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "guard.log", "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# --- pins ----------------------------------------------------------------------------------------
def containerfiles(repo: Path) -> list[Path]:
    return sorted(p for p in repo.glob("Containerfile*") if p.is_file())


def current_pins(repo: Path) -> dict[str, str]:
    out = {}
    for cf in containerfiles(repo):
        m = PIN_RE.search(cf.read_text())
        if m:
            out[cf.name] = m.group(1)
    return out


# --- fetch + parse -------------------------------------------------------------------------------
class FetchError(Exception):
    pass


def fetch_inrelease(snapshot: str, archive: str, suite: str) -> str:
    fx = os.environ.get("APT_GUARD_FIXTURES")
    if fx:
        p = Path(fx) / snapshot / f"{suite}.InRelease"
        if not p.is_file():
            raise FetchError(f"{suite}: no fixture")
        return p.read_text()
    url = f"{SNAPSHOT_BASE}/archive/{archive}/{snapshot}/dists/{suite}/InRelease"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 (fixed https host)
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FetchError(f"{suite}: {type(exc).__name__}") from None


def header_epoch(text: str, field: str) -> int | None:
    m = re.search(rf"^{field}: (.+)$", text, re.M)
    if not m:
        return None
    try:
        return int(email.utils.parsedate_to_datetime(m.group(1).strip()).timestamp())
    except (TypeError, ValueError):
        return None


def keyring() -> Path | None:
    k = os.environ.get("APT_GUARD_KEYRING")
    if k:
        return Path(k) if Path(k).is_file() else None
    for p in DEFAULT_KEYRINGS + [STATE_DIR / "debian-archive-keyring.gpg"]:
        if p.is_file():
            return p
    return extract_keyring()


def extract_keyring() -> Path | None:
    """Copy debian-archive-keyring.gpg out of the pinned (digest-verified) base image, if it is
    already in local storage. Never pulls."""
    if os.environ.get("APT_GUARD_PODMAN") == "skip" or not shutil.which("podman"):
        return None
    ref = None
    for cf in containerfiles(REPO):
        m = FROM_RE.search(cf.read_text())
        if m:
            ref = m.group(1)
            break
    if not ref:
        return None
    try:
        if subprocess.run(["podman", "image", "exists", ref], capture_output=True, timeout=30).returncode != 0:
            return None
        r = subprocess.run(["podman", "run", "--rm", "--network=none", "--entrypoint", "cat", ref,
                            "/usr/share/keyrings/debian-archive-keyring.gpg"], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    dest = STATE_DIR / "debian-archive-keyring.gpg"
    dest.write_bytes(r.stdout)
    log(f"keyring: extracted debian-archive-keyring.gpg from the pinned base image ({ref.split('@')[0]})")
    return dest


def check_signature(text: str) -> str:
    gpgv = os.environ.get("APT_GUARD_GPGV") or shutil.which("gpgv")
    if not gpgv or gpgv == "skip":
        return "not-checked"
    kr = keyring()
    if kr is None:
        return "not-checked"
    with tempfile.NamedTemporaryFile("w", suffix=".InRelease", delete=False) as fh:
        fh.write(text)
        tmp = fh.name
    try:
        r = subprocess.run([gpgv, "--keyring", str(kr), tmp], capture_output=True, timeout=60)
        return "verified" if r.returncode == 0 else "failed"
    except (OSError, subprocess.SubprocessError):
        return "failed"
    finally:
        os.unlink(tmp)


def inspect_snapshot(snapshot: str, verify_sig: bool) -> dict:
    """Earliest Valid-Until across the suites, per-suite detail; raises FetchError."""
    suites = {}
    earliest = None
    for archive, suite in SUITES:
        text = fetch_inrelease(snapshot, archive, suite)
        vu = header_epoch(text, "Valid-Until")
        d = {"date": iso(header_epoch(text, "Date")), "valid_until": iso(vu)}
        if verify_sig:
            d["signature"] = check_signature(text)
        suites[suite] = d
        if vu is not None and (earliest is None or vu < earliest):
            earliest = vu
    return {"snapshot": snapshot, "expiry": earliest, "suites": suites}


# --- state + alerts ------------------------------------------------------------------------------
def load_state() -> dict:
    try:
        return json.loads((STATE_DIR / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def save_state(st: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_DIR / "state.json.new"
    tmp.write_text(json.dumps(st, indent=2, sort_keys=True) + "\n")
    tmp.replace(STATE_DIR / "state.json")


def read_env_var(name: str, path: Path) -> str:
    try:
        for line in path.read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1]
    except OSError:
        pass
    return ""


def alert(st: dict, key: str, title: str, msg: str, priority: int) -> None:
    sent = st.setdefault("alerts_sent", [])
    if key in sent:
        return
    sent.append(key)
    del sent[:-40]
    sink = os.environ.get("APT_GUARD_NTFY_LOG")
    if sink:
        with open(sink, "a") as fh:
            fh.write(json.dumps({"key": key, "title": title, "priority": priority, "msg": msg}) + "\n")
        log(f"alert ({priority}) {title}")
        return
    base = read_env_var("NTFY_BASE_URL", ENV_FILE) or "http://127.0.0.1:2586"
    topic = read_env_var("NTFY_OPS_TOPIC", ENV_FILE) or "ops-health"
    token = read_env_var("NTFY_TOKEN", SECRETS_FILE).split(":", 1)[0]
    req = urllib.request.Request(f"{base}/{topic}", data=msg.encode(), method="POST",
                                 headers={"Title": title, "Priority": str(priority), "Tags": "package"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        urllib.request.urlopen(req, timeout=5).close()  # noqa: S310 (operator's own ntfy)
        log(f"alert ({priority}) {title}")
    except (urllib.error.URLError, OSError):
        log(f"warn: ntfy send failed (token_set={'yes' if token else 'no'})")


# --- prefetch ------------------------------------------------------------------------------------
def candidate_snapshot(now: int) -> str:
    t = dt.datetime.fromtimestamp(now, dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    return t.strftime("%Y%m%dT%H%M%SZ")


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=cwd, check=True,
                          capture_output=True, text=True, timeout=300).stdout


def prepare(st: dict, pin: str, pin_expiry: int, now: int) -> tuple[str | None, str]:
    """Returns (relay path or None, reason)."""
    done = st.get("prepared", {}).get(pin)
    if done and Path(done["relay"]).is_file():
        return done["relay"], "already prepared"
    cand = candidate_snapshot(now)
    if cand <= pin:
        return None, f"candidate {cand} is not newer than the pin"
    try:
        info = inspect_snapshot(cand, verify_sig=True)
    except FetchError as exc:
        return None, f"candidate {cand} not fetchable ({exc})"
    bad = [s for s, d in info["suites"].items() if d.get("signature") == "failed"]
    if bad:
        return None, f"candidate {cand}: signature FAILED for {', '.join(bad)}"
    if info["expiry"] is None or info["expiry"] < now + MIN_VALID_DAYS * 86400:
        return None, f"candidate {cand}: valid only to {iso(info['expiry'])} (< {MIN_VALID_DAYS} days)"
    if info["expiry"] <= pin_expiry:
        return None, (f"candidate {cand}: valid to {iso(info['expiry'])}, no later than the pin "
                      "(Debian has not published newer Release files yet)")
    sig = sorted({d.get("signature", "not-checked") for d in info["suites"].values()})
    work = STATE_DIR / "work"
    shutil.rmtree(work, ignore_errors=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        git("clone", "-q", "--no-hardlinks", str(REPO), str(work), cwd=STATE_DIR)
        base = git("rev-parse", "HEAD", cwd=work).strip()
        subprocess.run([sys.executable, str(work / "scripts/build/pin-base-images.py"), "--apt-snapshot", cand],
                       cwd=work, check=True, capture_output=True, text=True, timeout=120)
        diff = git("diff", cwd=work)
    except (subprocess.SubprocessError, OSError) as exc:
        shutil.rmtree(work, ignore_errors=True)
        return None, f"candidate {cand}: preparing the patch failed ({type(exc).__name__})"
    changed = [ln for ln in diff.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]
    stray = [ln for ln in changed if not re.match(r"^[-+]ARG APT_SNAPSHOT=\d{8}T\d{6}Z$", ln)]
    count = sum(1 for ln in changed if ln == f"+ARG APT_SNAPSHOT={cand}")
    if stray or count == 0:
        shutil.rmtree(work, ignore_errors=True)
        return None, f"candidate {cand}: the pin tool changed more than APT_SNAPSHOT lines ({len(stray)})"
    user = os.environ.get("USER") or "corporatetraveldc"
    out = DROP_DIR / f"aptsnap-{cand}-from-{user}"
    out.mkdir(parents=True, exist_ok=True)
    patch = out / f"aptsnap-{cand}.patch"
    patch.write_text(diff)
    sha = hashlib.sha256(patch.read_bytes()).hexdigest()
    tpl = (HERE / "aptsnap-relay.template").read_text()
    relay_text = (tpl.replace("@@GENERATED@@", iso(now)).replace("@@OLD@@", pin).replace("@@NEW@@", cand)
                  .replace("@@OLD_EXPIRY@@", iso(pin_expiry) or "unknown")
                  .replace("@@NEW_EXPIRY@@", iso(info["expiry"])).replace("@@BASE@@", base)
                  .replace("@@PATCH@@", str(patch)).replace("@@SHA@@", sha).replace("@@COUNT@@", str(count))
                  .replace("@@SIGNATURE@@", "/".join(sig)))
    relay = out / "aptsnap.relay"
    relay.write_text(relay_text)
    for p in (patch, relay):
        p.chmod(0o644)
    shutil.rmtree(work, ignore_errors=True)
    st.setdefault("prepared", {})[pin] = {"candidate": cand, "relay": str(relay), "patch_sha256": sha,
                                          "base": base, "candidate_expiry": iso(info["expiry"]),
                                          "signature": "/".join(sig), "prepared_at": iso(now)}
    return str(relay), f"prepared {cand} (valid to {iso(info['expiry'])}, signature {'/'.join(sig)})"


# --- modes ---------------------------------------------------------------------------------------
def evaluate(st: dict, now: int) -> tuple[str | None, int | None, str]:
    pins = current_pins(REPO)
    values = sorted(set(pins.values()))
    if not values:
        return None, None, "no-pin"
    pin = values[0]                      # the oldest pin governs
    try:
        info = inspect_snapshot(pin, verify_sig=False)
        st.update({"pin": pin, "expiry": iso(info["expiry"]), "expiry_epoch": info["expiry"],
                   "suites": info["suites"], "verified": True, "checked_at": iso(now)})
        return pin, info["expiry"], "verified"
    except FetchError as exc:
        cached = st.get("expiry_epoch") if st.get("pin") == pin else None
        st.update({"pin": pin, "verified": False, "checked_at": iso(now), "fetch_error": str(exc)})
        if cached is None:
            st.pop("expiry_epoch", None)
            st["expiry"] = None
        return pin, cached, "unverified"


def run() -> int:
    now = now_epoch()
    st = load_state()
    pin, expiry, how = evaluate(st, now)
    if pin is None:
        log("no ARG APT_SNAPSHOT found in the Containerfiles")
        save_state(st)
        return 1
    if expiry is None:
        log(f"pin {pin}: expiry unknown ({how}); nothing decided")
        save_state(st)
        return 0
    left_h = (expiry - now) / 3600
    log(f"pin {pin}: earliest Valid-Until {iso(expiry)} ({left_h:.1f} h left, {how})")
    if left_h <= PREFETCH_HOURS:
        relay, reason = prepare(st, pin, expiry, now)
        log(f"prefetch: {reason}")
        st["prefetch_result"] = reason
        if relay is None:
            alert(st, f"{pin}:prefetch-failed:{reason}", "Debian snapshot: next pin NOT prepared",
                  f"Pin {pin} expires {iso(expiry)}. Preparing the next pin failed: {reason}. "
                  "Run pin-base-images.py --apt-snapshot now by hand before the next rollout.", 4)
        level = "expired" if left_h <= 0 else ("urgent" if left_h <= URGENT_HOURS else "prefetch")
        prio = 5 if level in ("expired", "urgent") else 4
        when = "has EXPIRED" if level == "expired" else f"expires {iso(expiry)}"
        alert(st, f"{pin}:{level}", f"Debian snapshot pin {when}",
              f"Pin {pin} {when}; rollouts will fail to build. "
              + (f"Ready to run: bash {relay}" if relay else "No relay prepared; see the log."), prio)
    elif left_h <= WARN_HOURS:
        alert(st, f"{pin}:warn", "Debian snapshot pin expires within 48 h",
              f"Pin {pin} expires {iso(expiry)}. The next pin is prepared automatically at "
              f"{PREFETCH_HOURS} h before expiry.", 3)
    save_state(st)
    return 0


def check_rollout() -> int:
    now = now_epoch()
    st = load_state()
    pin, expiry, how = evaluate(st, now)
    try:
        save_state(st)
    except OSError:
        pass
    if pin is None or expiry is None:
        print(f"apt-snapshot-guard: WARNING: could not verify the Debian snapshot pin ({how}); not blocking")
        return 0
    left_h = (expiry - now) / 3600
    if left_h <= REFUSE_HOURS:
        state = "has EXPIRED" if left_h <= 0 else f"expires in {left_h:.1f} h"
        print(f"apt-snapshot-guard: REFUSING to build: Debian snapshot pin {pin} {state} ({iso(expiry)}).")
        prep = st.get("prepared", {}).get(pin)
        if prep:
            print(f"  prepared next pin {prep['candidate']}: bash {prep['relay']}")
        else:
            print("  move it first: python3 scripts/build/pin-base-images.py --apt-snapshot now  (review, sign, then roll out)")
        print("  override (not recommended): ALLOW_EXPIRED_SNAPSHOT=1")
        return 2
    print(f"apt-snapshot-guard: pin {pin} valid to {iso(expiry)} ({left_h:.1f} h, {how})")
    return 0


def status() -> int:
    print(json.dumps(load_state(), indent=2, sort_keys=True))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check-rollout", action="store_true")
    g.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    if a.status:
        return status()
    if a.check_rollout:
        return check_rollout()
    return run()


if __name__ == "__main__":
    sys.exit(main())
