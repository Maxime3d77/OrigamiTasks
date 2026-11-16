"""Regression tests: Proxmox web authorization, PEM imports and HTML notifications."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app import config, db, integrations, ldap_settings, notifications, security
from app.main import app


def test_proxmox_admin_saved_endpoint_needs_no_env_allowlist(monkeypatch):
    monkeypatch.setattr(config, "PROXMOX_ALLOWED_HOSTS", "other-pve.example.org:8006")
    assert integrations.authorized_origin(
        "https://pve100.infra.example.org:8006",config.PROXMOX_ALLOWED_HOSTS,
        admin_proxmox=True
    )=="https://pve100.infra.example.org:8006"
    with pytest.raises(integrations.IntegrationError):
        integrations.authorized_origin(
            "https://pve100.infra.example.org:8006",config.PROXMOX_ALLOWED_HOSTS)
    for address in ("https://127.0.0.1:8006","https://169.254.169.254:8006",
                    "http://pve100.infra.example.org:8006","https://pve100.infra.example.org:8080"):
        with pytest.raises(integrations.IntegrationError):
            integrations.authorized_origin(address, "", admin_proxmox=True)


def test_ui_can_register_proxmox_dc_and_token_without_env(monkeypatch):
    monkeypatch.setattr(config,"PROXMOX_ALLOWED_HOSTS","different.example.org:8006")
    with TestClient(app) as client:
        login=client.post("/api/login",json={
            "username":"admin","password":"test-admin-password-over-12"
        })
        assert login.status_code==200,login.text
        hdr={"X-CSRF-Token":client.cookies["origami_csrf"]}
        cred=client.post("/api/credentials",headers=hdr,json={
            "name":"ci-proxmox-notification-010",
            "kind":"proxmox",
            "payload":{"url":"https://pve100.infra.example.org:8006",
                       "username":"user@pve!origami","token":"top-secret-placeholder"}
        })
        assert cred.status_code==200,cred.text
        cid=cred.json()["id"]
        try:
            wrong=client.post("/api/datacenters",headers=hdr,json={
                "name":"wrong-target-ci-010","url":"https://attacker.example.org:8006",
                "credential_id":cid,
            })
            assert wrong.status_code==422
            good=client.post("/api/datacenters",headers=hdr,json={
                "name":"dc-web-ci-010","url":"https://pve100.infra.example.org:8006",
                "credential_id":cid,
            })
            assert good.status_code==200,good.text
            assert client.delete("/api/datacenters/"+str(good.json()["id"]),
                                 headers=hdr).status_code==200
        finally:
            assert client.delete("/api/credentials/"+str(cid),headers=hdr).status_code==200


def test_ldap_openssl_export_with_bag_headers_imports_ca():
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"EDD Test CA")])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(days=1))
        .not_valid_after(now+timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
        .sign(key,hashes.SHA256()))
    pem=cert.public_bytes(serialization.Encoding.PEM).decode()
    export="Bag Attributes\n    friendlyName: EDD root ca\nsubject=CN=EDD Test CA\nissuer=CN=EDD Test CA\n"+pem
    result=ldap_settings.import_ca(pem_text=export)
    try:
        assert result["certificate_count"]==1
        assert "EDD Test CA" in result["ca_subjects"][0]
    finally:
        from pathlib import Path
        Path(result["path"]).unlink(missing_ok=True)
    with pytest.raises(ldap_settings.LDAPSettingsError,match="Données non reconnues"):
        ldap_settings.import_ca(pem_text="unexpected text\n"+pem)


def test_html_notification_subject_and_redacted_diagnostics(monkeypatch):
    messages=[]
    class FakeSMTP:
        def send_message(self,message):messages.append(message)
        def quit(self):pass
    monkeypatch.setattr(notifications.connections,"open_smtp",
                        lambda profile,timeout: FakeSMTP())
    monkeypatch.setattr(notifications.executor,"credential",lambda cid:{
        "host":"smtp.example.org","from_address":"alerts@example.org",
        "tls_mode":"starttls","auth":False
    })
    monkeypatch.setattr(notifications,"_secrets",lambda:{"my-secret-token"})
    with db.session() as con:
        rid=con.execute(
            "INSERT INTO runs (job_name,steps_snapshot,status,trigger,requested_by,queued_at) "
            "VALUES (?,?,?,?,?,?)",
            ("Maintenance Proxmox","[]","failed","manual","admin",db.now())
        ).lastrowid
        con.execute(
            "INSERT INTO step_runs (run_id,step_id,name,type,status,attempt,exit_code,output,started_at,finished_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid,"x","Contrôle PVE","proxmox","failed",2,403,
             "PVE API error 403; token=my-secret-token",db.now(),db.now())
        )
    run={
        "job_name":"Maintenance Proxmox","trigger":"manual","requested_by":"admin",
        "notification_json":json.dumps({
            "enabled":True,"credential_id":99,"recipients":"alerts@example.org",
            "on_failure":True,"on_success":True
        })
    }
    try:
        assert notifications.send(rid,run,"failed","Erreur du workflow")[0]=="sent"
        failure=messages[-1]
        assert "[ÉCHEC]" in failure["Subject"]
        assert "my-secret-token" not in failure.as_string()
        assert "[SECRET]" in failure.as_string()
        assert "403" in failure.as_string()
        assert "text/html" in failure.as_string()
        assert notifications.send(rid,run,"success","")[0]=="sent"
        assert "[SUCCÈS]" in messages[-1]["Subject"]
    finally:
        with db.session() as con:
            con.execute("DELETE FROM step_runs WHERE run_id=?",(rid,))
            con.execute("DELETE FROM runs WHERE id=?",(rid,))


def test_notification_columns_exist_in_upgraded_database():
    db.initialize()
    with db.session() as con:
        assert "notification_json" in {
            row["name"] for row in con.execute("PRAGMA table_info(jobs)")
        }
        assert {"notification_json","notification_status","notification_error"} <= {
            row["name"] for row in con.execute("PRAGMA table_info(runs)")
        }
