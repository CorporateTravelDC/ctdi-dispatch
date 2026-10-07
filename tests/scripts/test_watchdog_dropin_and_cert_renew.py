"""Contract tests for two 2026-10-04 boot findings:
- the watchdog drop-in must sort AFTER the stock watchdog.conf (last one wins);
- cert renewal must never leave a failed nginx down (reload is a no-op on it)."""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DROPIN_DIR = REPO / "config/systemd/system.conf.d"


class WatchdogDropin(unittest.TestCase):
    def test_tracked_dropin_sorts_after_stock_files(self):
        ours = [p.name for p in DROPIN_DIR.glob("*ctdc-watchdog.conf")]
        self.assertEqual(len(ours), 1, ours)
        on_box = ["00-tuned.conf", "watchdog.conf", ours[0]]   # files present on the Pi
        self.assertEqual(sorted(on_box)[-1], ours[0])

    def test_tune_script_writes_the_tracked_name(self):
        text = (REPO / "scripts/watchdog-tune.sh").read_text()
        conf = re.search(r"^CONF=(\S+)$", text, re.M).group(1)
        self.assertEqual(Path(conf).name, next(DROPIN_DIR.glob("*ctdc-watchdog.conf")).name)
        self.assertIn('"$STALE_CONF"', text)


class CertRenewNginx(unittest.TestCase):
    def test_no_bare_reload(self):
        text = (REPO / "scripts/renew-tailscale-cert.sh").read_text()
        code = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
        self.assertFalse([l for l in code if re.search(r"systemctl reload nginx", l)])
        self.assertTrue([l for l in code if "reload-or-restart nginx" in l])
        self.assertTrue([l for l in code if "is-active --quiet nginx" in l])
