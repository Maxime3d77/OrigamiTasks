"""Load *user-authentication* SSH private keys with consistent, secret-free errors.

The server's /etc/ssh/ssh_host_*_key identifies the server and should never be
entered in the account vault. Public keys belong in remote authorized_keys.
"""
import io
import re
import hashlib
import base64


class SSHKeyError(ValueError):
    pass


def load_private_key(private_key, passphrase=None):
    """Return a Paramiko PKey from an OpenSSH/PEM private-key string."""
    import paramiko
    if not isinstance(private_key, str) or not 40 <= len(private_key) <= 24576:
        raise SSHKeyError("Clé SSH absente ou taille invalide (24 Kio maximum)")
    private_key=private_key.strip()
    if (private_key.startswith(("ssh-ed25519 ","ssh-rsa ","ecdsa-sha2-","sk-ssh-")) or
        private_key.startswith("---- BEGIN SSH2 PUBLIC KEY") or
        "BEGIN PUBLIC KEY" in private_key):
        raise SSHKeyError(
            "Clé publique détectée : coller la clé PRIVÉE du compte utilisateur (id_ed25519/id_rsa), "
            "pas la ligne de authorized_keys"
        )
    header=re.search(r"\A-----BEGIN ([A-Z0-9 ]+)-----",private_key)
    if not header or header.group(1) not in (
        "OPENSSH PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY",
        "DSA PRIVATE KEY", "PRIVATE KEY", "ENCRYPTED PRIVATE KEY",
    ):
        raise SSHKeyError(
            "Format non reconnu : clé privée OpenSSH ou PEM attendue, "
            "avec les lignes BEGIN/END complètes"
        )
    if not isinstance(passphrase,(str,type(None))):
        raise SSHKeyError("Phrase secrète invalide")
    if passphrase is not None and len(passphrase)>1024:
        raise SSHKeyError("Phrase secrète trop longue")
    if "-----END "+header.group(1)+"-----" not in private_key:
        raise SSHKeyError("Clé privée incomplète : ligne END manquante")
    needs_passphrase=False
    key_classes=(paramiko.Ed25519Key,paramiko.RSAKey,paramiko.ECDSAKey)
    for cls in key_classes:
        try:
            return cls.from_private_key(io.StringIO(private_key), password=passphrase or None)
        except paramiko.PasswordRequiredException:
            needs_passphrase=True
        except (paramiko.SSHException,ValueError,TypeError):
            continue
    if needs_passphrase:
        raise SSHKeyError("Cette clé SSH est chiffrée : renseigner sa phrase secrète dans le coffre")
    if header.group(1) in ("ENCRYPTED PRIVATE KEY","DSA PRIVATE KEY"):
        raise SSHKeyError("Format de clé SSH non pris en charge ; convertir en OpenSSH Ed25519 ou RSA")
    raise SSHKeyError(
        "Clé privée illisible : vérifier la copie complète, l'algorithme et la phrase secrète. "
        "La clé du compte ne doit pas être une clé d'hôte ssh_host_*"
    )


class SSHUnknownHostKey(Exception):
    """The remote host key was presented but has no trusted known_hosts entry."""
    pass


def strict_host_key_policy():
    """Reject first use and report the offered public host fingerprint (not secret)."""
    import paramiko
    class RejectWithFingerprint(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            fp="SHA256:"+base64.b64encode(
                hashlib.sha256(key.asbytes()).digest()
            ).decode("ascii").rstrip("=")
            raise SSHUnknownHostKey(
                "Clé d'hôte SSH inconnue dans known_hosts. "
                "Empreinte présentée par le serveur ("+key.get_name()+") : "+fp+
                ". Comparer cette empreinte à celle obtenue directement sur le serveur "
                "avant de l'autoriser. Aucune clé n'a été acceptée automatiquement."
            )
    return RejectWithFingerprint()

def configured_host_key_policy():
    """Use strict checking or persistent automatic trust-on-first-use."""
    import paramiko
    from . import config, host_keys
    if config.SSH_HOST_KEY_POLICY == "strict":
        return strict_host_key_policy()
    if config.SSH_HOST_KEY_POLICY != "accept-new":
        raise SSHKeyError("SSH_HOST_KEY_POLICY doit être 'accept-new' ou 'strict'")

    class PersistentAcceptNewPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            try:
                created = host_keys.accept_new(hostname, key)
            except host_keys.HostKeyError as exc:
                raise SSHUnknownHostKey(str(exc)) from None
            client._origami_added_host_key = bool(created)
            client.get_host_keys().add(hostname, key.get_name(), key)

    return PersistentAcceptNewPolicy()
