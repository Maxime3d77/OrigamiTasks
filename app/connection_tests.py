"""Bounded, administrator-approved diagnostics; optional explicit sends/probes.

No credentials, URL query secrets or server response bodies are returned.
"""
import io
import re
import ssl
import time
import threading
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid, format_datetime
from urllib.parse import urlsplit

import ftplib
import httpx

from . import config, connections, integrations, ldap_settings

TIMEOUT = 8
_SEND_LOCK = threading.Lock()
_LAST_MAIL_SEND = {}
_MAIL_SEND_COOLDOWN_SECONDS = 30


class ConnectionTestError(Exception):
    """A safe, human-readable diagnostic (no backend exception string)."""


def ok(kind, message, checks):
    return {"ok": True, "protocol": kind, "message": message, "checks": checks}


def check(name, detail):
    return {"name": name, "status": "success", "detail": detail}


def untested(name, detail):
    return {"name": name, "status": "not_tested", "detail": detail}



def warning(name, detail):
    return {"name": name, "status": "warning", "detail": detail}


def _bool(options, key):
    value = options.get(key, False)
    if type(value) is not bool:
        raise ConnectionTestError("Option de test invalide : "+key)
    return value


def _short(options, key, maximum, *, multiline=False):
    value = options.get(key, "")
    if not isinstance(value, str) or len(value) > maximum:
        raise ConnectionTestError("Champ de test trop long ou invalide : "+key)
    if not multiline and ("\r" in value or "\n" in value):
        raise ConnectionTestError("Retour à la ligne interdit : "+key)
    return value.strip()


def _email(address):
    # Exactly one mailbox: no header injection or bulk recipient lists.
    if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", address or ""):
        raise ConnectionTestError("Adresse e-mail invalide : un seul destinataire est autorisé")
    return address


def _send_mail(values, options, credential_id):
    if not _bool(options, "confirm_send"):
        raise ConnectionTestError("Confirmer explicitement l'envoi de l'e-mail de test")
    recipient = _email(_short(options, "recipient", 254))
    sender = _email(str(values.get("from_address") or
                        (values.get("username") if "@" in str(values.get("username") or "") else "")))
    subject = _short(options, "subject", 120) or "[OrigamiTasks] Vérification de la messagerie"
    body = _short(options, "message", 1500, multiline=True) or (
        "Ceci est un e-mail de test envoyé depuis OrigamiTasks.\n"
        "La présence du message dans la boîte valide sa réception."
    )
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[1])
    msg["X-OrigamiTasks-Test"] = "true"
    msg.set_content(body+"\n\nRéférence de test : "+msg["Message-ID"]+"\n")

    # Serialize send attempts per credential to prevent concurrent bypass.
    with _SEND_LOCK:
        if credential_id is not None:
            last = _LAST_MAIL_SEND.get(credential_id, 0)
            if last and time.monotonic() - last < _MAIL_SEND_COOLDOWN_SECONDS:
                raise ConnectionTestError("Attendre 30 secondes entre deux e-mails de test pour cette connexion")
        session = None
        try:
            session = connections.open_smtp(values, timeout=TIMEOUT)
            refused = session.send_message(msg, from_addr=sender, to_addrs=[recipient])
            if refused:
                raise ConnectionTestError("Le serveur SMTP a refusé le destinataire du test")
            if credential_id is not None:
                _LAST_MAIL_SEND[credential_id] = time.monotonic()
        except ConnectionTestError:
            raise
        except (OSError, TimeoutError, smtplib.SMTPException, ValueError) as exc:
            raise ConnectionTestError(
                "Envoi SMTP échoué ("+type(exc).__name__+") : vérifier l'expéditeur et les règles du relais"
            ) from None
        finally:
            if session is not None:
                try:
                    session.quit()
                except (OSError, smtplib.SMTPException):
                    session.close()
    return str(msg["Message-ID"])


def _port(value, default):
    try:
        n = int(value or default)
        if not (1 <= n <= 65535):
            raise ValueError()
    except (ValueError, TypeError):
        raise ConnectionTestError("Port TCP invalide") from None
    return n


