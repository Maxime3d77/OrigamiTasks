"""Bundled PRA engines, selected vault identities, memory-only SSH handoff.

Real PVE/LVM operations are deliberately NOT exercised in CI.
"""
import ast
import subprocess
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
from fastapi.testclient import TestClient
from app import pra_direct_jobs
from app.main import app

ROOT=Path(__file__).resolve().parents[1]


def test_linux_agent_bundles_root_owned_pra_engine():
    for name in ("pra-direct-seed.sh","pra-incremental-cold.sh"):
        subprocess.run(["bash","-n",str(ROOT/"agents/linux"/name)],check=True)
    subprocess.run(["python3","-m","py_compile",
                    str(ROOT/"agents/linux/pra-sync-report.py")],check=True)
    deploy=(ROOT/"app/agents.py").read_text()
    update=(ROOT/"app/agent_updates.py").read_text()
    for filename in ("pra-direct-seed.sh","pra-incremental-cold.sh","pra-sync-report.py"):
        assert filename in deploy
        assert filename in update
    assert "install -m 500 -o root -g root" in deploy
    assert "/usr/local/lib/origamitasks/pra/" in update
    assert "origamitasks-agent/agent.py" in deploy


def test_pra_job_uses_ephemeral_ssh_private_key_memory_only():
    ast.parse(pra_direct_jobs._REMOTE_PY)
    assert "os.memfd_create(" in pra_direct_jobs._REMOTE_PY
    assert "pass_fds=(k,h)" in pra_direct_jobs._REMOTE_PY
    assert 'PRA_PINNED_KNOWN_HOSTS' in pra_direct_jobs._REMOTE_PY
    source=(ROOT/"app/pra_direct_jobs.py").read_text()
    assert "input_.channel.sendall(payload)" in source
    assert "client_source.exec_command(cmd" in source
    assert "_known_host_line" in source
    assert "source_credential_id" in source
    assert "target_credential_id" in source
    assert "ALLOW_PROXMOX_ACTIONS" not in source


def test_vault_key_can_be_unlocked_in_memory():
    private=Ed25519PrivateKey.generate().private_bytes(
        Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()
    ).decode()
    exported=pra_direct_jobs._plain_key({"private_key":private})
    assert "OPENSSH PRIVATE KEY" in exported
    assert "BEGIN" in exported
    # Do not print or log the key.


def test_pra_job_endpoints_require_admin():
    with TestClient(app) as client:
        assert client.get("/api/dr/vms/1/direct-jobs").status_code==401
        assert client.post("/api/dr/vms/1/direct-jobs",json={}).status_code in (401,403)


def test_pra_ui_has_real_seed_and_incremental_buttons():
    src=(ROOT/"static/app.js").read_text()
    for token in ("Copie initiale directe","Tester la réplication à chaud",
                  "source_credential_id","target_credential_id",
                  "drDirectPRAForm","/direct-jobs"):
        assert token in src
