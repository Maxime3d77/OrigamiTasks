"""Regression: source SD stays running and unsafe cold sync cannot be queued."""
import ast
import subprocess
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app import pra_direct_jobs
from app.main import app

ROOT=Path(__file__).resolve().parents[1]


def test_agent_hot_qmp_probe_is_read_only():
    path=ROOT/"agents/linux/pra-hot-preflight.py"
    subprocess.run(["python3","-m","py_compile",str(path)],check=True)
    text=path.read_text()
    for command in ('query-block','query-commands','qmp_capabilities'):
        assert command in text
    for forbidden in ('execute("stop"','execute("quit"','execute("blockdev-backup"',
                      'execute("block-dirty-bitmap-add"','execute("block-dirty-bitmap-clear"'):
        assert forbidden not in text
    assert '"hot_incremental_ready"]=False' in text


def test_old_offline_executor_cannot_run_in_agent():
    script=ROOT/"agents/linux/pra-incremental-cold.sh"
    subprocess.run(["bash","-n",str(script)],check=True)
    result=subprocess.run(["bash",str(script)],text=True,capture_output=True)
    assert result.returncode==78
    assert "allumée" in result.stderr
    assert "rsync " not in script.read_text()


def test_controller_cannot_enqueue_cold_incremental():
    with pytest.raises(pra_direct_jobs.PRAJobError,match="incrémentale à chaud"):
        pra_direct_jobs._validate(1,"offline_incremental",1,2)
    assert "offline_incremental" not in {
        "seed"
    }
    with TestClient(app) as client:
        assert client.get("/api/dr/vms/1/hot-preflight?source_credential_id=1").status_code==401


def test_seed_script_never_powers_off_sd():
    text=(ROOT/"agents/linux/pra-direct-seed.sh").read_text()
    assert 'status: running' in text
    assert 'vzdump "$vmid" --mode snapshot --stdout' in text
    for forbidden in ("qm stop ","qm shutdown ","qm suspend ","pct stop "):
        assert forbidden not in text


def test_agent_upgrade_delivers_hot_probe():
    for path in ("app/agents.py","app/agent_updates.py"):
        assert "pra-hot-preflight.py" in (ROOT/path).read_text()
    ui=(ROOT/"static/app.js").read_text()
    assert "Tester la réplication à chaud" in ui
    assert "drHotPreflightForm" in ui
    assert "data-action=\"dr-vm-direct-incremental\"" not in ui