def _url(value, protocols=("https",)):
    """Validate root HTTPS endpoint without credentials, query or fragments."""
    parsed = urlsplit(str(value or ""))
    if (parsed.scheme not in protocols or not parsed.hostname or parsed.username or
        parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ConnectionTestError("URL racine HTTPS valide obligatoire (sans identifiants)")
    try:
        _ = parsed.port
    except ValueError:
        raise ConnectionTestError("Port de l'URL invalide") from None
    return str(value).rstrip("/")


def _allowed_origin(url, allowlist):
    try:
        return integrations.authorized_origin(url, allowlist)
    except integrations.IntegrationError:
        raise ConnectionTestError(
            "Hôte non autorisé pour le test. Vérifier la liste d'hôtes autorisés du connecteur."
        ) from None


def ssh(values):
    """Paramiko connect() authenticates and verifies the pinned known_hosts key."""
    try:
        import paramiko
    except ImportError:
        raise ConnectionTestError("Module SSH Paramiko absent") from None
    host = connections.safe_host(values.get("host"))
    user = str(values.get("username") or "")
    if not user:
        raise ConnectionTestError("Utilisateur SSH non renseigné")
    if not values.get("password") and not values.get("private_key"):
        raise ConnectionTestError("Mot de passe ou clé privée SSH manquant")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if config.KNOWN_HOSTS.is_file():
        client.load_host_keys(str(config.KNOWN_HOSTS))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    params = dict(hostname=host, port=_port(values.get("port"), 22), username=user,
                  timeout=TIMEOUT, auth_timeout=TIMEOUT, banner_timeout=TIMEOUT,
                  look_for_keys=False, allow_agent=False)
    if values.get("private_key"):
        for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                params["pkey"] = cls.from_private_key(
                    io.StringIO(values["private_key"]),
                    password=values.get("passphrase") or None
                )
                break
            except (paramiko.SSHException, ValueError):
                continue
        if "pkey" not in params:
            raise ConnectionTestError("Clé privée SSH invalide ou phrase secrète incorrecte")
    else:
        params["password"] = values["password"]
    try:
        client.connect(**params)
        return ok("ssh", "Connexion SSH et authentification vérifiées ; aucune commande exécutée.",
                  [check("Connectivité TCP", "Connexion établie"),
                   check("Clé de l'hôte", "Empreinte vérifiée dans known_hosts"),
                   check("Authentification", "Compte SSH accepté")])
    except paramiko.BadHostKeyException:
        raise ConnectionTestError("SSH : empreinte d'hôte différente de la clé approuvée") from None
    except paramiko.AuthenticationException:
        raise ConnectionTestError("SSH : authentification refusée par le serveur") from None
    except paramiko.SSHException:
        raise ConnectionTestError(
            "SSH : protocole/clé d'hôte non validé. Vérifier known_hosts et l'algorithme de clé."
        ) from None
    except (OSError, TimeoutError):
        raise ConnectionTestError("SSH : serveur injoignable ou délai dépassé") from None
    finally:
        client.close()


def ftp(values):
    """FTPS explicit: login and TLS control/data negotiation, no FTP file operation."""
    host = connections.safe_host(values.get("host"))
    port = _port(values.get("port"), 21)
    mode = str(values.get("tls_mode") or "explicit")
    if mode not in ("explicit", "none"):
        raise ConnectionTestError("Mode FTP inconnu")
    if mode == "none":
        # Never transmit an FTP password over an unencrypted connection in a test.
        client = ftplib.FTP(timeout=TIMEOUT)
        try:
            client.connect(host, port, timeout=TIMEOUT)
            return ok("ftp", "Serveur FTP joignable ; identifiants non testés sur une liaison en clair.",
                      [check("Connectivité FTP", "Bannière FTP reçue"),
                       untested("Authentification", "Non testée : le serveur ne propose pas TLS")])
        except ftplib.all_errors as exc:
            raise ConnectionTestError("FTP : connexion impossible ("+type(exc).__name__+")") from None
        finally:
            try:
                client.close()
            except Exception:
                pass
    client = ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=TIMEOUT)
    try:
        client.connect(host, port, timeout=TIMEOUT)
        client.login(values.get("username") or "anonymous",
                     values.get("password") or "")
        client.prot_p()
        return ok("ftp", "Connexion FTPS et authentification validées ; aucun transfert effectué.",
                  [check("Connectivité FTP", "Bannière reçue"),
                   check("TLS", "Canal de commande sécurisé et données protégées"),
                   check("Authentification", "Compte accepté")])
    except ftplib.all_errors as exc:
        raise ConnectionTestError("FTPS : échec du transport/TLS ou des identifiants ("+
                                  type(exc).__name__+")") from None
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def ldap(values):
    """Bind with an explicitly configured vault account, then BASE search only."""
    from ldap3 import Server, Connection, Tls, BASE
    uri = str(values.get("uri") or "")
    parsed = urlsplit(uri)
    starttls = bool(values.get("starttls", False))
    if (parsed.scheme not in ("ldap", "ldaps") or not parsed.hostname or
        parsed.username or parsed.password or parsed.query or parsed.fragment or
        parsed.path not in ("", "/") or
        (parsed.scheme == "ldap") != starttls):
        raise ConnectionTestError("LDAP : URI LDAPS ou LDAP+STARTTLS obligatoire")
    username = values.get("username")
    password = values.get("password")
    if not username or not password:
        raise ConnectionTestError("LDAP : DN et mot de passe de liaison obligatoires")
    try:
        ca_cert = ldap_settings.current().get("ca_cert") or None
        server = Server(uri, tls=Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=ca_cert),
                        connect_timeout=TIMEOUT)
        conn = Connection(server, user=username, password=password,
                          receive_timeout=TIMEOUT, auto_bind=not starttls)
        try:
            if starttls:
                conn.open()
                if not conn.start_tls() or not conn.bind():
                    raise ConnectionTestError("LDAP : négociation STARTTLS ou bind refusé")
            base = values.get("base_dn") or ""
            checks = [check("TLS / certificat", "Connexion chiffrée validée"),
                      check("Authentification", "Bind du compte de service accepté")]
            if base:
                if not conn.search(base, "(objectClass=*)", BASE, attributes=["objectClass"],
                                   size_limit=1):
                    raise ConnectionTestError("LDAP : bind accepté mais base DN inaccessible")
                checks.append(check("Base DN", "Recherche en lecture seule validée"))
            return ok("ldap", "Connexion LDAP et compte de service vérifiés ; aucune modification.",
                      checks)
        finally:
            conn.unbind()
    except ConnectionTestError:
        raise
    except Exception as exc:
        raise ConnectionTestError("LDAP : échec TLS/bind/recherche ("+type(exc).__name__+")") from None


