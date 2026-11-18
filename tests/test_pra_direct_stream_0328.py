"""Direct SSH seed is a deliberate, one-shot source->target backup stream."""
from pathlib import Path
import subprocess

SCRIPT=Path(__file__).resolve().parents[1]/"scripts"/"pra-direct-seed.sh"


def test_shell_syntax():
    subprocess.run(["bash","-n",str(SCRIPT)],check=True)


def test_direct_seed_cannot_overwrite_or_start():
    text=SCRIPT.read_text()
    assert "set -Eeuo pipefail" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "BatchMode=yes" in text
    assert "ForwardAgent=no" in text
    assert 'vzdump "$vmid" --mode snapshot --stdout --compress 0 | ssh' in text
    assert "qmrestore - '$targetvmid'" in text
    assert "--start 0" in text
    assert "--unique 1" in text
    assert "--onboot 0" in text
    assert "--force" not in text
    assert "pbs" not in text.lower() or "no pbs" in text.lower()


def test_seed_requires_approval():
    run=subprocess.run(["bash",str(SCRIPT),"100","au.invalid","900100","local-lvm","--not-approved"],
                       capture_output=True,text=True)
    assert run.returncode!=0
    assert "Confirmation required" in run.stderr
