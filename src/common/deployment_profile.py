"""common.deployment_profile -- typed loader for the deployment profile
(2026-10-09; design pack docs/deployment-profiles/, gates 1 and 2).

What it does
  * Loads ONE profile JSON: the file named by DISPATCH_PROFILE, else the bundled
    reference profile (src/common/profiles/ctdi-reference.json). The reference
    profile is the known-good default: it reproduces today's Washington, DC
    behaviour exactly, so a deployment that sets nothing behaves as before.
  * Parses the sections the platform consumes (deployment, geography, sources)
    into frozen dataclasses. Unknown keys and wrong types are errors
    (ProfileError) -- a typo is never silently ignored (pack gate 1). The other
    sections are kept as validated-by-schema raw dicts (`Profile.raw`).
  * Standard library only: the images do not carry `jsonschema`; the strict JSON
    Schema check runs in docs/deployment-profiles/validate_profiles.py and in the
    test suite.

Precedence, everywhere: an explicit environment setting > the profile > the
bundled reference. Environment settings that already exist keep working
(DISPATCH_LOCAL_TZ, DISPATCH_DAY_ROLLOVER, NOTAM_HOME_ARTCCS, ...).

Secrets and personal data never live in a profile: the receiver position is the
NAME of the environment variables that hold it (`home_base.coordinates_env`).

Geography data: airports carry their own coordinates and IANA zone, resolved
when the profile is AUTHORED (public airport reference data, OpenStreetMap /
Nominatim for free-text places), never looked up at runtime -- local-first.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REFERENCE_PATH = Path(__file__).resolve().parent / "profiles" / "ctdi-reference.json"
ROLES = frozenset({"surface_tracking", "metar", "core", "fids", "cps", "notam_watch"})
TOP_LEVEL = frozenset({"schema_version", "deployment", "organization", "geography", "sources", "modules",
                       "workflows", "security", "build", "integrations", "deployment_gate"})


class ProfileError(ValueError):
    """The profile is malformed: unknown key, wrong type, bad value."""


def _obj(d, path: str, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(d, dict):
        raise ProfileError(f"{path}: expected an object")
    unknown = set(d) - set(allowed)
    if unknown:
        raise ProfileError(f"{path}: unknown key(s) {sorted(unknown)}")
    missing = set(required) - set(d)
    if missing:
        raise ProfileError(f"{path}: missing key(s) {sorted(missing)}")
    return d


def _str_list(v, path: str) -> tuple[str, ...]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ProfileError(f"{path}: expected a list of strings")
    return tuple(v)


def _num(v, path: str, lo: float, hi: float) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ProfileError(f"{path}: expected a number in [{lo}, {hi}] or null")
    return float(v)


@dataclass(frozen=True)
class Airport:
    icao: str
    name: str = ""
    roles: frozenset[str] = frozenset()
    latitude: float | None = None
    longitude: float | None = None
    tz: str | None = None


@dataclass(frozen=True)
class Geography:
    airports: tuple[Airport, ...]
    rail_stations: tuple[str, ...]
    nws_wfos: tuple[str, ...]
    static_layers: tuple[str, ...]
    notam_home: tuple[str, ...]
    notam_monitor: tuple[str, ...]
    atc_facilities: tuple[str, ...]
    geofence_radius_nm: float | None
    home_latitude: float | None
    home_longitude: float | None
    home_env: tuple[str, str] | None


@dataclass(frozen=True)
class Profile:
    id: str
    profile: str
    timezone: str
    day_rollover: str
    geography: Geography
    sources_enabled: tuple[str, ...]
    path: str
    raw: dict = field(repr=False, compare=False, default_factory=dict)


def _airport(a, path: str) -> Airport:
    if isinstance(a, str):
        return Airport(icao=a.upper())
    _obj(a, path, {"icao", "name", "roles", "latitude", "longitude", "tz"}, {"icao", "roles"})
    roles = frozenset(_str_list(a["roles"], f"{path}.roles"))
    bad = roles - ROLES
    if bad:
        raise ProfileError(f"{path}.roles: unknown role(s) {sorted(bad)}")
    tz = a.get("tz")
    if tz is not None:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(tz)
        except Exception as e:  # noqa: BLE001
            raise ProfileError(f"{path}.tz: not an IANA zone ({tz!r})") from e
    return Airport(icao=str(a["icao"]).upper(), name=str(a.get("name", "")), roles=roles,
                   latitude=_num(a.get("latitude"), f"{path}.latitude", -90, 90),
                   longitude=_num(a.get("longitude"), f"{path}.longitude", -180, 180), tz=tz)


def parse(d: dict, path: str = "<profile>") -> Profile:
    _obj(d, path, TOP_LEVEL, TOP_LEVEL)
    dep = _obj(d["deployment"], "deployment",
               {"id", "display_name", "profile", "environment", "timezone", "day_rollover", "locale", "jurisdiction",
                "data_residency", "offline_mode", "retention_days"})
    g = _obj(d["geography"], "geography",
             {"country_codes", "regions", "service_areas", "home_base", "airports", "rail_stations", "weather",
              "airspace", "atc_facilities", "geofence_radius_nm", "distance_unit", "currency"})
    hb = _obj(g.get("home_base") or {}, "geography.home_base", {"latitude", "longitude", "radius_km", "coordinates_env"})
    env = hb.get("coordinates_env")
    if env is not None:
        _obj(env, "geography.home_base.coordinates_env", {"latitude", "longitude"}, {"latitude", "longitude"})
    air = _obj(g.get("airspace") or {}, "geography.airspace", {"static_layers", "tfr_enabled", "notam_zones"})
    nz = _obj(air.get("notam_zones") or {}, "geography.airspace.notam_zones", {"home", "monitor"})
    wx = _obj(g.get("weather") or {}, "geography.weather", {"provider", "nws_areas", "nws_wfos"})
    airports = tuple(_airport(a, f"geography.airports[{i}]") for i, a in enumerate(g.get("airports") or []))
    dup = {a.icao for a in airports if sum(1 for b in airports if b.icao == a.icao) > 1}
    if dup:
        raise ProfileError(f"geography.airports: duplicate {sorted(dup)}")
    tz = dep.get("timezone") or "UTC"
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception as e:  # noqa: BLE001
        raise ProfileError(f"deployment.timezone: not an IANA zone ({tz!r})") from e
    src = _obj(d["sources"], "sources", {"enabled", "disabled", "credentials_ref", "source_regions"})
    return Profile(
        id=str(dep.get("id", "")), profile=str(dep.get("profile", "")), timezone=tz,
        day_rollover=str(dep.get("day_rollover") or "05:00"),
        geography=Geography(
            airports=airports,
            rail_stations=_str_list(g.get("rail_stations") or [], "geography.rail_stations"),
            nws_wfos=_str_list(wx.get("nws_wfos") or [], "geography.weather.nws_wfos"),
            static_layers=_str_list(air.get("static_layers") or [], "geography.airspace.static_layers"),
            notam_home=_str_list(nz.get("home") or [], "geography.airspace.notam_zones.home"),
            notam_monitor=_str_list(nz.get("monitor") or [], "geography.airspace.notam_zones.monitor"),
            atc_facilities=_str_list(g.get("atc_facilities") or [], "geography.atc_facilities"),
            geofence_radius_nm=_num(g.get("geofence_radius_nm"), "geography.geofence_radius_nm", 0, 20000),
            home_latitude=_num(hb.get("latitude"), "geography.home_base.latitude", -90, 90),
            home_longitude=_num(hb.get("longitude"), "geography.home_base.longitude", -180, 180),
            home_env=(env["latitude"], env["longitude"]) if env else None),
        sources_enabled=_str_list(src.get("enabled") or [], "sources.enabled"),
        path=path, raw=d)


@lru_cache(maxsize=4)
def _load_cached(path: str, mtime: float) -> Profile:
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise ProfileError(f"{path}: cannot read profile ({type(e).__name__})") from e
    return parse(d, path)


def load(path: str | os.PathLike | None = None) -> Profile:
    p = Path(path or os.environ.get("DISPATCH_PROFILE") or REFERENCE_PATH)
    return _load_cached(str(p), p.stat().st_mtime if p.exists() else 0.0)


def current() -> Profile:
    return load()


# -- geography provider (profile-backed; no runtime network lookups) ---------------------------

def airports(role: str, profile: Profile | None = None) -> tuple[str, ...]:
    """ICAO codes carrying `role`, in profile order."""
    if role not in ROLES:
        raise ProfileError(f"unknown airport role {role!r}")
    p = profile or current()
    return tuple(a.icao for a in p.geography.airports if role in a.roles)


def airport(icao: str, profile: Profile | None = None) -> Airport | None:
    icao = (icao or "").upper()
    p = profile or current()
    return next((a for a in p.geography.airports if a.icao == icao or a.icao == "K" + icao), None)


def _nm(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    return 2 * 3440.065 * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


def airports_within(lat: float, lon: float, radius_nm: float, profile: Profile | None = None) -> tuple[Airport, ...]:
    p = profile or current()
    return tuple(a for a in p.geography.airports if a.latitude is not None and a.longitude is not None
                 and _nm(lat, lon, a.latitude, a.longitude) <= radius_nm)


def receiver_position(profile: Profile | None = None) -> tuple[float, float, str] | None:
    """(lat, lon, source). Environment variables named by the profile win; then
    coordinates in the profile itself; else None -- the CALLER decides what a
    missing position means (pack gate 6: distance-sensitive features should treat
    it as a configuration error rather than assume a city)."""
    p = profile or current()
    if p.geography.home_env:
        lat_s, lon_s = (os.environ.get(n, "").strip() for n in p.geography.home_env)
        if lat_s and lon_s:
            try:
                return float(lat_s), float(lon_s), "env"
            except ValueError:
                raise ProfileError("receiver position environment variables are not numbers") from None
    if p.geography.home_latitude is not None and p.geography.home_longitude is not None:
        return p.geography.home_latitude, p.geography.home_longitude, "profile"
    return None


def local_timezone(profile: Profile | None = None) -> str:
    """Operator-local zone for day-keyed work: DISPATCH_LOCAL_TZ > profile."""
    return os.environ.get("DISPATCH_LOCAL_TZ") or (profile or current()).timezone
