"""Public Proxmox CA registry managed from the administrator web interface.

Only a fingerprint (SHA-256 of a normalized PEM bundle) is stored in vault/DC
settings. No user-provided filesystem paths or private keys are accepted.
"""
import re
from pathlib import Path
from cryptography import x509
from . import config, ldap_settings

_ID = re.compile(r"^[a-f0-9]{64}$")


class ProxmoxCAError(ValueError):
    pass


def import_public(pem_text=None, der_base64=None):
    try:
        result = ldap_settings.import_ca(pem_text=pem_text,
                                         der_base64=der_base64,
                                         store="proxmox-cas")
    except ldap_settings.LDAPSettingsError as exc:
        raise ProxmoxCAError(str(exc)) from None
    return {
        "id": Path(result["path"]).stem,
        "certificate_count": result["certificate_count"],
        "ignored_non_ca_count": result["ignored_non_ca_count"],
        "ca_subjects": result["ca_subjects"],
        "fingerprint_sha256": result["fingerprint_sha256"],
    }


def filename(ca_id):
    if not isinstance(ca_id, str) or not _ID.fullmatch(ca_id):
        raise ProxmoxCAError("Identifiant CA Proxmox invalide : importer le certificat depuis l'interface")
    base = config.DATA / "proxmox-cas"
    path = base / (ca_id + ".pem")
    if not path.is_file() or path.is_symlink():
        raise ProxmoxCAError("CA Proxmox absente du stockage : réimporter le certificat")
    return path


def verify_value(ca_id):
    """Return HTTPX's TLS verification value: PEM file or default system CAs.

    An administrator can configure a per-DC CA from the UI. Legacy host-level
    PROXMOX_CA_CERT remains only a fallback for pre-existing deployments.
    """
    if ca_id:
        return str(filename(ca_id))
    return config.PROXMOX_CA_CERT or True


def list_public():
    root = config.DATA / "proxmox-cas"
    if not root.is_dir():
        return []
    rows = []
    for path in sorted(root.glob("*.pem"))[:1000]:
        if not _ID.fullmatch(path.stem) or path.is_symlink():
            continue
        try:
            content = path.read_bytes()
            match = re.search(rb"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----", content)
            if not match:
                continue
            cert = x509.load_pem_x509_certificate(match.group())
            subject = cert.subject.rfc4514_string()
        except (OSError, ValueError):
            continue
        rows.append({"id": path.stem, "subject": subject[:260]})
    return rows
