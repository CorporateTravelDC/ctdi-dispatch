#!/usr/bin/env python3
"""scripts/lib/ctdc_oob.py -- the logic of the lockdown out-of-band (OOB) management channel.

Interface-agnostic and configured by ONE strict JSON file (docs/LOCKDOWN_OOB.md;
config/network/oob.example.json is the documented example). Root runs the INSTALLED copy from
scripts/ctdc-oob.sh, which gathers facts (links, routes, byte counters, NetworkManager connections,
tailnet addresses, vnstat) into JSON and EXECUTES the actions printed here; the operator runs the
checkout copy from scripts/oob-pin-build.sh. This module never changes the system itself.

Subcommands (all print to stdout; errors go to stderr with exit 2):
  validate      check a config file (unknown keys are errors)
  shell-config  the few scalar settings the shell needs, as KEY=VALUE lines
  enter / tick / leave   new state + the action list for ctdc-oob.sh
  budgets       per metered candidate: used / remaining / OOB budget, today
  pin-accounts  the signer-table lookups oob-pin-build.sh must make
  pins-build    the pinned-fingerprint file (fingerprints only) + the key material for root
  pins-check    per-account authorized_keys from pinned keys only; the rest is rejected
  pins-mode     file-keys | fido-only, re-derived from the pin file
  sshd-config   the sshd drop-in
  breakglass    verify one GPG-signed command (dedicated keyring, nonce, expiry, replay)
  openpgp-fpr   the v4 fingerprint of an OpenPGP public key file
ASCII output only.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GB = 10 ** 9                          # carrier gigabytes are decimal
FIDO_TYPES = ("sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com")
SSH_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
             "ssh-rsa") + FIDO_TYPES
KNOWN_COMMANDS = ("status", "clear-hold", "restore-network", "end-lockdown")
SOURCES = ("board_signers", "approval_signers", "ssh_pubkey_file", "gpg_asc")
ACCOUNT_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
ROLE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
IFACE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,15}")
RATE_RE = re.compile(r"[0-9]{1,6}(k|m)bit")
HARD_CAP_KBIT = 2048                  # operator 2026-11-18: the OOB link is never faster than 2 Mbit/s each way
FLOOR_KBIT = 256                      # ... and must stay usable at a carrier's ~256 kbit/s post-allowance throttle


class OobError(Exception):
    pass


# -- config -------------------------------------------------------------------

def _obj(d, path, allowed, required=()):
    if not isinstance(d, dict):
        raise OobError(f"{path}: expected an object")
    unknown = set(d) - set(allowed)
    if unknown:
        raise OobError(f"{path}: unknown key(s) {sorted(unknown)}")
    missing = set(required) - set(d)
    if missing:
        raise OobError(f"{path}: missing key(s) {sorted(missing)}")
    return d


def _typed(v, path, typ, check=None, why=""):
    if typ is float and isinstance(v, int) and not isinstance(v, bool):
        v = float(v)
    if not isinstance(v, typ) or (typ is int and isinstance(v, bool)):
        raise OobError(f"{path}: expected {typ.__name__}")
    if check is not None and not check(v):
        raise OobError(f"{path}: {why or 'bad value'}")
    return v


def _str_list(v, path, rx=None):
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise OobError(f"{path}: expected a list of strings")
    if rx is not None:
        for x in v:
            if not rx.fullmatch(x):
                raise OobError(f"{path}: bad entry {x!r}")
    return v


def _parse_utc(s: str) -> float:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


def _utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate(cfg, default_account=None) -> dict:
    """Strict: unknown keys anywhere are errors. Returns the config with defaults filled in."""
    _obj(cfg, "config", {"_comment", "version", "candidates", "never", "on_budget", "cap_rate",
                         "hard_cap_kbit", "throttled_kbit", "floor_kbit",
                         "vpn", "tailscale", "ssh", "pins", "breakglass"}, {"version", "candidates"})
    if cfg["version"] != 1:
        raise OobError("config.version: only version 1 is understood")
    ob = cfg.get("on_budget", "soft")
    # 2026-11-18 (operator): the budget is SOFT by default -- when it is used up the channel stays up at
    # the hard cap and alerts; "cut" (disconnect) is explicit only. "cap" / "alert-only" are the
    # superseded names for soft (the hard cap now applies from the start).
    if ob in ("cap", "alert-only"):
        ob = "soft"
    out = {"never": _str_list(cfg.get("never", []), "config.never"),
           "on_budget": ob, "cap_rate": cfg.get("cap_rate", "2mbit")}
    if out["on_budget"] not in ("soft", "cut"):
        raise OobError("config.on_budget: soft | cut")
    if not RATE_RE.fullmatch(out["cap_rate"]):
        raise OobError("config.cap_rate: like 2mbit or 500kbit")
    # the HARD cap on whatever interface carries the OOB channel (every candidate, satellite included)
    out["hard_cap_kbit"] = _typed(cfg.get("hard_cap_kbit", HARD_CAP_KBIT), "config.hard_cap_kbit", int,
                                  lambda v: 64 <= v <= 1_000_000, "64..1000000")
    # the floor the channel is designed to stay usable at (a carrier's post-allowance throttle) ...
    out["floor_kbit"] = _typed(cfg.get("floor_kbit", FLOOR_KBIT), "config.floor_kbit", int,
                               lambda v: 64 <= v <= out["hard_cap_kbit"], "64..hard_cap_kbit")
    # ... and the shaping rate used once the allowance looks used up, just under the floor, so this box's
    # own queue (where tailscaled has priority) is the bottleneck rather than the carrier's
    out["throttled_kbit"] = _typed(cfg.get("throttled_kbit", int(out["floor_kbit"] * 0.9)), "config.throttled_kbit", int,
                                   lambda v: 64 <= v <= out["floor_kbit"], "64..floor_kbit")
    cands = cfg["candidates"]
    if not isinstance(cands, list) or not cands:
        raise OobError("config.candidates: a non-empty list")
    out["candidates"], names = [], set()
    for i, c in enumerate(cands):
        p = f"config.candidates[{i}]"
        _obj(c, p, {"interface", "connection", "metered", "hard_cap_kbit"})
        if ("interface" in c) == ("connection" in c):
            raise OobError(f"{p}: exactly one of interface / connection")
        if "interface" in c:
            _typed(c["interface"], f"{p}.interface", str, IFACE_RE.fullmatch, "not an interface name")
        else:
            _typed(c["connection"], f"{p}.connection", str, lambda s: 0 < len(s) <= 64 and s.isprintable(), "1-64 printable characters")
        name = c.get("interface") or c.get("connection")
        if name in names:
            raise OobError(f"{p}: {name!r} listed twice")
        if name in out["never"]:
            raise OobError(f"{p}: {name!r} is also in config.never")
        names.add(name)
        cc = {"key": f"c{i}", "interface": c.get("interface"), "connection": c.get("connection"), "metered": None,
              "hard_cap_kbit": _typed(c.get("hard_cap_kbit", out["hard_cap_kbit"]), f"{p}.hard_cap_kbit", int,
                                      lambda v: 64 <= v <= out["hard_cap_kbit"],
                                      "64..config.hard_cap_kbit (a candidate may only LOWER the hard cap)")}
        if "metered" in c:
            m = _obj(c["metered"], f"{p}.metered",
                     {"allowance_gb", "cycle_day", "timezone", "baseline_used_gb", "baseline_at",
                      "baseline_vnstat_month_bytes", "budget_fraction", "cap_rate", "cap_on_leave",
                      "other_usage_gb", "other_usage_at"},
                     {"allowance_gb", "cycle_day", "baseline_used_gb", "baseline_at"})
            mm = {"allowance_gb": _typed(m["allowance_gb"], f"{p}.metered.allowance_gb", float, lambda v: v > 0, "must be > 0"),
                  "cycle_day": _typed(m["cycle_day"], f"{p}.metered.cycle_day", int, lambda v: 1 <= v <= 28, "1-28"),
                  "timezone": m.get("timezone", "UTC"),
                  "baseline_used_gb": _typed(m["baseline_used_gb"], f"{p}.metered.baseline_used_gb", float, lambda v: v >= 0, "must be >= 0"),
                  "baseline_at": _typed(m["baseline_at"], f"{p}.metered.baseline_at", str),
                  "baseline_vnstat_month_bytes": m.get("baseline_vnstat_month_bytes"),
                  "budget_fraction": _typed(m.get("budget_fraction", 0.5), f"{p}.metered.budget_fraction", float, lambda v: 0 < v <= 1, "in (0, 1]"),
                  "cap_rate": m.get("cap_rate", out["cap_rate"]),
                  "cap_on_leave": _typed(m.get("cap_on_leave", True), f"{p}.metered.cap_on_leave", bool),
                  # use of the same allowance this box cannot see (a phone on the same plan), as entered
                  "other_usage_gb": _typed(m.get("other_usage_gb", 0.0), f"{p}.metered.other_usage_gb", float, lambda v: v >= 0, "must be >= 0"),
                  "other_usage_at": m.get("other_usage_at")}
            if mm["other_usage_gb"] > 0 and not mm["other_usage_at"]:
                raise OobError(f"{p}.metered.other_usage_at: required when other_usage_gb > 0")
            if mm["other_usage_at"] is not None:
                try:
                    _parse_utc(_typed(mm["other_usage_at"], f"{p}.metered.other_usage_at", str))
                except ValueError as e:
                    raise OobError(f"{p}.metered.other_usage_at: YYYY-MM-DDTHH:MM:SSZ") from e
            try:
                ZoneInfo(mm["timezone"])
            except (ZoneInfoNotFoundError, ValueError) as e:
                raise OobError(f"{p}.metered.timezone: unknown time zone") from e
            try:
                _parse_utc(mm["baseline_at"])
            except ValueError as e:
                raise OobError(f"{p}.metered.baseline_at: YYYY-MM-DDTHH:MM:SSZ") from e
            if mm["baseline_vnstat_month_bytes"] is not None:
                _typed(mm["baseline_vnstat_month_bytes"], f"{p}.metered.baseline_vnstat_month_bytes", int, lambda v: v >= 0, "must be >= 0")
            if not RATE_RE.fullmatch(mm["cap_rate"]):
                raise OobError(f"{p}.metered.cap_rate: like 2mbit")
            cc["metered"] = mm
        out["candidates"].append(cc)
    # 2026-11-18 (operator): the VPN is configurable, not Tailscale-only. `vpn` is the block; `tailscale` is the
    # superseded name for the same block (accepted, mapped to `vpn`); giving both is an error.
    if "vpn" in cfg and "tailscale" in cfg:
        raise OobError("config: give `vpn` or the superseded `tailscale`, not both")
    vkey = "vpn" if "vpn" in cfg else "tailscale"
    vp = f"config.{vkey}"
    ts = _obj(cfg.get(vkey, {}), vp, {"service", "table", "rule_priority", "mark", "mask", "port", "protocol",
                                      "interface", "relay_tcp_port"})
    proto = _typed(ts.get("protocol", "udp"), f"{vp}.protocol", str, lambda v: v in ("udp", "tcp"), "udp or tcp")
    port = ts.get("port", 41641)
    if port is not None:
        _typed(port, f"{vp}.port", int, lambda v: 1 <= v <= 65535, "1..65535 or null")
    relay = ts.get("relay_tcp_port", 443)
    if relay is not None:
        _typed(relay, f"{vp}.relay_tcp_port", int, lambda v: 1 <= v <= 65535, "1..65535 or null")
    if proto == "tcp" and port is not None and relay == port:
        raise OobError(f"{vp}.relay_tcp_port: must differ from port when protocol is tcp")
    out["vpn"] = {
        "service": _typed(ts.get("service", "tailscaled.service"), f"{vp}.service", str,
                          re.compile(r"[A-Za-z0-9@_.-]{1,64}\.service").fullmatch, "a unit name"),
        "table": _typed(ts.get("table", 3313), f"{vp}.table", int, lambda v: 256 <= v < 2 ** 31, "256..2^31"),
        "rule_priority": _typed(ts.get("rule_priority", 5200), f"{vp}.rule_priority", int, lambda v: 1 <= v < 5210, "1..5209 (ahead of tailscale's own rules)"),
        "mark": _typed(ts.get("mark", "0xcdc"), f"{vp}.mark", str, re.compile(r"0x[0-9a-f]{1,8}").fullmatch, "hex"),
        "mask": _typed(ts.get("mask", "0xfff"), f"{vp}.mask", str, re.compile(r"0x[0-9a-f]{1,8}").fullmatch, "hex"),
        # the VPN's own transport port: inbound packets to it get the priority tin on the OOB link (null: no match)
        "port": port,
        "protocol": proto,
        # the tunnel interface sshd listens on (its global addresses); default Tailscale's
        "interface": _typed(ts.get("interface", "tailscale0"), f"{vp}.interface", str,
                            re.compile(r"[A-Za-z0-9_.:-]{1,15}").fullmatch, "an interface name (1-15 chars)"),
        # the VPN's relay fallback over TCP (Tailscale DERP: 443) -> Best Effort tin (null: no match)
        "relay_tcp_port": relay,
    }
    if int(out["vpn"]["mask"], 16) & 0xFF0000:
        raise OobError(f"{vp}.mask: must not overlap tailscale's own 0xff0000 mark bits")
    if int(out["vpn"]["mark"], 16) & ~int(out["vpn"]["mask"], 16):
        raise OobError(f"{vp}.mark: must lie inside the mask")
    ssh = _obj(cfg.get("ssh", {}), "config.ssh", {"enabled", "allowed_accounts", "fido"})
    accts = ssh.get("allowed_accounts", [default_account] if default_account else [])
    out["ssh"] = {"enabled": _typed(ssh.get("enabled", True), "config.ssh.enabled", bool),
                  "allowed_accounts": _str_list(accts, "config.ssh.allowed_accounts", ACCOUNT_RE),
                  "fido": ssh.get("fido", "auto")}
    if out["ssh"]["fido"] not in ("auto", "required", "off"):
        raise OobError("config.ssh.fido: auto | required | off")
    if out["ssh"]["enabled"] and not out["ssh"]["allowed_accounts"]:
        raise OobError("config.ssh.allowed_accounts: at least one account (default: the operator)")
    pins = cfg.get("pins", [])
    if not isinstance(pins, list):
        raise OobError("config.pins: a list")
    out["pins"], roles = [], {}
    for i, s in enumerate(pins):
        p = f"config.pins[{i}]"
        _obj(s, p, {"role", "source", "account", "path", "fingerprint", "optional"}, {"role", "source"})
        role = _typed(s["role"], f"{p}.role", str, ROLE_RE.fullmatch, "[a-z0-9-], at most 32")
        if role in roles:
            raise OobError(f"{p}: role {role!r} given twice")
        src = s["source"]
        if src not in SOURCES:
            raise OobError(f"{p}.source: one of {', '.join(SOURCES)}")
        e = {"role": role, "source": src, "optional": _typed(s.get("optional", False), f"{p}.optional", bool)}
        if src in ("board_signers", "approval_signers", "ssh_pubkey_file"):
            if "account" not in s:
                raise OobError(f"{p}: {src} needs an account")
            e["account"] = _typed(s["account"], f"{p}.account", str, ACCOUNT_RE.fullmatch, "not an account name")
        elif "account" in s:
            raise OobError(f"{p}: a gpg_asc pin has no account")
        if src in ("ssh_pubkey_file", "gpg_asc"):
            if "path" not in s:
                raise OobError(f"{p}: {src} needs a path")
            e["path"] = _typed(s["path"], f"{p}.path", str, lambda v: 0 < len(v) < 256 and ".." not in v.split("/"), "a plain path")
        elif "path" in s:
            raise OobError(f"{p}: {src} takes no path")
        if "fingerprint" in s:
            if src != "gpg_asc":
                raise OobError(f"{p}: only a gpg_asc pin takes an expected fingerprint")
            e["fingerprint"] = _typed(s["fingerprint"], f"{p}.fingerprint", str, re.compile(r"[0-9A-F]{40}").fullmatch, "40 upper-case hex")
        roles[role] = src
        out["pins"].append(e)
    bg = _obj(cfg.get("breakglass", {}), "config.breakglass", {"enabled", "commands", "max_ttl_hours", "signers"})
    out["breakglass"] = {"enabled": _typed(bg.get("enabled", False), "config.breakglass.enabled", bool),
                         "commands": _str_list(bg.get("commands", list(KNOWN_COMMANDS)), "config.breakglass.commands"),
                         "max_ttl_hours": _typed(bg.get("max_ttl_hours", 24), "config.breakglass.max_ttl_hours", int, lambda v: 1 <= v <= 168, "1-168"),
                         "signers": _str_list(bg.get("signers", []), "config.breakglass.signers", ROLE_RE)}
    bad = set(out["breakglass"]["commands"]) - set(KNOWN_COMMANDS)
    if bad:
        raise OobError(f"config.breakglass.commands: not implemented: {sorted(bad)}")
    for r in out["breakglass"]["signers"]:
        if roles.get(r) != "gpg_asc":
            raise OobError(f"config.breakglass.signers: {r!r} is not a gpg_asc pin role")
    if out["breakglass"]["enabled"] and not out["breakglass"]["signers"]:
        raise OobError("config.breakglass.signers: at least one gpg_asc pin role when enabled")
    return out


def load_config(path, default_account=None) -> dict:
    with open(path) as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError as e:
            raise OobError(f"{path}: not JSON ({e})") from e
    return validate(raw, default_account)


# -- selection ----------------------------------------------------------------

def resolve(cand, facts):
    if cand["interface"]:
        return cand["interface"]
    return facts.get("nm_active", {}).get(cand["connection"])


def is_never(cfg, dev, facts):
    if dev in cfg["never"]:
        return True
    conns = [c for c, d in facts.get("nm_active", {}).items() if d == dev]
    return any(c in cfg["never"] for c in conns)



def _vpn_match(cfg):
    """shape_oob's last three fields: the VPN's port, its protocol, its TCP relay port ('-' = no match)."""
    v = cfg["vpn"]
    return [str(v["port"]) if v["port"] is not None else "-", v["protocol"],
            str(v["relay_tcp_port"]) if v["relay_tcp_port"] is not None else "-"]

def select(cfg, facts, state):
    """The first candidate whose device has carrier and a default route, is not in `never`, and
    (with on_budget=cut) has not used up its budget. (key, device) or (None, None)."""
    for c in cfg["candidates"]:
        dev = resolve(c, facts)
        if not dev or is_never(cfg, dev, facts):
            continue
        f = facts.get("ifaces", {}).get(dev)
        if not f or not f.get("carrier") or not (f.get("gw4") or f.get("gw6")):
            continue
        st = state.get("metered", {}).get(c["key"], {})
        if c["metered"] and cfg["on_budget"] == "cut" and st.get("exhausted"):
            continue
        return c["key"], dev
    return None, None


# -- budget -------------------------------------------------------------------

def cycle_start(now, cycle_day, tz):
    z = ZoneInfo(tz)
    local = dt.datetime.fromtimestamp(now, z)
    y, m = local.year, local.month
    if local.day < cycle_day:
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return dt.datetime(y, m, cycle_day, tzinfo=z).timestamp()


def _bytes_since(traffic, start):
    for series, step in (("fiveminute", 300), ("hour", 3600), ("day", 86400)):
        rows = traffic.get(series) or []
        if rows and rows[0]["timestamp"] <= start:
            total = sum(r["rx"] + r["tx"] for r in rows if r["timestamp"] >= start)
            exact = series != "day" and start % step == 0
            return total, series + ("" if exact else " (approximate)")
    raise OobError("vnstat data does not reach back to the start of the period")


def budget(m, vn, now, dev):
    """The remaining high-speed allowance from THREE sources, taking the most conservative (operator
    2026-11-18: "soft over 3 sources"; "cached at is not live evidence"):
      live     vnstat on this box's device since the cycle start -- live, but sees only this box
      carrier  the operator's carrier figure plus this box's vnstat bytes since that figure's own time;
               the figure is a CACHED reading (labelled with its age), never live evidence
      other    use of the same allowance this box cannot see (a phone on the same plan), as entered,
               added on top
    used = max(live, carrier) + other; remaining = allowance - used; budget = fraction x remaining."""
    traffic = None
    for i in vn.get("interfaces", []):
        if i.get("name") == dev:
            traffic = i.get("traffic", {})
    if traffic is None:
        raise OobError(f"vnstat has no interface {dev}")
    allowance = int(round(m["allowance_gb"] * GB))
    cs = cycle_start(now, m["cycle_day"], m["timezone"])
    sources = {}
    try:
        live, series = _bytes_since(traffic, cs)
        sources["live"] = {"used_bytes": live, "as_of_utc": _utc(now), "age_s": 0, "live": True,
                           "label": f"vnstat {dev} since the cycle start ({series}); this box only"}
    except OobError as e:
        sources["live"] = {"used_bytes": None, "live": True, "label": f"unavailable: {e}"}
    b_at = _parse_utc(m["baseline_at"])
    if cs <= b_at <= now:
        base = int(round(m["baseline_used_gb"] * GB))
        month = (traffic.get("month") or [{}])[-1]
        b_dt = dt.datetime.fromtimestamp(b_at, dt.timezone.utc)
        md = month.get("date", {})
        delta = None
        try:
            if m.get("baseline_vnstat_month_bytes") is not None and md.get("year") == b_dt.year and md.get("month") == b_dt.month:
                delta = month["rx"] + month["tx"] - m["baseline_vnstat_month_bytes"]
                if delta < 0:
                    raise OobError("vnstat month total is below the baseline -- re-baseline")
                how = "vnstat month delta"
            else:
                delta, series = _bytes_since(traffic, b_at)
                how = f"vnstat {series}"
            sources["carrier"] = {"used_bytes": base + delta, "figure_bytes": base, "as_of_utc": _utc(b_at),
                                  "age_s": int(now - b_at), "live": False,
                                  "label": f"carrier figure as of {_utc(b_at)}, not live (+ {how} since)"}
        except OobError as e:
            sources["carrier"] = {"used_bytes": None, "as_of_utc": _utc(b_at), "live": False, "label": f"unavailable: {e}"}
    else:
        sources["carrier"] = {"used_bytes": None, "as_of_utc": _utc(b_at), "live": False,
                              "label": f"carrier figure as of {_utc(b_at)} is from an earlier cycle -- ignored"}
    o_at = m.get("other_usage_at")
    if o_at and cs <= _parse_utc(o_at) <= now:
        sources["other"] = {"used_bytes": int(round(m["other_usage_gb"] * GB)), "as_of_utc": o_at,
                            "age_s": int(now - _parse_utc(o_at)), "live": False,
                            "label": f"other use of the allowance (not seen by this box) as entered at {o_at}, not live"}
    else:
        sources["other"] = {"used_bytes": 0, "live": False,
                            "label": "no other-use figure for this cycle" + (f" (the one from {o_at} is older)" if o_at else "")}
    seen = [x["used_bytes"] for k, x in sources.items() if k != "other" and x["used_bytes"] is not None]
    if not seen:
        raise OobError("neither vnstat nor a carrier figure gives this cycle's use -- re-baseline "
                       f"(live: {sources['live']['label']}; carrier: {sources['carrier']['label']})")
    used = max(seen) + sources["other"]["used_bytes"]
    remaining = max(0, allowance - used)
    pick = max((k for k in ("live", "carrier") if sources[k]["used_bytes"] is not None),
               key=lambda k: sources[k]["used_bytes"])
    method = f"most conservative of 3 sources: {pick}" + (" + other" if sources["other"]["used_bytes"] else "")
    return {"cycle_start_utc": _utc(cs), "allowance_bytes": allowance, "used_bytes": used,
            "remaining_bytes": remaining, "budget_fraction": m["budget_fraction"],
            "budget_bytes": int(remaining * m["budget_fraction"]), "method": method, "sources": sources}


def apply_override(cfg, overrides):
    """Re-baselines written by `ctdc-oob.sh rebaseline` ({candidate key: {...}}) win while newer."""
    for c in cfg["candidates"]:
        ov = (overrides or {}).get(c["key"])
        if c["metered"] and ov and ov.get("baseline_at") and _parse_utc(ov["baseline_at"]) > _parse_utc(c["metered"]["baseline_at"]):
            for k in ("baseline_at", "baseline_used_gb", "baseline_vnstat_month_bytes"):
                c["metered"][k] = ov.get(k)
        if c["metered"] and ov and ov.get("other_usage_at") and (
                not c["metered"]["other_usage_at"] or _parse_utc(ov["other_usage_at"]) > _parse_utc(c["metered"]["other_usage_at"])):
            c["metered"]["other_usage_gb"] = float(ov["other_usage_gb"])
            c["metered"]["other_usage_at"] = ov["other_usage_at"]
    return cfg


# -- enter / tick / leave -> actions ---------------------------------------------

def _cand(cfg, key):
    return next(c for c in cfg["candidates"] if c["key"] == key)


def _normal_cap(c, dev, acts):
    """A candidate device that does NOT carry the OOB channel gets its normal-times treatment: a metered
    link keeps its standing cap (cap_on_leave), anything else is unshaped."""
    if c["metered"] and c["metered"]["cap_on_leave"]:
        acts.append(["cap_on", dev, c["metered"]["cap_rate"]])
    else:
        acts.append(["cap_off", dev])


def _init_meter(c, dev, facts, vnstat, now):
    st = {"device": dev, "used_bytes": 0, "exhausted": False, "alerted": False,
          "allowance_out": False, "allowance_alerted": False,
          "last_counter": facts.get("ifaces", {}).get(dev, {}).get("bytes", 0) if dev else 0}
    try:
        if dev is None:
            raise OobError("device not present")
        b = budget(c["metered"], vnstat.get(dev, {}), now, dev)
        st.update(budget_bytes=b["budget_bytes"], remaining_bytes=b["remaining_bytes"], basis=b["method"],
                  sources={k: v["label"] for k, v in b["sources"].items()})
        st["allowance_out"] = b["remaining_bytes"] <= 0
    except OobError as e:
        # fail closed on the BUDGET (alert, treat as used up) -- the channel itself stays up at the hard cap
        # the allowance is unknown, not known to be gone: the link keeps the hard cap, not the throttled rate
        st.update(budget_bytes=0, remaining_bytes=None, basis=f"unavailable: {e}", exhausted=True)
    return st


def tick(cfg, facts, state, vnstat, now):
    acts = []
    if not state.get("active"):
        return state, acts
    meters = state.setdefault("metered", {})
    for c in cfg["candidates"]:
        if not c["metered"]:
            continue
        dev = resolve(c, facts)
        st = meters.get(c["key"])
        if st is None or (st.get("device") is None and dev):
            st = meters[c["key"]] = _init_meter(c, dev, facts, vnstat, now)
        if dev and dev in facts.get("ifaces", {}):
            st["device"] = dev
            cur = facts["ifaces"][dev].get("bytes", 0)
            last = st.get("last_counter", 0)
            st["used_bytes"] += cur - last if cur >= last else cur      # a counter reset (reboot, re-plug)
            st["last_counter"] = cur
        if not st["exhausted"] and st["used_bytes"] >= st["budget_bytes"]:
            st["exhausted"] = True
            acts.append(["log", f"{c['key']} ({st['device']}): OOB budget used up ({st['used_bytes']} of {st['budget_bytes']} bytes) -- on_budget={cfg['on_budget']}"])
        if st["exhausted"] and not st["alerted"]:
            st["alerted"] = True
            what = ("link disconnected (on_budget=cut)" if cfg["on_budget"] == "cut"
                    else f"channel stays up at the {c['hard_cap_kbit']} kbit/s hard cap (soft budget)")
            acts.append(["alert", "5", "Lockdown OOB: data budget used up",
                         f"{st['device'] or c['key']} used {st['used_bytes'] / GB:.2f} GB of its {st['budget_bytes'] / GB:.2f} GB OOB budget; {what}."])
        if not st.get("allowance_out") and st.get("remaining_bytes") is not None and st["used_bytes"] >= st["remaining_bytes"]:
            st["allowance_out"] = True
        if st.get("allowance_out") and not st.get("allowance_alerted"):
            st["allowance_alerted"] = True
            acts.append(["alert", "5", "Lockdown OOB: high-speed allowance looks used up",
                         f"{st['device'] or c['key']}: the most conservative of the 3 sources shows no high-speed data left; "
                         f"shaping to {cfg['throttled_kbit']} kbit/s so the VPN daemon keeps priority under the carrier's throttle."])
    key, dev = select(cfg, facts, state)
    for c in cfg["candidates"]:                      # every candidate device present that is NOT the OOB one
        d = resolve(c, facts)
        if not d or d == dev or is_never(cfg, d, facts) or d not in facts.get("ifaces", {}):
            continue
        st = meters.get(c["key"], {})
        if c["metered"] and cfg["on_budget"] == "cut" and st.get("exhausted"):
            acts.append(["disconnect", d])
        else:
            _normal_cap(c, d, acts)
    if dev:
        c = _cand(cfg, key)
        st = meters.get(key, {})
        rate = cfg["throttled_kbit"] if c["metered"] and st.get("allowance_out") else c["hard_cap_kbit"]
        # the HARD cap, whatever the interface: never removed while the channel is up
        acts.append(["shape_oob", dev, str(rate)] + _vpn_match(cfg))
        acts.append(["route_on", dev])
    else:
        acts.append(["route_off"])
        acts.append(["log", "no OOB candidate has carrier and a default route -- the VPN daemon uses normal routing"])
    state["selected"] = key
    state["device"] = dev
    if cfg["ssh"]["enabled"]:
        acts.append(["sshd_on"])
    return state, acts


def enter(cfg, facts, state, vnstat, now, reason):
    if state.get("active"):
        state, acts = tick(cfg, facts, state, vnstat, now)
        return state, [["log", f"already active since {state.get('started_utc')} -- re-asserted, budget kept"]] + acts
    state = {"active": True, "started": int(now), "started_utc": _utc(now), "reason": reason[:200], "metered": {}}
    for c in cfg["candidates"]:
        if c["metered"]:
            state["metered"][c["key"]] = _init_meter(c, resolve(c, facts), facts, vnstat, now)
    state, acts = tick(cfg, facts, state, vnstat, now)
    summary = "; ".join(f"{st['device'] or k}: budget {st['budget_bytes'] / GB:.2f} GB" for k, st in state["metered"].items()) or "no metered candidate"
    cap = _cand(cfg, state["selected"])["hard_cap_kbit"] if state.get("selected") else cfg["hard_cap_kbit"]
    acts.append(["alert", "4", "Lockdown OOB channel up",
                 f"via {state.get('device') or 'normal routing'} at a {cap} kbit/s hard cap; {summary}. Closes with restore-network.sh."])
    return state, acts


def leave(cfg, facts, state):
    """The ONLY place the OOB hard cap comes off: every candidate device goes back to its normal-times
    treatment (a metered link keeps its standing cap)."""
    acts = [["route_off"]]
    acts.append(["sshd_off"])                            # always: a drop-in left by an older config goes too
    done = set()
    for c in cfg["candidates"]:
        dev = resolve(c, facts) or state.get("metered", {}).get(c["key"], {}).get("device") \
            or (state.get("device") if state.get("selected") == c["key"] else None)
        if not dev or dev in done:
            continue
        done.add(dev)
        if c["metered"] and state.get("metered", {}).get(c["key"], {}).get("exhausted") and cfg["on_budget"] == "cut":
            acts.append(["connect", dev])
        _normal_cap(c, dev, acts)
    state = dict(state, active=False, ended_utc=_utc(dt.datetime.now(dt.timezone.utc).timestamp()))
    return state, acts


def floor_plan(cfg, facts, state):
    """For ctdc-oob.sh prove-floor: the device a lockdown would use now and how to put it back."""
    key, dev = select(cfg, facts, state)
    if not dev:
        raise OobError("no OOB candidate has carrier and a default route now")
    c = _cand(cfg, key)
    restore = (["cap_on", dev, c["metered"]["cap_rate"]] if c["metered"] and c["metered"]["cap_on_leave"]
               else ["cap_off", dev])
    return {"key": key, "device": dev, "floor_kbit": cfg["floor_kbit"], "hard_cap_kbit": c["hard_cap_kbit"],
            "port": cfg["vpn"]["port"], "protocol": cfg["vpn"]["protocol"],
            "relay_tcp_port": cfg["vpn"]["relay_tcp_port"], "restore": restore}


# -- keys ---------------------------------------------------------------------

def ssh_fingerprint(line):
    parts = line.strip().split()
    if len(parts) < 2 or parts[0] not in SSH_TYPES:
        raise OobError("not a plain OpenSSH public key line")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError as e:
        raise OobError("bad base64 in public key") from e
    n = int.from_bytes(blob[:4], "big")
    if blob[4:4 + n].decode("ascii", "replace") != parts[0]:
        raise OobError("key type does not match the key blob")
    return parts[0], "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _dearmor(data):
    if b"-----BEGIN PGP" not in data:
        return data
    body, state = [], 0
    for ln in data.decode("ascii", "replace").splitlines():
        if state == 0 and ln.startswith("-----BEGIN PGP"):
            state = 1
        elif state == 1 and ln.strip() == "":
            state = 2
        elif state == 2:
            if ln.startswith("-----END PGP") or ln.startswith("="):
                break
            body.append(ln.strip())
    return base64.b64decode("".join(body))


def _packets(data):
    i = 0
    while i < len(data):
        h = data[i]
        if not h & 0x80:
            raise OobError("not an OpenPGP packet stream")
        if h & 0x40:
            tag, o = h & 0x3F, data[i + 1]
            if o < 192:
                ln, hl = o, 2
            elif o < 224:
                ln, hl = ((o - 192) << 8) + data[i + 2] + 192, 3
            elif o == 255:
                ln, hl = int.from_bytes(data[i + 2:i + 6], "big"), 6
            else:
                raise OobError("partial-length packets are not expected in a public key")
        else:
            tag, sz = (h >> 2) & 0x0F, {0: 1, 1: 2, 2: 4}.get(h & 3)
            if sz is None:
                raise OobError("indeterminate-length packet")
            ln, hl = int.from_bytes(data[i + 1:i + 1 + sz], "big"), 1 + sz
        yield tag, data[i + hl:i + hl + ln]
        i += hl + ln


def openpgp_fpr(data):
    for tag, body in _packets(_dearmor(data)):
        if tag == 6:
            if body[0] != 4:
                raise OobError(f"OpenPGP key version {body[0]} is not supported (v4 only)")
            return hashlib.sha1(b"\x99" + len(body).to_bytes(2, "big") + body).hexdigest().upper()
    raise OobError("no public key packet")


def derive_mode(fido, entries):
    """off -> file-keys; required -> fido-only; auto -> fido-only when at least one pinned SSH key is a
    hardware key (sk-*) on an ACTIVE signer row of an ADMIN account, else file-keys."""
    if fido == "off":
        return "file-keys"
    if fido == "required":
        return "fido-only"
    for e in entries:
        if e.get("channel") == "ssh" and e.get("keytype") in FIDO_TYPES and e.get("active") \
                and e.get("signer_row") and e.get("account_role") == "admin":
            return "fido-only"
    return "file-keys"


def pins_build(cfg, rows, repo, material_dir, now):
    """rows: [{source, account, pubkey, role, kind, active}] from the signer tables. Returns the pin
    file text (fingerprints only) and writes the public key material for root into material_dir."""
    entries, ssh_lines = [], []
    os.makedirs(material_dir, exist_ok=True)
    for p in cfg["pins"]:
        role, src = p["role"], p["source"]
        try:
            if src in ("board_signers", "approval_signers"):
                r = [x for x in rows if x["source"] == src and x["account"] == p["account"]]
                if not r:
                    raise OobError(f"no {src} row for {p['account']}")
                kt, fpr = ssh_fingerprint(r[0]["pubkey"])
                entries.append({"channel": "ssh", "role": role, "source": f"{src}:{p['account']}", "keytype": kt, "fpr": fpr,
                                "account": p["account"], "account_role": r[0].get("role") or "-",
                                "active": bool(r[0].get("active")), "signer_row": True})
                ssh_lines.append(" ".join(r[0]["pubkey"].split()[:2]) + f" {role}")
            elif src == "ssh_pubkey_file":
                path = p["path"] if p["path"].startswith("/") else os.path.join(repo, p["path"])
                if not os.path.isfile(path):
                    raise OobError(f"{p['path']} not found")
                with open(path) as f:
                    line = f.read(16384).strip().splitlines()[0]
                kt, fpr = ssh_fingerprint(line)
                entries.append({"channel": "ssh", "role": role, "source": "file", "keytype": kt, "fpr": fpr,
                                "account": p["account"], "account_role": "-", "active": True, "signer_row": False})
                ssh_lines.append(" ".join(line.split()[:2]) + f" {role}")
            else:
                path = p["path"] if p["path"].startswith("/") else os.path.join(repo, p["path"])
                if not os.path.isfile(path):
                    raise OobError(f"{p['path']} not found")
                with open(path, "rb") as f:
                    data = f.read(1 << 20)
                fpr = openpgp_fpr(data)
                if p.get("fingerprint") and fpr != p["fingerprint"]:
                    raise OobError(f"{p['path']} is not the expected key")
                entries.append({"channel": "gpg", "role": role, "source": "file", "keytype": "openpgp", "fpr": fpr,
                                "account": "-", "account_role": "-", "active": True, "signer_row": False})
                with open(os.path.join(material_dir, f"gpg-{role}.asc"), "wb") as f:
                    f.write(data)
        except OobError as e:
            entries.append({"role": role, "missing": re.sub(r"\s+", " ", str(e))[:160]})
    mode = derive_mode(cfg["ssh"]["fido"], entries)
    with open(os.path.join(material_dir, "ssh.pub"), "w") as f:
        f.write("".join(ln + "\n" for ln in ssh_lines))
    out = ["# OOB pinned fingerprints -- generated by scripts/oob-pin-build.sh. FINGERPRINTS ONLY.",
           "# The OOB sshd accepts a key only if its SHA256 fingerprint is an active 'ssh' line here; 'gpg'",
           "# lines pin the break-glass command signers. See docs/LOCKDOWN_OOB.md.",
           f"# generated-utc: {_utc(now)}", f"fido: {cfg['ssh']['fido']}", f"mode: {mode}"]
    for e in entries:
        if e.get("missing"):
            out.append(f"missing {e['role']} {e['missing']}")
        else:
            out.append(f"{e['channel']} {e['role']} {e['source']} {e['keytype']} {e['fpr']} account={e['account']} "
                       f"account_role={e['account_role']} active={int(e['active'])} signer_row={int(e['signer_row'])}")
    return "\n".join(out) + "\n"


def pins_parse(text):
    fido = mode = None
    ssh, gpg, missing = [], [], []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        f = ln.split()
        if f[0] == "fido:" and len(f) == 2 and f[1] in ("auto", "required", "off") and fido is None:
            fido = f[1]
        elif f[0] == "mode:" and len(f) == 2 and f[1] in ("file-keys", "fido-only") and mode is None:
            mode = f[1]
        elif f[0] in ("ssh", "gpg") and len(f) == 9 and ROLE_RE.fullmatch(f[1]):
            kv = dict(x.split("=", 1) for x in f[5:])
            e = {"channel": f[0], "role": f[1], "source": f[2], "keytype": f[3], "fpr": f[4], "account": kv.get("account"),
                 "account_role": kv.get("account_role"), "active": kv.get("active") == "1", "signer_row": kv.get("signer_row") == "1"}
            (ssh if f[0] == "ssh" else gpg).append(e)
        elif f[0] == "missing" and len(f) >= 2 and ROLE_RE.fullmatch(f[1]):
            missing.append(f[1])
        else:
            raise OobError(f"unrecognised pin line: {ln[:80]}")
    if fido is None or mode is None:
        raise OobError("pin file needs a fido: and a mode: line")
    if derive_mode(fido, ssh) != mode:      # never trusted from the file alone
        raise OobError(f"pin file says mode {mode} but its entries give {derive_mode(fido, ssh)} -- rebuild the pins")
    return {"fido": fido, "mode": mode, "ssh": ssh, "gpg": gpg, "missing": missing}


def pins_check(pins, cfg, key_lines):
    """({account: [authorized_keys lines]}, rejections). Only active pinned ssh fingerprints of an
    allowed account pass; fido-only mode passes sk-* keys only."""
    if cfg["ssh"]["fido"] != pins["fido"]:
        raise OobError(f"config fido={cfg['ssh']['fido']} but the pins were built with fido={pins['fido']} -- rebuild the pins")
    allowed = {e["fpr"]: e for e in pins["ssh"] if e["active"]}
    ok, bad = {}, []
    for raw in key_lines:
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            kt, fpr = ssh_fingerprint(raw)
        except OobError as e:
            bad.append(f"rejected (unparseable: {e})")
            continue
        e = allowed.get(fpr)
        if e is None:
            bad.append(f"rejected {kt} {fpr}: not pinned")
        elif e["keytype"] != kt:
            bad.append(f"rejected {kt} {fpr}: pinned as {e['keytype']}")
        elif e["account"] not in cfg["ssh"]["allowed_accounts"]:
            bad.append(f"rejected {kt} {fpr}: account {e['account']} may not log in")
        elif pins["mode"] == "fido-only" and kt not in FIDO_TYPES:
            bad.append(f"rejected {kt} {fpr}: fido-only mode accepts hardware (sk-*) keys only")
        else:
            ok.setdefault(e["account"], []).append(f"restrict,pty {kt} {raw.split()[1]} ctdc-oob:{e['role']}")
    return ok, bad


def sshd_config(cfg, mode, listen, keys_dir="/etc/ctdc-oob/authorized_keys.d"):
    if not listen:
        raise OobError("no tailnet address to listen on")
    for a in listen:
        if not re.fullmatch(r"100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}|fd7a:115c:a1e0:[0-9a-f:]+", a):
            raise OobError(f"{a} is not a tailnet address")
    lines = ["# 05-ctdc-oob.conf -- written by ctdc-oob.sh (lockdown OOB); removed when the OOB channel closes",
             "# and at every boot. First value wins in sshd, and this file sorts first in sshd_config.d."]
    lines += [f"ListenAddress {a if ':' not in a else '[' + a + ']'}" for a in listen]
    lines += ["PasswordAuthentication no", "KbdInteractiveAuthentication no", "PermitEmptyPasswords no",
              "PubkeyAuthentication yes", "AuthenticationMethods publickey", "PermitRootLogin no",
              "GSSAPIAuthentication no", "HostbasedAuthentication no",
              f"AuthorizedKeysFile {keys_dir}/%u", "AuthorizedKeysCommand none", "AuthorizedPrincipalsFile none",
              "AllowUsers " + " ".join(cfg["ssh"]["allowed_accounts"]),
              # usable down to the ~256 kbit/s floor: compressed, small infrequent keepalives inside the
              # tunnel, no extra TCP keepalive traffic, and a dead peer still reaped in ~4 minutes
              "Compression yes", "TCPKeepAlive no", "ClientAliveInterval 30", "ClientAliveCountMax 8"]
    if mode == "fido-only":
        lines.append("PubkeyAcceptedAlgorithms " + ",".join(FIDO_TYPES))
    return "\n".join(lines) + "\n"


# -- break-glass ----------------------------------------------------------------

def parse_command(text, now, allow, max_ttl_s):
    if len(text) > 4096 or not text.isascii():
        raise OobError("command text must be ASCII and at most 4096 bytes")
    kv = {}
    for ln in text.splitlines():
        if not ln.strip():
            continue
        if ": " not in ln:
            raise OobError("malformed line")
        k, v = ln.split(": ", 1)
        if k in kv:
            raise OobError(f"duplicate key {k}")
        kv[k] = v.strip()
    if set(kv) != {"ctdc-oob-breakglass", "command", "nonce", "expires-utc", "reason"} or kv["ctdc-oob-breakglass"] != "1":
        raise OobError("needs exactly: ctdc-oob-breakglass: 1, command, nonce, expires-utc, reason")
    if kv["command"] not in allow:
        raise OobError(f"command {kv['command']!r} is not on the allowlist")
    if not re.fullmatch(r"[0-9a-f]{32,64}", kv["nonce"]):
        raise OobError("nonce must be 32-64 lowercase hex characters")
    try:
        exp = _parse_utc(kv["expires-utc"])
    except ValueError as e:
        raise OobError("expires-utc must be YYYY-MM-DDTHH:MM:SSZ") from e
    if exp <= now:
        raise OobError("command has expired")
    if exp - now > max_ttl_s:
        raise OobError("expiry is further ahead than max_ttl_hours")
    if not re.fullmatch(r"[ -~]{3,200}", kv["reason"]):
        raise OobError("reason must be 3-200 printable characters")
    return {"command": kv["command"], "nonce": kv["nonce"], "expires": exp, "reason": kv["reason"]}


def gpg_verify(key_file, pin, text_file, sig_file, gpg="gpg"):
    """Detached signature, verified in a fresh dedicated keyring holding ONLY key_file; the signer's
    primary fingerprint must equal pin. Never the ambient keyring, never an agent."""
    with open(key_file, "rb") as f:
        if openpgp_fpr(f.read()) != pin:
            raise OobError("the key file is not the pinned key")
    with tempfile.TemporaryDirectory(prefix="ctdc-oob-gpg-") as home:
        os.chmod(home, 0o700)
        open(os.path.join(home, "common.conf"), "w").close()   # no use-keyboxd: a plain keyring only
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "GNUPGHOME": home, "LC_ALL": "C"}
        base = [gpg, "--homedir", home, "--batch", "--no-tty", "--no-autostart", "--no-options",
                "--no-default-keyring", "--keyring", os.path.join(home, "pin.kbx"), "--trust-model", "always"]
        r = subprocess.run(base + ["--import", key_file], env=env, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise OobError("could not import the pinned key into the dedicated keyring")
        r = subprocess.run(base + ["--status-fd", "1", "--verify", sig_file, text_file],
                           env=env, capture_output=True, text=True, timeout=30)
    status = [ln.split() for ln in r.stdout.splitlines() if ln.startswith("[GNUPG:] ")]
    bad = {"BADSIG", "EXPKEYSIG", "REVKEYSIG", "EXPSIG", "ERRSIG", "NO_PUBKEY"} & {s[1] for s in status if len(s) > 1}
    if bad:
        raise OobError("signature rejected: " + ",".join(sorted(bad)))
    valid = [s for s in status if len(s) > 2 and s[1] == "VALIDSIG"]
    if r.returncode != 0 or len(valid) != 1 or not any(len(s) > 1 and s[1] == "GOODSIG" for s in status):
        raise OobError("no single good signature")
    primary = valid[0][11] if len(valid[0]) > 11 else valid[0][2]
    if primary.upper() != pin:
        raise OobError("signed by a key other than the pinned key")
    return primary.upper()


def nonce_seen(store, nonce):
    try:
        with open(store) as f:
            return any(ln.split()[:1] == [nonce] for ln in f)
    except FileNotFoundError:
        return False


def breakglass(cfg, pins, key_dir, text_file, sig_file, store, now, gpg="gpg"):
    if not cfg["breakglass"]["enabled"]:
        raise OobError("break-glass commands are disabled in the config")
    with open(text_file, "rb") as f:
        raw = f.read(4097)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as e:
        raise OobError("command text must be ASCII") from e
    cmd = parse_command(text, now, cfg["breakglass"]["commands"], cfg["breakglass"]["max_ttl_hours"] * 3600)
    signers = [e for e in pins["gpg"] if e["role"] in cfg["breakglass"]["signers"]]
    if not signers:
        raise OobError("no pinned break-glass signer key")
    errors, who = [], None
    for e in signers:
        kf = os.path.join(key_dir, f"gpg-{e['role']}.asc")
        if not os.path.isfile(kf):
            errors.append(f"{e['role']}: key not installed")
            continue
        try:
            gpg_verify(kf, e["fpr"], text_file, sig_file, gpg=gpg)
            who = e["role"]
            break
        except OobError as ex:
            errors.append(f"{e['role']}: {ex}")
    if who is None:
        raise OobError("; ".join(errors))
    if nonce_seen(store, cmd["nonce"]):
        raise OobError("replay: this nonce was already used")
    with open(store, "a") as f:                       # recorded BEFORE the command runs
        f.write(f"{cmd['nonce']} {int(now)} {cmd['command']} {who} {hashlib.sha256(raw).hexdigest()}\n")
    return dict(cmd, signer=who)


# -- facts (read-only) ------------------------------------------------------------

def _run_json(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def _nm_active():
    try:
        r = subprocess.run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for ln in r.stdout.splitlines():
        parts = re.split(r"(?<!\\):", ln)
        if len(parts) == 2 and parts[1]:
            out[parts[0].replace("\\:", ":").replace("\\\\", "\\")] = parts[1]
    return out


def gather_facts(sys_net="/sys/class/net", vpn_if="tailscale0"):
    ifaces = {}
    for dev in sorted(os.listdir(sys_net)):
        if dev == "lo" or not IFACE_RE.fullmatch(dev):
            continue
        def rd(rel, d=dev):
            try:
                with open(os.path.join(sys_net, d, rel)) as f:
                    return f.read().strip()
            except OSError:
                return ""
        rx, tx = rd("statistics/rx_bytes"), rd("statistics/tx_bytes")
        ifaces[dev] = {"carrier": rd("carrier") == "1", "gw4": "", "gw6": "",
                       "bytes": (int(rx) if rx.isdigit() else 0) + (int(tx) if tx.isdigit() else 0)}
    for fam in ("4", "6"):
        for r in _run_json(["ip", "-" + fam, "-j", "route", "show", "default"]):
            hops = r.get("nexthops") or [r]
            for h in hops:
                d = h.get("dev")
                if d in ifaces and not ifaces[d]["gw" + fam]:
                    ifaces[d]["gw" + fam] = h.get("gateway") or "-"
    tail = []
    for a in _run_json(["ip", "-j", "addr", "show", "dev", vpn_if]):
        for i in a.get("addr_info", []):
            if i.get("scope") == "global":
                tail.append(i["local"])
    return {"ifaces": ifaces, "nm_active": _nm_active(), "tailnet": tail}


# -- CLI ----------------------------------------------------------------------

def _load_json(path, default=None):
    if path and os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    return default


def _write_json(path, obj):
    tmp = f"{path}.new"
    with open(tmp, "w") as f:
        json.dump(obj, f, sort_keys=True, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _vnstat_dir(d):
    out = {}
    if d and os.path.isdir(d):
        for n in os.listdir(d):
            if n.startswith("vnstat-") and n.endswith(".json"):
                try:
                    out[n[7:-5]] = _load_json(os.path.join(d, n), {})
                except json.JSONDecodeError:
                    out[n[7:-5]] = {}
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="ctdc_oob.py")
    p.add_argument("--config")
    p.add_argument("--default-account")
    p.add_argument("--overrides")
    p.add_argument("--now", type=float)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    fa = sub.add_parser("facts")
    fa.add_argument("--vnstat-dir")
    sub.add_parser("shell-config")
    for n in ("enter", "tick", "leave", "budgets", "floor-plan"):
        s = sub.add_parser(n)
        s.add_argument("--facts", required=True)
        s.add_argument("--state")
        s.add_argument("--vnstat-dir")
        s.add_argument("--reason", default="manual")
        s.add_argument("--no-write", action="store_true")
    sub.add_parser("pin-accounts")
    pb = sub.add_parser("pins-build")
    pb.add_argument("--rows", required=True)
    pb.add_argument("--repo", required=True)
    pb.add_argument("--material-dir", required=True)
    for n in ("pins-check", "pins-mode", "sshd-config"):
        s = sub.add_parser(n)
        s.add_argument("--pins", required=True)
        s.add_argument("--keys")
        s.add_argument("--out-dir")
        s.add_argument("--listen", action="append", default=[])
    bg = sub.add_parser("breakglass")
    bg.add_argument("--pins", required=True)
    bg.add_argument("--key-dir", required=True)
    bg.add_argument("--text", required=True)
    bg.add_argument("--sig", required=True)
    bg.add_argument("--nonces", required=True)
    o = sub.add_parser("openpgp-fpr")
    o.add_argument("file")
    a = p.parse_args(argv)
    now = a.now or dt.datetime.now(dt.timezone.utc).timestamp()
    try:
        if a.cmd == "openpgp-fpr":
            with open(a.file, "rb") as f:
                print(openpgp_fpr(f.read()))
            return 0
        if not a.config:
            raise OobError("--config is required")
        cfg = apply_override(load_config(a.config, a.default_account), _load_json(a.overrides, {}))
        if a.cmd == "facts":
            facts = gather_facts(vpn_if=cfg["vpn"]["interface"])
            print(json.dumps(facts, sort_keys=True))
            if a.vnstat_dir:                       # vnstat JSON for every metered candidate's device
                os.makedirs(a.vnstat_dir, exist_ok=True)
                for c in cfg["candidates"]:
                    dev = resolve(c, facts)
                    if c["metered"] and dev:
                        try:
                            r = subprocess.run(["vnstat", "-i", dev, "--json"], capture_output=True, text=True, timeout=30)
                            with open(os.path.join(a.vnstat_dir, f"vnstat-{dev}.json"), "w") as f:
                                f.write(r.stdout if r.returncode == 0 else "{}")
                        except (OSError, subprocess.TimeoutExpired):
                            pass
        elif a.cmd == "validate":
            print(f"ok: {len(cfg['candidates'])} candidate(s), {sum(1 for c in cfg['candidates'] if c['metered'])} metered, "
                  f"{len(cfg['pins'])} pin source(s), fido={cfg['ssh']['fido']}, on_budget={cfg['on_budget']}")
        elif a.cmd == "shell-config":
            t = cfg["vpn"]
            print(f"HARD_CAP_KBIT={cfg['hard_cap_kbit']}\nFLOOR_KBIT={cfg['floor_kbit']}\n"
                  f"TS_PORT={t['port'] if t['port'] is not None else '-'}\nVPN_PROTO={t['protocol']}\n"
                  f"VPN_IF={t['interface']}\nRELAY_TCP_PORT={t['relay_tcp_port'] if t['relay_tcp_port'] is not None else '-'}")
            if cfg["ssh"]["allowed_accounts"]:
                print(f"SSH_ACCOUNT={cfg['ssh']['allowed_accounts'][0]}")
            print(f"TABLE={t['table']}\nRULE_PRIO={t['rule_priority']}\nMARK={t['mark']}\nMASK={t['mask']}\n"
                  f"TS_SERVICE={t['service']}\nSSH_ENABLED={int(cfg['ssh']['enabled'])}\nBG_ENABLED={int(cfg['breakglass']['enabled'])}")
            for c in cfg["candidates"]:
                if c["metered"] and c["interface"]:
                    print(f"METERED_IF={c['interface']}")
                if c["metered"] and c["connection"]:
                    print(f"METERED_CONN={c['connection']}")
        elif a.cmd in ("enter", "tick", "leave", "budgets", "floor-plan"):
            facts = _load_json(a.facts, {})
            state = _load_json(a.state, {}) if a.state else {}
            vn = _vnstat_dir(a.vnstat_dir)
            if a.cmd == "floor-plan":
                print(json.dumps(floor_plan(cfg, facts, state), sort_keys=True))
                return 0
            if a.cmd == "budgets":
                for c in cfg["candidates"]:
                    if not c["metered"]:
                        continue
                    dev = resolve(c, facts)
                    try:
                        b = budget(c["metered"], vn.get(dev, {}), now, dev)
                        print(json.dumps(dict(b, key=c["key"], device=dev), sort_keys=True))
                        for name in ("live", "carrier", "other"):
                            x = b["sources"][name]
                            used = "-" if x["used_bytes"] is None else f"{x['used_bytes'] / GB:.2f} GB"
                            age = f", age {x['age_s'] // 3600}h{(x['age_s'] % 3600) // 60:02d}m" if "age_s" in x else ""
                            print(f"#   {c['key']} source {name}: {used} -- {x['label']}{age}")
                        print(f"#   {c['key']} remaining (most conservative): {b['remaining_bytes'] / GB:.2f} GB; "
                              f"OOB budget {b['budget_bytes'] / GB:.2f} GB; {b['method']}")
                    except OobError as e:
                        print(json.dumps({"key": c["key"], "device": dev, "error": str(e)}))
                key, dev = select(cfg, facts, state)
                print(json.dumps({"would_select": key, "device": dev}))
                return 0
            if a.cmd == "enter":
                state, acts = enter(cfg, facts, state, vn, now, a.reason)
            elif a.cmd == "tick":
                state, acts = tick(cfg, facts, state, vn, now)
            else:
                state, acts = leave(cfg, facts, state)
            if a.state and not a.no_write:
                _write_json(a.state, state)
            for act in acts:
                print("\t".join(x.replace("\t", " ").replace("\n", " ") for x in act))
        elif a.cmd == "pin-accounts":
            for s in cfg["pins"]:
                if s["source"] in ("board_signers", "approval_signers"):
                    print(f"{s['source']} {s['account']}")
        elif a.cmd == "pins-build":
            with open(a.rows) as f:
                rows = json.load(f)
            sys.stdout.write(pins_build(cfg, rows, a.repo, a.material_dir, now))
        elif a.cmd in ("pins-check", "pins-mode", "sshd-config"):
            with open(a.pins) as f:
                pins = pins_parse(f.read())
            if a.cmd == "pins-mode":
                print(pins["mode"])
            elif a.cmd == "sshd-config":
                sys.stdout.write(sshd_config(cfg, pins["mode"], a.listen))
            else:
                with open(a.keys) as f:
                    ok, bad = pins_check(pins, cfg, f.read().splitlines())
                for ln in bad:
                    print(ln, file=sys.stderr)
                if a.out_dir:
                    os.makedirs(a.out_dir, exist_ok=True)
                    for acct in cfg["ssh"]["allowed_accounts"]:
                        dest = os.path.join(a.out_dir, acct)
                        with open(dest + ".new", "w") as f:
                            f.write("".join(ln + "\n" for ln in ok.get(acct, [])))
                        os.chmod(dest + ".new", 0o644)
                        os.replace(dest + ".new", dest)
                for acct, lines in sorted(ok.items()):
                    print(f"{acct}: {len(lines)} pinned key(s)")
                if not ok:
                    print("pins-check: no pinned key accepted", file=sys.stderr)
                    return 3
        elif a.cmd == "breakglass":
            with open(a.pins) as f:
                pins = pins_parse(f.read())
            cmd = breakglass(cfg, pins, a.key_dir, a.text, a.sig, a.nonces, now)
            print(json.dumps({k: cmd[k] for k in ("command", "nonce", "reason", "signer")}))
    except (OobError, KeyError, ValueError, OSError) as e:
        print(f"ctdc_oob: {a.cmd}: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