def matrix(values):
    """Use the authenticated Matrix whoami endpoint, with allow-list validation."""
    base = _allowed_origin(values.get("homeserver"), config.MATRIX_ALLOWED_HOSTS)
    token = values.get("token")
    if not token:
        raise ConnectionTestError("Matrix : token d'accès manquant")
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False) as client:
            r = client.get(base+"/_matrix/client/v3/account/whoami",
                           headers={"Authorization": "Bearer "+token})
        if r.status_code != 200:
            raise ConnectionTestError("Matrix : identité du token non validée (HTTP "+str(r.status_code)+")")
        data = r.json()
        if not isinstance(data, dict) or not data.get("user_id"):
            raise ConnectionTestError("Matrix : réponse whoami invalide")
        return ok("matrix", "Token Matrix reconnu ; aucun message envoyé.",
                  [check("HTTPS", "Homeserver accessible"),
                   check("Identité", "Endpoint whoami a accepté le token")])
    except ConnectionTestError:
        raise
    except (httpx.HTTPError, ValueError):
        raise ConnectionTestError("Matrix : erreur réseau/TLS ou réponse inattendue") from None


def proxmox(values):
    """Read-only access/permissions route: validates token without VM actions."""
    base = _allowed_origin(values.get("url"), config.PROXMOX_ALLOWED_HOSTS)
    try:
        data = integrations.proxmox_request(base, values, "/api2/json/access/permissions",
                                            timeout=TIMEOUT)
        if not isinstance(data, dict):
            raise ConnectionTestError("Proxmox : réponse API inattendue")
        return ok("proxmox", "Token Proxmox accepté ; aucune action sur les VM.",
                  [check("HTTPS", "API accessible avec certificat vérifié"),
                   check("Token API", "Requête de lecture autorisée")])
    except ConnectionTestError:
        raise
    except (httpx.HTTPError, integrations.IntegrationError, ValueError, KeyError, TypeError) as exc:
        raise ConnectionTestError("Proxmox : refus API/TLS ou token invalide ("+
                                  type(exc).__name__+")") from None


