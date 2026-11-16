"""Security and release regression coverage; no real external host is contacted."""
import importlib.util
import json
from pathlib import Path
import ssl
import subprocess
import hashlib
import base64

import paramiko
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, NoEncryption, BestAvailableEncryption
)

from app import config, ldap_settings, connection_tests
from app.ssh_keys import SSHUnknownHostKey, SSHKeyError, strict_host_key_policy, load_private_key


def test_user_openssh_private_key_and_encrypted_key_are_readable():
    key=Ed25519PrivateKey.generate()
    raw=key.private_bytes(Encoding.PEM,PrivateFormat.OpenSSH,NoEncryption()).decode()
    assert "BEGIN OPENSSH PRIVATE KEY" in raw
    obj=load_private_key(raw)
    assert isinstance(obj,paramiko.Ed25519Key)

    secret=key.private_bytes(
        Encoding.PEM,PrivateFormat.OpenSSH,BestAvailableEncryption(b"pass phrase")
    ).decode()
    with pytest.raises(SSHKeyError,match="phrase secrète"):
        load_private_key(secret)
    assert isinstance(load_private_key(secret,"pass phrase"),paramiko.Ed25519Key)

    with pytest.raises(SSHKeyError,match="Clé publique"):
        load_private_key("ssh-ed25519 "+base64.b64encode(key.public_key().public_bytes_raw()).decode())


def test_unknown_host_fingerprint_reported_but_key_is_not_trusted():
    key=paramiko.RSAKey.generate(1024)
    policy=strict_host_key_policy()
    with pytest.raises(SSHUnknownHostKey) as error:
        policy.missing_host_key(None,"serveur.fr",key)
    fp="SHA256:"+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    assert fp in str(error.value)
    assert "Aucune clé n'a été acceptée automatiquement" in str(error.value)


def test_diagnostic_separately_identifies_unknown_host(monkeypatch):
    key=paramiko.RSAKey.generate(1024)
    class FakeSSH:
        def load_system_host_keys(self):pass
        def set_missing_host_key_policy(self,policy):self.policy=policy
        def connect(self,**kwargs):
            self.policy.missing_host_key(self,kwargs["hostname"],key)
        def close(self):pass
    monkeypatch.setattr(paramiko,"SSHClient",FakeSSH)
    with pytest.raises(connection_tests.ConnectionTestError,match="Clé d'hôte SSH inconnue") as error:
        connection_tests.ssh({"host":"serveur.fr","username":"deploy","password":"test-password"})
    assert "SHA256:" in str(error.value)


def test_ldap_skip_tls_trust_requires_both_saved_optin_and_host_authorization(monkeypatch):
    settings=ldap_settings.defaults()
    settings["ca_cert"]=""
    monkeypatch.setattr(config,"ALLOW_INSECURE_LDAP_TLS",False)
    with pytest.raises(ldap_settings.LDAPSettingsError,match="ALLOW_INSECURE_LDAP_TLS"):
        ldap_settings.tls_for(dict(settings,skip_cert_verification=True))
    monkeypatch.setattr(config,"ALLOW_INSECURE_LDAP_TLS",True)
    strict=ldap_settings.tls_for(dict(settings,skip_cert_verification=False))
    unsafe=ldap_settings.tls_for(dict(settings,skip_cert_verification=True))
    assert strict.validate==ssl.CERT_REQUIRED
    assert unsafe.validate==ssl.CERT_NONE
    settings["uri"]="ldaps://directory.example.org:636"
    other=ldap_settings.tls_for(dict(settings,skip_cert_verification=True),
                                uri="ldaps://other.example.org:636")
    assert other.validate==ssl.CERT_REQUIRED


def test_update_worker_preflight_never_merges_or_restarts(tmp_path,monkeypatch):
    repo_root=Path(__file__).resolve().parents[1]
    path=repo_root/"ops"/"update-worker.py"
    spec=importlib.util.spec_from_file_location("update_worker_037",path)
    worker=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    data=tmp_path/"data"
    data.mkdir()
    (data/"origamitasks.db").write_bytes(b"stub for backup preflight")
    calls=[]
    def fake_execute(args,root,timeout=600):
        calls.append(tuple(args))
        joined=" ".join(args)
        if "remote get-url" in joined: return "https://github.com/example/OrigamiTasks.git"
        if "status --porcelain" in joined:return ""
        if "rev-parse HEAD" in joined:return "abc123"
        if "rev-parse FETCH_HEAD" in joined:return "def456"
        if "rev-list --count" in joined:return "1"
        if "show -s --format=%cI" in joined:return "2026-11-16T10:00:00+00:00"
        if "show HEAD:app/config.py" in joined:return "APP_VERSION = '0.3.6-rc1'"
        if "show FETCH_HEAD:app/config.py" in joined:return "APP_VERSION = '0.3.7-rc1'"
        if "docker compose config" in joined:return ""
        if "docker compose version" in joined:return "Docker Compose version v2"
        return ""
    changes=[]
    monkeypatch.setattr(worker,"execute",fake_execute)
    monkeypatch.setattr(worker,"state_write",lambda *a,**kw:changes.append((a,kw)))
    monkeypatch.setattr(worker.subprocess,"run",
        lambda *a,**kw:subprocess.CompletedProcess(a[0],0))
    result=worker.process(tmp_path,"preflight","main",
                          "https://github.com/example/OrigamiTasks.git")
    assert "aucune mise à jour installée" in result
    assert changes[-1][1]["preflight_ok"] is True
    assert not any("compose up" in " ".join(args) or "git merge " in " ".join(args)
                   for args in calls)


def test_release_workflow_and_browser_preflight_contract():
    root=Path(__file__).resolve().parents[1]
    release=(root/".github/workflows/release.yml").read_text()
    js=(root/"static/app.js").read_text()
    assert "gh release create" in release
    assert "--verify-tag" in release
    assert "python -m pytest -q" in release
    assert "APP_VERSION" in release
    assert "preflight" in js and "worker_online" in js
    assert "update-worker-heartbeat.json" in (root/"app/main.py").read_text()
    assert "ALLOW_INSECURE_LDAP_TLS=false" in (root/".env.example").read_text()



def test_published_docs_and_interface_use_generic_server_examples():
    root=Path(__file__).resolve().parents[1]
    for path in [*sorted((root/"docs").rglob("*.md")),
                 root/"README.md",root/"static"/"app.js"]:
        content=path.read_text()
        assert "vl-exploittools-01" not in content, path
        assert "infra.aday.fr" not in content, path
