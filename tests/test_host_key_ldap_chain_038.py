"""SSH host-key approval is explicit and safe; LDAP imports only CAs from bundles.

All SSH services are simulated; no network or private key leaves the test runner.
"""
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paramiko
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app import config, host_keys, ldap_settings
from app.main import app


def login():
    client=TestClient(app)
    client.__enter__()
    response=client.post("/api/login",json={
        "username":"admin","password":"test-admin-password-over-12"
    })
    assert response.status_code==200,response.text
    return client,{"X-CSRF-Token":client.cookies["origami_csrf"]}


def test_host_key_inspection_and_explicit_verified_approval(monkeypatch,tmp_path):
    key=paramiko.RSAKey.generate(1024)
    monkeypatch.setattr(host_keys,"_fetch",lambda host,port:key)
    monkeypatch.setattr(config,"KNOWN_HOSTS",tmp_path/"known_hosts")
    client,headers=login()
    try:
        inspection=client.post("/api/ssh/host-key/inspect",headers=headers,json={
            "host":"serveur.fr","port":22
        })
        # A host-known-key file may only be managed inside persistent data.
        assert inspection.status_code==422
        monkeypatch.setattr(config,"KNOWN_HOSTS",config.DATA/"ci-038-knownhosts")
        managed=config.KNOWN_HOSTS
        managed.unlink(missing_ok=True)
        offered=client.post("/api/ssh/host-key/inspect",headers=headers,json={
            "host":"serveur.fr","port":22
        })
        assert offered.status_code==200,offered.text
        expected=offered.json()["fingerprint"]
        assert expected.startswith("SHA256:")
        assert offered.json()["status"]=="unknown"
        target={"host":"serveur.fr","port":22,"fingerprint":expected,
                "verified_out_of_band":True}
        assert client.post("/api/ssh/host-key/approve",json=target).status_code==403
        not_verified=client.post("/api/ssh/host-key/approve",headers=headers,json={
            **target,"verified_out_of_band":False
        })
        assert not_verified.status_code==422
        mismatch=client.post("/api/ssh/host-key/approve",headers=headers,json={
            **target,"fingerprint":"SHA256:incorrect"
        })
        assert mismatch.status_code==422
        assert not managed.exists()
        approved=client.post("/api/ssh/host-key/approve",headers=headers,json=target)
        assert approved.status_code==200,approved.text
        assert approved.json()["ok"] and not approved.json()["already_trusted"]
        assert managed.is_file()
        known=paramiko.HostKeys()
        known.load(str(managed))
        assert known.lookup("serveur.fr")[key.get_name()]==key
        assert client.post("/api/ssh/host-key/inspect",headers=headers,json={
            "host":"serveur.fr","port":22
        }).json()["trusted"]
        repeat=client.post("/api/ssh/host-key/approve",headers=headers,json=target)
        assert repeat.status_code==200 and repeat.json()["already_trusted"]
        assert managed.read_text().count("serveur.fr")==1
    finally:
        managed.unlink(missing_ok=True)
        client.__exit__(None,None,None)


def test_changed_ssh_host_key_is_not_overwritten(monkeypatch):
    first=paramiko.RSAKey.generate(1024)
    second=paramiko.RSAKey.generate(1024)
    path=config.DATA/"ci-038-changed-host"
    path.write_text("serveur.fr "+first.get_name()+" "+first.get_base64()+"\n")
    monkeypatch.setattr(config,"KNOWN_HOSTS",path)
    monkeypatch.setattr(host_keys,"_fetch",lambda host,port:second)
    client,headers=login()
    try:
        inspected=client.post("/api/ssh/host-key/inspect",headers=headers,json={
            "host":"serveur.fr","port":22
        })
        assert inspected.status_code==200
        assert inspected.json()["status"]=="changed"
        response=client.post("/api/ssh/host-key/approve",headers=headers,json={
            "host":"serveur.fr","port":22,
            "fingerprint":inspected.json()["fingerprint"],
            "verified_out_of_band":True
        })
        assert response.status_code==422
        assert first.get_base64() in path.read_text()
        assert second.get_base64() not in path.read_text()
    finally:
        path.unlink(missing_ok=True)
        client.__exit__(None,None,None)


def test_custom_ssh_port_pins_bracketed_hostname(monkeypatch):
    key=paramiko.RSAKey.generate(1024)
    monkeypatch.setattr(host_keys,"_fetch",lambda host,port:key)
    path=config.DATA/"ci-038-port"
    path.unlink(missing_ok=True)
    monkeypatch.setattr(config,"KNOWN_HOSTS",path)
    result=host_keys.approve("serveur.fr",2222,host_keys._fingerprint(key),True)
    try:
        assert result["ok"]
        known=paramiko.HostKeys()
        known.load(str(path))
        assert known.lookup("[serveur.fr]:2222")[key.get_name()]==key
    finally:
        path.unlink(missing_ok=True)


def _generate_ca_and_leaf():
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    leaf_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"Example Root CA")])
    leaf_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"server.example.org")])
    now=datetime.now(timezone.utc)
    root=(x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(days=1))
          .not_valid_after(now+timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
          .sign(ca_key,hashes.SHA256()))
    leaf=(x509.CertificateBuilder().subject_name(leaf_name).issuer_name(ca_name)
          .public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(days=1))
          .not_valid_after(now+timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
          .sign(ca_key,hashes.SHA256()))
    return root,leaf


def test_ca_chain_with_leaf_keeps_only_valid_ca_and_reports_ignored_leaf():
    root,leaf=_generate_ca_and_leaf()
    pem=(leaf.public_bytes(serialization.Encoding.PEM)
         +root.public_bytes(serialization.Encoding.PEM)).decode()
    result=ldap_settings.import_ca(pem_text=pem)
    path=Path(result["path"])
    try:
        assert result["certificate_count"]==1
        assert result["ignored_non_ca_count"]==1
        assert "Example Root CA" in result["ca_subjects"][0]
        trust=path.read_text()
        assert trust.count("-----BEGIN CERTIFICATE-----")==1
        assert x509.load_pem_x509_certificate(trust.encode()).subject==root.subject
    finally:
        path.unlink(missing_ok=True)


def test_leaf_only_bundle_is_rejected_and_ca_only_is_accepted():
    root,leaf=_generate_ca_and_leaf()
    from app.ldap_settings import LDAPSettingsError
    import pytest
    with pytest.raises(LDAPSettingsError,match="uniquement des certificats serveur"):
        ldap_settings.import_ca(pem_text=leaf.public_bytes(serialization.Encoding.PEM).decode())
    r=ldap_settings.import_ca(pem_text=root.public_bytes(serialization.Encoding.PEM).decode())
    try:
        assert r["certificate_count"]==1 and r["ignored_non_ca_count"]==0
    finally:
        Path(r["path"]).unlink(missing_ok=True)


def test_ui_has_ssh_trust_wizard_with_clear_confirmation():
    root=Path(__file__).resolve().parents[1]
    code=(root/"static/app.js").read_text()
    styles=(root/"static/styles.css").read_text()
    for token in ("ssh-hostkey-inspect","ssh-hostkey-approve",
                  "sshTrustTarget","verified_fingerprint",
                  "verified_out_of_band","ssh-trust-fingerprint"):
        assert token in code or token in styles
    assert "CA importée" in code
    assert "ignored_non_ca_count" in code