def api(values):
    """GET only to allow-listed HTTPS API. A 2xx response does not prove identity."""
    if not config.CONNECTION_TEST_ALLOWED_HOSTS.strip():
        raise ConnectionTestError(
            "API : renseigner CONNECTION_TEST_ALLOWED_HOSTS dans .env avant le test"
        )
    base = _allowed_origin(values.get("base_url"), config.CONNECTION_TEST_ALLOWED_HOSTS)
    path = str(values.get("test_path") or "/")
    if (not path.startswith("/") or path.startswith("//") or
        "?" in path or "#" in path or "\\" in path or
        ".." in path.split("/") or len(path) > 180):
        raise ConnectionTestError("API : chemin de test invalide")
    headers = {}
    if values.get("token"):
        headers["Authorization"] = "Bearer "+values["token"]
    elif values.get("username") and values.get("password"):
        # Basic authentication only over verified HTTPS.
        import base64
        raw = (values["username"]+":"+values["password"]).encode()
        headers["Authorization"] = "Basic "+base64.b64encode(raw).decode()
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False) as client:
            response = client.get(base+path, headers=headers)
        if response.status_code not in range(200, 300):
            raise ConnectionTestError("API : réponse HTTP "+str(response.status_code)+
                                      " ; accès non confirmé")
        return ok("api", "GET HTTPS autorisé ; la validité métier du token dépend de l'endpoint choisi.",
                  [check("HTTPS", "Certificat TLS vérifié"),
                   check("GET de diagnostic", "HTTP "+str(response.status_code)),
                   untested("Identité", "Une réponse 2xx ne garantit pas que le serveur a contrôlé le token")])
    except ConnectionTestError:
        raise
    except httpx.HTTPError:
        raise ConnectionTestError("API : erreur réseau ou certificat TLS") from None


def run(kind, values):
    if kind == "smtp":
        try:
            result = connections.test_smtp(values)
        except connections.ConnectionError as exc:
            raise ConnectionTestError(str(exc)) from None
        mode = values.get("tls_mode") or "starttls"
        auth = bool(result.get("authenticated"))
        checks = [check("Connectivité SMTP", "Serveur accessible")]
        checks.append(check("Sécurité", {"ssl":"TLS direct vérifié",
                                         "starttls":"STARTTLS validé",
                                         "none":"Connexion sans TLS (relais interne)"}[mode]))
        checks.append(check("Authentification", "Identifiants acceptés") if auth
                      else untested("Authentification", "Non requise pour ce relais"))
        return ok("smtp", result["message"], checks)
    registry = {"ssh": ssh, "ftp": ftp, "ldap": ldap, "matrix": matrix,
                "proxmox": proxmox, "api": api}
    if kind == "generic":
        raise ConnectionTestError(
            "Identifiant générique : aucun protocole ou serveur défini. "
            "Choisir un profil SSH, SMTP, LDAP, API… pour tester une connexion."
        )
    if kind not in registry:
        raise ConnectionTestError("Type de connexion non pris en charge")
    return registry[kind](values)
