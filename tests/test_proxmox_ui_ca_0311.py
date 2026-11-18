"""Admin web workflow for standalone Proxmox servers, clusters, and imported TLS CAs."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
import pytest

from app import config, db, proxmox_cas, integrations, security
from app.main import app


def ca_pem():
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"Proxmox Test Root CA")])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(days=1))
        .not_valid_after(now+timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
        .sign(key,hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def auth(client):
    response=client.post("/api/login",json={"username":"admin",
                                            "password":"test-admin-password-over-12"})
    assert response.status_code==200,response.text
    return {"X-CSRF-Token":client.cookies["origami_csrf"]}


def test_web_manage_proxmox_without_env_or_docker_restart(monkeypatch):
    monkeypatch.setattr(config,"PROXMOX_ALLOWED_HOSTS","some-other-dc.infra.example.org:8006")
    monkeypatch.setattr(config,"PROXMOX_CA_CERT","")
    with TestClient(app) as client:
        headers=auth(client)
        ca=client.post("/api/proxmox/cas",headers=headers,json={"pem_text":ca_pem()})
        assert ca.status_code==200,ca.text
        caid=ca.json()["id"]
        assert len(caid)==64 and proxmox_cas.filename(caid).is_file()
        assert any(x["id"]==caid for x in client.get("/api/proxmox/cas").json()["items"])
        secret="token-secret-for-proxmox-ci"
        new=client.post("/api/datacenters",headers=headers,json={
            "name":"SD Test Cluster", "kind":"cluster",
            "url":"https://pve-test.infra.example.org:8006",
            "token_username":"automation@pve!origami","token_secret":secret,
            "ca_cert":caid})
        assert new.status_code==200,new.text
        dcid=new.json()["id"]
        try:
            rows=client.get("/api/datacenters").json()["items"]
            dc=next(x for x in rows if x["id"]==dcid)
            assert dc["kind"]=="cluster"
            assert dc["ca_cert"]==caid
            assert secret not in str(dc)
            with db.session() as con:
                cred=con.execute(
                    "SELECT encrypted_payload FROM credentials WHERE id=?",
                    (dc["credential_id"],)).fetchone()
            values=security.decrypt(cred["encrypted_payload"])
            assert values["username"]=="automation@pve!origami"
            assert values["token"]==secret
            assert values["ca_cert"]==caid
            assert proxmox_cas.verify_value(values["ca_cert"])==str(proxmox_cas.filename(caid))
            edited=client.put("/api/datacenters/"+str(dcid),headers=headers,json={
                "name":"SD Test Node", "kind":"node",
                "url":"https://pve-test.infra.example.org:8006",
                "credential_id":dc["credential_id"],"ca_cert":caid})
            assert edited.status_code==200,edited.text
            dc=next(x for x in client.get("/api/datacenters").json()["items"] if x["id"]==dcid)
            assert dc["name"]=="SD Test Node" and dc["kind"]=="node"
            invalid=client.put("/api/datacenters/"+str(dcid),headers=headers,json={
                "name":"Hijack", "kind":"node",
                "url":"https://other-node.infra.example.org:8006",
                "credential_id":dc["credential_id"],"ca_cert":caid})
            assert invalid.status_code==422
        finally:
            assert client.delete("/api/datacenters/"+str(dcid),headers=headers).status_code==200
            assert client.delete("/api/credentials/"+str(dc["credential_id"]),headers=headers).status_code==200


def test_proxmox_ca_only_uses_known_uploaded_files():
    with pytest.raises(proxmox_cas.ProxmoxCAError):
        proxmox_cas.verify_value("../../etc/ssl/ca.pem")
    with pytest.raises(proxmox_cas.ProxmoxCAError):
        proxmox_cas.verify_value("a"*64)


def test_proxmox_request_picks_ca_from_credential(monkeypatch):
    path=proxmox_cas.import_public(pem_text=ca_pem())["id"]
    seen=[]
    class FakeResponse:
        def raise_for_status(self):pass
        def json(self):return {"data":{"ok":1}}
    class FakeHTTP:
        def __init__(self,**kwargs):
            seen.append(kwargs)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def request(self,method,url,headers,data):
            assert url=="https://pve-tests.example.org:8006/api2/json/version"
            assert headers["Authorization"]=="PVEAPIToken=bot@pve!ci=redacted-test-secret"
            return FakeResponse()
    monkeypatch.setattr(integrations.httpx,"Client",FakeHTTP)
    try:
        assert integrations.proxmox_request(
            "https://pve-tests.example.org:8006",
            {"username":"bot@pve!ci","token":"redacted-test-secret","ca_cert":path},
            "/api2/json/version"
        )=={"ok":1}
        assert seen[0]["verify"]==str(proxmox_cas.filename(path))
        assert seen[0]["follow_redirects"] is False
    finally:
        proxmox_cas.filename(path).unlink(missing_ok=True)


def test_proxmox_admin_endpoints_require_login():
    with TestClient(app) as client:
        assert client.get("/api/proxmox/cas").status_code==401
        assert client.post("/api/proxmox/cas",json={"pem_text":ca_pem()}).status_code in (401,403)
