"""Exercise the real mirror-check loop against isolated installed/tracked trees."""
from pathlib import Path
import shlex,subprocess

REPO=Path(__file__).resolve().parents[2]


def run_check(tmp_path,installed=None):
    tracked=tmp_path/"repo/.config/systemd/user";tracked.mkdir(parents=True)
    (tracked/"humans.slice").write_text("[Slice]\nCPUQuota=150%\n")
    (tracked/"example.timer").write_text("[Timer]\nOnCalendar=daily\n")
    home=tmp_path/"synthetic-home";home.mkdir()
    if installed is not None:
        live=home/".config/systemd/user";live.mkdir(parents=True)
        (live/"humans.slice").write_text(installed)
    src=(REPO/"scripts/check-platform-drift.sh").read_text()
    loop=src[src.index("for rel in .config/systemd/user"):src.index("# -- 12. tracked cloudflared")]
    script="DRIFT=0\n"+f"HOME_DIR={shlex.quote(str(home))}\n"+'ok() { printf \'[OK] %s\n\' "$*"; }\nwarn() { printf \'[WARN] %s\n\' "$*"; }\ndrift() { DRIFT=1; printf \'[DRIFT] %s\n\' "$*"; }\n'+loop+'\nexit "$DRIFT"\n'
    return subprocess.run(["bash"],input=script,cwd=tracked.parents[2],capture_output=True,text=True,timeout=10)


def test_absent_human_template_is_intentional_but_other_missing_units_still_warn(tmp_path):
    r=run_check(tmp_path)
    assert r.returncode==0,r.stderr
    assert "[OK] humans.slice is an opt-in multi-human deployment template" in r.stdout
    assert "[WARN] tracked .config/systemd/user/example.timer" in r.stdout
    assert "[WARN] tracked .config/systemd/user/humans.slice" not in r.stdout


def test_installed_human_template_mismatch_is_still_drift(tmp_path):
    r=run_check(tmp_path,"[Slice]\nCPUQuota=200%\n")
    assert r.returncode==1,r.stderr
    assert "[DRIFT]" in r.stdout and "humans.slice" in r.stdout
