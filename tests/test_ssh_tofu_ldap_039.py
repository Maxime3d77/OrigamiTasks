"""Regression coverage: automatic SSH TOFU and schema-neutral LDAP diagnostics.

All SSH keys are generated for tests; no real server or user credential is accessed.
"""
from pathlib import Path

import paramiko
import pytest

from app import config, host_keys, ldap_settings, ssh_keys


def test_accept_new_persists_and_never_replaces_known_host(monkeypatch):
    path = config.DATA / "ci-039-auto-known-hosts"
    path.unlink(missing_ok=True)
    monkeypatch.setattr(config, "KNOWN_HOSTS", path)
    monkeypatch.setattr(config, "SSH_HOST_KEY_POLICY", "accept-new")
    first = paramiko.RSAKey.generate(1024)
    other = paramiko.RSAKey.generate(1024)
    client = paramiko.SSHClient()
    policy = ssh_keys.configured_host_key_policy()
    try:
        policy.missing_host_key(client, "test.internal", first)
        assert client._origami_added_host_key is True
        assert path.is_file()
        saved = paramiko.HostKeys()
        saved.load(str(path))
        assert saved.lookup("test.internal")[first.get_name()] == first

        policy.missing_host_key(client, "test.internal", first)
        assert client._origami_added_host_key is False
        assert path.read_text().count("test.internal") == 1

        with pytest.raises(ssh_keys.SSHUnknownHostKey, match="différente"):
            policy.missing_host_key(client, "test.internal", other)
        assert first.get_base64() in path.read_text()
        assert other.get_base64() not in path.read_text()
    finally:
        path.unlink(missing_ok=True)


def test_accept_new_tracks_nonstandard_ports(monkeypatch):
    path = config.DATA / "ci-039-custom-port-known-hosts"
    path.unlink(missing_ok=True)
    monkeypatch.setattr(config, "KNOWN_HOSTS", path)
    monkeypatch.setattr(config, "SSH_HOST_KEY_POLICY", "accept-new")
    key = paramiko.RSAKey.generate(1024)
    client = paramiko.SSHClient()
    try:
        ssh_keys.configured_host_key_policy().missing_host_key(
            client, "[test.internal]:2222", key
        )
        loaded = paramiko.HostKeys()
        loaded.load(str(path))
        assert loaded.lookup("[test.internal]:2222")[key.get_name()] == key
    finally:
        path.unlink(missing_ok=True)


def test_strict_mode_still_rejects_unknown_hosts(monkeypatch):
    monkeypatch.setattr(config, "SSH_HOST_KEY_POLICY", "strict")
    policy = ssh_keys.configured_host_key_policy()
    client = paramiko.SSHClient()
    with pytest.raises(ssh_keys.SSHUnknownHostKey, match="inconnue"):
        policy.missing_host_key(client, "test.internal", paramiko.RSAKey.generate(1024))


def test_ldap_admin_test_avoids_client_side_objectclass_schema_validation(monkeypatch):
    import ldap3

    seen = {"queries": []}

    class FakeServer:
        def __init__(self, uri, **kwargs):
            seen["get_info"] = kwargs.get("get_info")
            assert uri == "ldaps://ldap.internal:636"

    class FakeConnection:
        def __init__(self, server, **kwargs):
            assert kwargs["check_names"] is False
            self.entries = []

        def search(self, base, ldap_filter, scope, **kwargs):
            seen["queries"].append(ldap_filter)
            self.entries = [{"cn": "test-group"}]
            return True

        def unbind(self):
            pass

    monkeypatch.setattr(ldap3, "Server", FakeServer)
    monkeypatch.setattr(ldap3, "Connection", FakeConnection)
    monkeypatch.setattr(ldap_settings, "tls_for", lambda settings: object())
    monkeypatch.setattr(ldap_settings, "check_settings", lambda settings: settings)
    values = {
        "enabled": True, "uri": "ldaps://ldap.internal:636",
        "starttls": False, "bind_dn": "cn=service,dc=internal",
        "bind_password": "unused", "base_dn": "dc=internal",
        "group_base_dn": "ou=Groups,dc=internal", "user_attribute": "uid",
        "skip_cert_verification": False,
    }
    result = ldap_settings.test(values)
    assert result["ok"] is True
    assert seen["get_info"] == ldap3.NONE
    assert any("objectClass=posixGroup" in q for q in seen["queries"])


def test_ui_explains_automatic_first_use():
    js = (Path(__file__).resolve().parents[1] / "static/app.js").read_text()
    assert "accept-new" in js
    assert "Afficher l’empreinte SSH (facultatif)" in js
