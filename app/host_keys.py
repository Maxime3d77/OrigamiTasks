"""Admin-only SSH host-key inspection and explicitly verified trust-on-first-use.

Never auto-approve: require an exact SHA256 fingerprint verified out of band.
Use existing pinned known_hosts entries and reject changed host keys.
No SSH passwords or user private keys are sent during inspection.
"""
import base64
import fcntl
import hashlib
import os
import socket
from pathlib import Path

from . import config, ssh_inventory


class HostKeyError(ValueError):
    pass


def target(host, ssh_port=22):
    try:
        return ssh_inventory.fqdn(host), ssh_inventory.port(ssh_port)
    except ssh_inventory.InventoryError as exc:
        raise HostKeyError(str(exc)) from None


def _hostname(host, ssh_port):
    return host if ssh_port == 22 else "["+host+"]:"+str(ssh_port)


def _fingerprint(key):
    return "SHA256:"+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")


def _trusted_path():
    path=config.KNOWN_HOSTS.resolve()
    # Only the persistent application data volume may be mutated via the web.
    if not path.is_relative_to(config.DATA.resolve()):
        raise HostKeyError(
            "Le fichier SSH_KNOWN_HOSTS n'est pas sous /app/data ; gérer cette source de confiance sur l'hôte."
        )
    if path.exists() and not path.is_file():
        raise HostKeyError("Fichier known_hosts invalide")
    return path


def _compare_known(host, ssh_port, offered):
    import paramiko
    path=_trusted_path()
    if not path.exists():
        return "unknown"
    try:
        known=paramiko.HostKeys()
        known.load(str(path))
    except (OSError, paramiko.SSHException):
        raise HostKeyError("Fichier known_hosts illisible : corriger son format avant d'ajouter une clé") from None
    record=known.lookup(_hostname(host,ssh_port))
    if not record:
        return "unknown"
    old=record.get(offered.get_name())
    if old is None:
        return "unknown"
    return "trusted" if old==offered else "changed"



def accept_new(hostname, key):
    """Persist an SSH host key on first use (TOFU), never replace a known host.

    Paramiko passes host for port 22 and [host]:port for custom ports.
    No SSH user credential is involved in this operation.
    """
    import paramiko
    if (not isinstance(hostname, str) or not hostname or len(hostname) > 260 or
            any(c in hostname for c in ('\n', '\r', '\t', ' ', '\x00'))):
        raise HostKeyError("Nom d'hôte SSH invalide pour known_hosts")
    path = _trusted_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                # Validate while locked, also across worker processes.
                stream.seek(0)
                content = stream.read()
                known = paramiko.HostKeys()
                if content.strip():
                    try:
                        known.load(str(path))
                    except (OSError, paramiko.SSHException, ValueError):
                        raise HostKeyError("known_hosts invalide : aucune clé ajoutée") from None
                existing = known.lookup(hostname)
                if existing:
                    if existing.get(key.get_name()) == key:
                        return False
                    raise HostKeyError(
                        "Clé d'hôte SSH différente pour un serveur déjà connu : "
                        "connexion bloquée, vérifier la rotation de clé"
                    )
                if content and not content.endswith("\n"):
                    stream.write("\n")
                stream.write(hostname + " " + key.get_name() + " " +
                             key.get_base64() + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                return True
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except OSError:
        raise HostKeyError(
            "Impossible d'enregistrer la clé publique SSH dans le volume persistent known_hosts"
        ) from None


def _fetch(host, ssh_port):
    """Read the offered public host key (no user authentication attempt)."""
    import paramiko
    sock=None
    transport=None
    try:
        sock=socket.create_connection((host,ssh_port),timeout=6)
        sock.settimeout(6)
        transport=paramiko.Transport(sock)
        transport.banner_timeout=6
        transport.auth_timeout=6
        transport.start_client(timeout=6)
        key=transport.get_remote_server_key()
        if key is None:
            raise HostKeyError("Le serveur SSH n'a présenté aucune clé d'hôte")
        return key
    except (OSError, TimeoutError, EOFError, paramiko.SSHException):
        raise HostKeyError("Impossible de récupérer la clé publique de ce serveur SSH") from None
    finally:
        if transport:
            transport.close()
        elif sock:
            sock.close()


def inspect(host, ssh_port=22):
    host,ssh_port=target(host,ssh_port)
    key=_fetch(host,ssh_port)
    status=_compare_known(host,ssh_port,key)
    return {
        "host":host,"port":ssh_port,
        "algorithm":key.get_name(),
        "fingerprint":_fingerprint(key),
        "status":status,
        "trusted":status=="trusted",
    }


def approve(host, ssh_port, fingerprint, verified_out_of_band=False):
    """Write only a public host key, only on an exact independently confirmed fingerprint."""
    import paramiko
    host,ssh_port=target(host,ssh_port)
    if verified_out_of_band is not True:
        raise HostKeyError(
            "Confirmer la comparaison avec l'empreinte obtenue directement sur la console du serveur"
        )
    if not isinstance(fingerprint,str) or len(fingerprint)>110 or not fingerprint.startswith("SHA256:"):
        raise HostKeyError("Empreinte SHA256 de vérification invalide")
    key=_fetch(host,ssh_port)
    if _fingerprint(key)!=fingerprint:
        raise HostKeyError("Empreinte différente de celle approuvée : aucune clé enregistrée")
    existing=_compare_known(host,ssh_port,key)
    if existing=="changed":
        raise HostKeyError("Empreinte d'hôte déjà approuvée différente : opération bloquée")
    if existing=="trusted":
        return {"ok":True,"already_trusted":True,"host":host,"port":ssh_port,
                "fingerprint":fingerprint}
    path=_trusted_path()
    if not path.parent.is_dir():
        raise HostKeyError("Répertoire de confiance SSH absent dans les données persistantes")
    line=_hostname(host,ssh_port)+" "+key.get_name()+" "+key.get_base64()+"\n"
    try:
        flags=os.O_RDWR|os.O_CREAT|getattr(os,"O_NOFOLLOW",0)
        fd=os.open(path,flags,0o600)
        with os.fdopen(fd,"r+",encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
            try:
                # Re-validate while holding the lock to avoid overwriting
                # an entry added by a parallel authorized operation.
                current=paramiko.HostKeys()
                stream.seek(0)
                text=stream.read()
                if text.strip():
                    try:
                        current.load(str(path))
                    except (OSError,paramiko.SSHException):
                        raise HostKeyError("known_hosts illisible ; opération abandonnée") from None
                old=current.lookup(_hostname(host,ssh_port))
                if old and old.get(key.get_name()) is not None:
                    if old[key.get_name()]!=key:
                        raise HostKeyError("Empreinte enregistrée modifiée ; aucune écriture")
                    return {"ok":True,"already_trusted":True,"host":host,
                            "port":ssh_port,"fingerprint":fingerprint}
                stream.seek(0,os.SEEK_END)
                if stream.tell() and not text.endswith("\n"):
                    stream.write("\n")
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    except (OSError,PermissionError):
        raise HostKeyError(
            "Impossible d'écrire known_hosts. Vérifier les droits du volume data du conteneur."
        ) from None
    return {"ok":True,"already_trusted":False,"host":host,
            "port":ssh_port,"fingerprint":fingerprint}
