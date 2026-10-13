"""2026-10-13 ntfy audit: the FDC-NOTAM path sends K-prefixed center codes (KZDC); they
resolved to OTHER, so ZDC restriction NOTAMs never reached fdps-zdc."""
from shared import sector_coalesce as sc


def test_k_prefixed_center_codes_resolve_like_the_bare_code():
    for k, bare in (("KZDC", "ZDC"), ("KZNY", "ZNY"), ("kztl", "ZTL"), (" KZSE ", "ZSE")):
        assert sc.sector_ntfy_topic(k, "fdps") == sc.sector_ntfy_topic(bare, "fdps") is not None
        assert sc.resolve_sector(k) == sc.resolve_sector(bare)
        assert sc.is_tracked_facility(k)
    assert sc.sector_ntfy_topic("KZDC", "fdps") == "fdps-zdc" and sc.resolve_sector("KZDC") == "DC_LOCAL"


def test_airports_and_unknown_codes_are_unchanged():
    assert sc.sector_ntfy_topic("KDCA", "stdds") == "stdds-zdc"
    assert sc.sector_ntfy_topic("KZPH", "fdps") is None              # a real KZ.. airport, not a center
    assert sc.resolve_sector(None) == "OTHER" and sc.sector_ntfy_topic("", "fdps") is None
