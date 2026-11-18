"""Proxmox SSH agent rollout: automatic first-contact key pinning and change refusal.

The RSA keys are generated locally for tests. No network, deployment or
user SSH private key is involved in host-key trust.
"""
import pytest
from app import host_keys


def test_pve_first_contact_pins_without_manual_fingerprint(monkeypatch,tmp_path):
    import paramiko
    path=tmp_path/"known_hosts"
    first=paramiko.RSAKey.generate(1024)
    second=paramiko.RSAKey.generate(1024)
    monkeypatch.setattr(host_keys,"_trusted_path",lambda:path)
    monkeypatch.setattr(host_keys,"_fetch",lambda host,port:first)
    initial=host_keys.trust_first_use("node01.example.org",22)
    assert initial["added"] is True
    assert initial["status"]=="added"
    assert initial["fingerprint"].startswith("SHA256:")
    original=path.read_text()
    assert "node01.example.org ssh-rsa " in original
    assert first.get_base64() in original
    assert second.get_base64() not in original
    assert (path.stat().st_mode & 0o777)==0o600

    # Reconnect using the same host key: no duplicate or rewrite.
    repeated=host_keys.trust_first_use("node01.example.org",22)
    assert repeated["added"] is False
    assert repeated["status"]=="trusted"
    assert path.read_text()==original

    # A changed key may NOT be silently accepted, regardless of user key.
    monkeypatch.setattr(host_keys,"_fetch",lambda host,port:second)
    with pytest.raises(host_keys.HostKeyError,match="modifiée"):
        host_keys.trust_first_use("node01.example.org",22)
    assert path.read_text()==original


def test_custom_port_uses_port_qualified_pin(monkeypatch,tmp_path):
    import paramiko
    key=paramiko.RSAKey.generate(1024)
    path=tmp_path/"known_hosts"
    monkeypatch.setattr(host_keys,"_trusted_path",lambda:path)
    monkeypatch.setattr(host_keys,"_fetch",lambda host,port:key)
    result=host_keys.trust_first_use("node02.example.org",2222)
    assert result["port"]==2222
    assert "[node02.example.org]:2222 ssh-rsa" in path.read_text()
    assert host_keys.trust_first_use("node02.example.org",2222)["added"] is False


def test_tofu_conflicting_algorithm_is_refused(monkeypatch,tmp_path):
    import paramiko
    rsa=paramiko.RSAKey.generate(1024)
    ecdsa=paramiko.ECDSAKey.generate()
    path=tmp_path/"known_hosts"
    path.write_text("node03.example.org "+rsa.get_name()+" "+rsa.get_base64()+"\n")
    monkeypatch.setattr(host_keys,"_trusted_path",lambda:path)
    monkeypatch.setattr(host_keys,"_fetch",lambda host,port:ecdsa)
    with pytest.raises(host_keys.HostKeyError,match="différente"):
        host_keys.trust_first_use("node03.example.org",22)
    assert path.read_text().count("\n")==1


def test_fleet_ui_explains_tofu_and_private_key_difference():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    js=(root/"static/app.js").read_text()
    batch=(root/"app/agent_deployments.py").read_text()
    assert "Première clé d’hôte SSH mémorisée automatiquement" in js
    assert "Aucune empreinte à confirmer" in js
    assert "trust_first_use(target['host'],target['port'])" in batch
    assert "ssh.host-key.first-use" in batch
    assert "Clé SSH approuvée par hôte obligatoire" not in js
