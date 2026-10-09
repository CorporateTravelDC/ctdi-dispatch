"""
2026-10-09: typed deployment-profile loader (common.deployment_profile) and the
bundled reference profile, the known-good default. The reference profile must
reproduce today's hardcoded Washington, DC behaviour exactly; these tests pin
that while consumers move onto the loader one at a time.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from common import deployment_profile as dp

REPO = Path(__file__).resolve().parents[2]


def ref():
    return dp.load(dp.REFERENCE_PATH)


def test_reference_profile_reproduces_todays_hardcoded_sets():
    p = ref()
    assert dp.airports("metar", p) == ("KDCA", "KIAD", "KBWI", "KFDK", "KHEF", "KJYO", "KGAI")
    from ingest.parsers import smes_parser
    assert set(dp.airports("surface_tracking", p)) == set(smes_parser.SMES_AIRPORTS)
    import common.db as db
    assert {a for a in db._DC_AREA_AIRPORTS if a.startswith("K")} == set(dp.airports("core", p))
    assert set(dp.airports("fids", p)) == {"KDCA", "KIAD"}
    env = (REPO / "config/dispatch.env").read_text()
    wfo = next(l.split("=", 1)[1] for l in env.splitlines() if l.startswith("NWWS_WFO_FILTER="))
    assert p.geography.nws_wfos == tuple(wfo.split(","))
    assert p.timezone == "America/New_York" and p.day_rollover == "05:00"
    assert p.geography.notam_home == () and p.geography.notam_monitor == ()   # public edition ships zones empty


def test_metar_fetcher_reads_the_profile_with_the_identical_default():
    from poller.fetchers import metar
    assert metar.DC_STATIONS == metar._REFERENCE_STATIONS


def test_metar_station_parse_accepts_any_icao_station():
    from poller.fetchers import metar
    assert metar.parse_metar("EGLL 081200Z 24010KT 9999 BKN020 12/08 Q1013").station == "EGLL"
    assert metar.parse_metar("METAR KDCA 081152Z 00000KT 10SM CLR 14/08 A3012").station == "KDCA"
    assert metar.parse_metar("garbage text").station == "UNKN"


def _write(tmp_path, mutate):
    d = json.loads(dp.REFERENCE_PATH.read_text())
    mutate(d)
    f = tmp_path / "p.json"
    f.write_text(json.dumps(d))
    return f


@pytest.mark.parametrize("mutate", [
    lambda d: d["geography"].__setitem__("airprots", []),                       # typo'd key
    lambda d: d["geography"]["airports"][0].__setitem__("roles", ["metr"]),     # unknown role
    lambda d: d["geography"]["airports"][0].__setitem__("tz", "Mars/Olympus"),  # bad zone
    lambda d: d["geography"]["airports"].append(d["geography"]["airports"][0]), # duplicate
    lambda d: d["deployment"].__setitem__("timezone", "EST5EDT-ish"),
    lambda d: d.__setitem__("extra", {}),
    lambda d: d["geography"]["home_base"].__setitem__("latitude", 123.0),
])
def test_malformed_profiles_are_errors_never_ignored(tmp_path, mutate):
    with pytest.raises(dp.ProfileError):
        dp.load(_write(tmp_path, mutate))


def test_dispatch_profile_selects_another_deployment(tmp_path, monkeypatch):
    def lon(d):
        d["deployment"]["id"] = "example-elsewhere"
        d["deployment"]["timezone"] = "Europe/London"
        d["geography"]["airports"] = [{"icao": "EGLL", "roles": ["metar", "core"], "tz": "Europe/London"}]
    monkeypatch.setenv("DISPATCH_PROFILE", str(_write(tmp_path, lon)))
    monkeypatch.delenv("DISPATCH_LOCAL_TZ", raising=False)
    assert dp.current().id == "example-elsewhere"
    assert dp.airports("metar") == ("EGLL",) and dp.local_timezone() == "Europe/London"
    monkeypatch.setenv("DISPATCH_LOCAL_TZ", "Asia/Tokyo")
    assert dp.local_timezone() == "Asia/Tokyo"                                  # explicit setting wins


def test_receiver_position_env_then_profile_then_none(tmp_path, monkeypatch):
    p = ref()
    monkeypatch.setenv("ULTRAFEEDER_LAT", "51.5")
    monkeypatch.setenv("ULTRAFEEDER_LON", "-0.1")
    assert dp.receiver_position(p) == (51.5, -0.1, "env")
    monkeypatch.delenv("ULTRAFEEDER_LAT")
    assert dp.receiver_position(p) is None                                      # no silent city default
    q = dp.load(_write(tmp_path, lambda d: d["geography"]["home_base"].update(latitude=10.0, longitude=20.0)))
    assert dp.receiver_position(q) == (10.0, 20.0, "profile")


def test_geography_provider_radius_query():
    p = ref()
    near_dca = {a.icao for a in dp.airports_within(38.852, -77.038, 15, p)}
    assert "KDCA" in near_dca and "KBWI" not in near_dca
    assert dp.airport("DCA", p).icao == "KDCA" and dp.airport("ZZZZ", p) is None


def test_every_profile_passes_the_strict_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPO / "docs/deployment-profiles/schemas/profile.schema.json").read_text())
    files = list((REPO / "docs/deployment-profiles/profiles").glob("*.json")) + [dp.REFERENCE_PATH] \
        + list((REPO / "docs/deployment-profiles/private").glob("*.json"))
    for f in files:
        jsonschema.validate(json.loads(f.read_text()), schema)
    assert len(files) >= 10


def test_the_live_profile_stays_private():
    import importlib.util
    import sys
    scrub = REPO / "scripts" / "scrub-public-tree.py"
    if not scrub.is_file():                      # the public edition does not ship the scrubber
        pytest.skip("public edition")
    spec = importlib.util.spec_from_file_location("scrub_public_tree_dp", scrub)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    assert "docs/deployment-profiles/private" in m.DROP_DIRS
