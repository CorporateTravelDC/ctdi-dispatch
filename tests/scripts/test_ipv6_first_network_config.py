"""2026-11-17 (operator: IPv6 priority; NM connectivity check off until Starlink).
The polkit rule grants only the operator account the two NetworkManager actions the failover watchdog
needs; gai.conf no longer prefers IPv4; NM's connectivity check is off."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULE = (ROOT / "config/polkit/49-ctdc-net-failover.rules").read_text()


def test_polkit_rule_is_scoped_to_the_operator_and_two_actions():
    code = "\n".join(l for l in RULE.splitlines() if not l.strip().startswith("//"))
    # 2026-11-18: the guarded rule rejects everything else first (!=), then evaluates the holds
    assert re.findall(r'subject\.user\s*[!=]=\s*"([^"]+)"', code) == ["corporatetraveldc"]
    assert sorted(re.findall(r'action\.id\s*[!=]=\s*"([^"]+)"', code)) == [
        "org.freedesktop.NetworkManager.network-control",
        "org.freedesktop.NetworkManager.settings.modify.system"]
    assert "subject.isInGroup" not in code and "polkit.Result.YES" in code
    assert code.count("polkit.addRule") == 1


def test_gai_conf_does_not_prefer_ipv4():
    lines = [l.split("#")[0].strip() for l in (ROOT / "config/network/gai.conf").read_text().splitlines()]
    assert not any(l.startswith("precedence ::ffff:0:0/96") for l in lines)


def test_nm_connectivity_check_is_off():
    conf = (ROOT / "config/network/zz-ctdc-connectivity.conf").read_text()
    body = [l.strip() for l in conf.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert body == ["[connectivity]", "enabled=false"]
