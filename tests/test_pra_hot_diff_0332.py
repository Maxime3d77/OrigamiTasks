"""Live-source differential script is deliberately non-destructive on original VMs."""
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"agents/linux/pra-hot-diff.sh"

def test_syntax():
    subprocess.run(["bash","-n",str(SCRIPT)],check=True)

def test_never_stops_source_or_mutates_old_standby():
    text=SCRIPT.read_text()
    assert 'status: running' in text
    assert "vzdump \"$sid\" --mode snapshot --stdout --compress 0 | vma extract" in text
    assert "qm clone '$base' '$candidate' --full 1" in text
    assert "--write-devices" in text
    assert 'sha256sum "$raw"' in text
    assert "ORIGAMI_HOT_GENERATION_READY" in text
    for unsafe in ("qm stop ","qm shutdown ","qm suspend ","qm destroy ","lvremove ","--force"):
        assert unsafe not in text
def test_refuses_without_parameters():
    r=subprocess.run(["bash",str(SCRIPT)],capture_output=True,text=True)
    assert r.returncode!=0
