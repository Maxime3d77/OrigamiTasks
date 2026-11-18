"""Bounded, administrator-approved diagnostics; optional explicit sends/probes.

No credentials, URL query secrets or server response bodies are returned.
"""
import io
import re
import ssl
import time
import threading
import secrets
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid, format_datetime
from urllib.parse import urlsplit, quote

import ftplib
import httpx

from . import config, connections, integrations, ldap_settings

TIMEOUT = 8
_SEND_LOCK = threading.Lock()
_LAST_MAIL_SEND = {}
_LAST_MATRIX_SEND = {}
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
            _LAST_MAIL_SEND[credential_id] = time.monotonic()
        session = None
        try:
            session = connections.open_smtp(values, timeout=TIMEOUT)
            refused = session.send_message(msg, from_addr=sender, to_addrs=[recipient])
            if refused:
                raise ConnectionTestError("Le serveur SMTP a refusé le destinataire du test")
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


def ssh(values, options=None):
    """Validate host key and login, plus explicitly selected read-only probes."""
    options = options or {}
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
    from .ssh_keys import configured_host_key_policy, SSHUnknownHostKey
    client.set_missing_host_key_policy(configured_host_key_policy())
    params = dict(hostname=host, port=_port(values.get("port"), 22), username=user,
                  timeout=TIMEOUT, auth_timeout=TIMEOUT, banner_timeout=TIMEOUT,
                  look_for_keys=False, allow_agent=False)
    if values.get("private_key"):
        from .ssh_keys import load_private_key, SSHKeyError
        try:
            params["pkey"]=load_private_key(
                values["private_key"],values.get("passphrase")
            )
        except SSHKeyError as exc:
            raise ConnectionTestError(str(exc)) from None
    else:
        params["password"] = values["password"]
    try:
        client.connect(**params)
        checks = [check("Connectivité TCP", "Connexion établie"),
                  check("Clé de l'hôte", "Nouvelle clé automatiquement enregistrée dans known_hosts" if getattr(client, "_origami_added_host_key", False) else "Clé reconnue dans known_hosts"),
                  check("Authentification", "Compte SSH accepté")]
        if _bool(options, "check_sftp"):
            sftp = client.open_sftp()
            try:
                sftp.stat(".")  # read metadata, never list/upload/download/delete
                checks.append(check("Sous-système SFTP", "Accès au répertoire courant validé"))
            finally:
                sftp.close()
        if _bool(options, "check_whoami"):
            # Only this fixed read-only command (Windows + Linux), never arbitrary text.
            _, stdout, _ = client.exec_command("whoami", timeout=TIMEOUT)
            channel = stdout.channel
            end = time.monotonic()+TIMEOUT
            while not channel.exit_status_ready() and time.monotonic()<end:
                time.sleep(0.1)
            if not channel.exit_status_ready() or channel.recv_exit_status()!=0:
                raise ConnectionTestError("SSH : whoami a échoué ou dépassé le délai")
            checks.append(check("Commande distante", "whoami terminé sans modification"))
        return ok("ssh", "Clé d’hôte et authentification SSH vérifiées.", checks)
    except SSHUnknownHostKey as exc:
        raise ConnectionTestError(str(exc)) from None
    except paramiko.BadHostKeyException:
        raise ConnectionTestError("SSH : empreinte d'hôte différente de la clé approuvée") from None
    except paramiko.AuthenticationException:
        raise ConnectionTestError("SSH : authentification refusée par le serveur") from None
    except paramiko.SSHException:
        raise ConnectionTestError(
            "SSH : échec de négociation du protocole SSH ou algorithmes incompatibles. Clé d'hôte inconnue et empreinte différente sont signalées séparément."
        ) from None
    except (OSError, TimeoutError, EOFError):
        raise ConnectionTestError("SSH : hôte injoignable, sous-système indisponible ou délai dépassé") from None
    finally:
        client.close()


def ftp(values, options=None):
    """FTPS login plus optional read-only current-directory check."""
    options = options or {}
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
        auth_label = ("Compte accepté" if values.get("username")
                      else "Connexion anonyme acceptée ; aucun compte utilisateur spécifique validé")
        checks = [check("Connectivité FTP", "Bannière reçue"),
                  check("TLS", "Canal de commande sécurisé et données protégées"),
                  check("Authentification", auth_label)]
        if _bool(options, "check_directory"):
            client.pwd()
            checks.append(check("Accès répertoire", "Commande PWD autorisée, aucun fichier transféré"))
        return ok("ftp", "Connexion FTPS et authentification validées ; aucun transfert effectué.", checks)
    except ftplib.all_errors as exc:
        raise ConnectionTestError("FTPS : échec du transport/TLS ou des identifiants ("+
                                  type(exc).__name__+")") from None
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def ldap(values, options=None):
    """Bind and optional scoped user/group lookups; never write LDAP objects."""
    options = options or {}
    from ldap3 import Server, Connection, Tls, BASE, SUBTREE, NONE
    from ldap3.utils.conv import escape_filter_chars
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
        settings=ldap_settings.current()
        server = Server(uri, tls=ldap_settings.tls_for(settings,uri=uri),
                        connect_timeout=TIMEOUT, get_info=NONE)
        conn = Connection(server, user=username, password=password,
                          receive_timeout=TIMEOUT, auto_bind=not starttls,
                          check_names=False)
        try:
            if starttls:
                conn.open()
                if not conn.start_tls() or not conn.bind():
                    raise ConnectionTestError("LDAP : négociation STARTTLS ou bind refusé")
            base = values.get("base_dn") or ""
            checks = [warning("TLS / certificat", "Transport chiffré, mais certificat NON vérifié (exception dangereuse)")
                      if settings.get('skip_cert_verification')
                      else check("TLS / certificat", "Certificat et transport TLS validés"),
                      check("Authentification", "Bind du compte de service accepté")]
            if base:
                if not conn.search(base, "(objectClass=*)", BASE, attributes=["objectClass"],
                                   size_limit=1):
                    raise ConnectionTestError("LDAP : bind accepté mais base DN inaccessible")
                checks.append(check("Base DN", "Recherche en lecture seule validée"))
            test_user = _short(options, "test_user", 100)
            if test_user:
                if not base:
                    raise ConnectionTestError("LDAP : base DN requise pour chercher un utilisateur")
                attr = values.get("user_attribute") or ldap_settings.current().get("user_attribute") or "uid"
                if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]*", attr):
                    raise ConnectionTestError("LDAP : attribut de recherche invalide")
                conn.search(base, "("+attr+"="+escape_filter_chars(test_user)+")",
                            SUBTREE, attributes=["cn"], size_limit=2)
                found = bool(getattr(conn, "entries", []))
                checks.append(check("Recherche utilisateur", "Compte trouvé")
                              if found else warning("Recherche utilisateur", "Utilisateur introuvable"))
            test_group = _short(options, "test_group", 100)
            if test_group:
                group_base = values.get("group_base_dn") or base
                if not group_base:
                    raise ConnectionTestError("LDAP : base DN de groupes manquante")
                conn.search(group_base,
                            "(cn="+escape_filter_chars(test_group)+")",
                            SUBTREE, attributes=["cn"], size_limit=2)
                found = bool(getattr(conn, "entries", []))
                checks.append(check("Recherche groupe", "Groupe trouvé")
                              if found else warning("Recherche groupe", "Groupe introuvable"))
            return ok("ldap", "Connexion et bind LDAP vérifiés ; recherches facultatives en lecture seule.",
                      checks)
        finally:
            conn.unbind()
    except ConnectionTestError:
        raise
    except Exception as exc:
        raise ConnectionTestError("LDAP : échec TLS/bind/recherche ("+type(exc).__name__+")") from None


def matrix(values, options=None, credential_id=None):
    """Authenticated whoami; optional room membership check and explicit send."""
    options = options or {}
    base = _allowed_origin(values.get("homeserver"), config.MATRIX_ALLOWED_HOSTS)
    token = values.get("token")
    if not token:
        raise ConnectionTestError("Matrix : token d'accès manquant")
    room = _short(options, "room_id", 255)
    check_room = _bool(options, "check_room")
    send = _bool(options, "send_message")
    if (check_room or send) and (not room.startswith("!") or ":" not in room):
        raise ConnectionTestError("Matrix : indiquer l'identifiant complet de la room (!id:serveur)")
    headers = {"Authorization": "Bearer "+token}
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False) as client:
            response = client.get(base+"/_matrix/client/v3/account/whoami", headers=headers)
            if response.status_code != 200:
                raise ConnectionTestError("Matrix : identité non validée (HTTP "+str(response.status_code)+")")
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("user_id"), str):
                raise ConnectionTestError("Matrix : réponse whoami invalide")
            checks = [check("HTTPS", "Homeserver accessible"),
                      check("Identité", "Le jeton est reconnu par whoami")]
            if check_room:
                url = (base+"/_matrix/client/v3/rooms/"+quote(room, safe="")+
                       "/state/m.room.member/"+quote(data["user_id"], safe=""))
                membership = client.get(url, headers=headers)
                if membership.status_code != 200:
                    raise ConnectionTestError(
                        "Matrix : appartenance à la room non confirmée (HTTP "+
                        str(membership.status_code)+")"
                    )
                info = membership.json()
                if not isinstance(info, dict) or info.get("membership") != "join":
                    raise ConnectionTestError("Matrix : le bot n'est pas membre de cette room")
                checks.append(check("Appartenance room", "Le compte est membre du salon"))
    except ConnectionTestError:
        raise
    except (httpx.HTTPError, ValueError):
        raise ConnectionTestError("Matrix : erreur réseau/TLS ou réponse inattendue") from None

    if send:
        if not _bool(options, "confirm_send"):
            raise ConnectionTestError("Confirmer explicitement l'envoi du message de test")
        message = _short(options, "message", 1000, multiline=True) or (
            "[OrigamiTasks] Message de test volontaire. Vérifier sa présence dans le salon."
        )
        with _SEND_LOCK:
            if credential_id is not None:
                last = _LAST_MATRIX_SEND.get(credential_id, 0)
                if last and time.monotonic()-last < _MAIL_SEND_COOLDOWN_SECONDS:
                    raise ConnectionTestError("Attendre 30 secondes entre deux messages Matrix de test")
                _LAST_MATRIX_SEND[credential_id] = time.monotonic()
            try:
                integrations.matrix_message(
                    {"homeserver":base, "room_id":room, "message":message}, values,
                    "test-"+secrets.token_hex(8), "matrix", TIMEOUT
                )
            except Exception as exc:
                raise ConnectionTestError(
                    "Matrix : envoi refusé ou impossible ("+type(exc).__name__+")"
                ) from None
        checks.append(check("Envoi message", "API Matrix a accepté le message ; vérifier son affichage dans la room"))
    return ok("matrix", "Jeton Matrix validé ; tests demandés effectués.", checks)


def proxmox(values, options=None):
    """Read-only token, node inventory and optional named-node status."""
    options = options or {}
    base = integrations.authorized_origin(values.get("url"), config.PROXMOX_ALLOWED_HOSTS, admin_proxmox=True)
    try:
        data = integrations.proxmox_request(
            base, values, "/api2/json/access/permissions", timeout=TIMEOUT
        )
        if not isinstance(data, dict):
            raise ConnectionTestError("Proxmox : réponse de permissions inattendue")
        checks = [check("HTTPS", "Certificat API vérifié"),
                  check("Token API", "Requête authentifiée de permissions acceptée")]
        if _bool(options, "check_nodes"):
            nodes = integrations.proxmox_request(base, values, "/api2/json/nodes",
                                                 timeout=TIMEOUT)
            if not isinstance(nodes, list):
                raise ConnectionTestError("Proxmox : inventaire de nœuds inattendu")
            checks.append(check("Lecture nœuds", str(len(nodes))+" nœud(s) accessibles"))
        name = _short(options, "node", 90)
        if name:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,89}", name):
                raise ConnectionTestError("Proxmox : nom de nœud invalide")
            status = integrations.proxmox_request(
                base, values, "/api2/json/nodes/"+quote(name, safe="")+"/status",
                timeout=TIMEOUT
            )
            if not isinstance(status, dict):
                raise ConnectionTestError("Proxmox : statut de nœud inattendu")
            checks.append(check("Statut nœud", "Lecture du nœud autorisée (aucune action VM)"))
        return ok("proxmox", "Token Proxmox validé ; lectures demandées effectuées sans modification.",
                  checks)
    except ConnectionTestError:
        raise
    except integrations.IntegrationError as exc:
        raise ConnectionTestError("Proxmox : "+str(exc)) from None
    except httpx.HTTPStatusError as exc:
        status=exc.response.status_code
        if status==401:
            detail="HTTP 401 — token API ou secret refusé"
        elif status==403:
            detail="HTTP 403 — token accepté mais permissions insuffisantes pour cet appel"
        else:
            detail="HTTP "+str(status)+" — erreur API Proxmox"
        raise ConnectionTestError("Proxmox : "+detail) from None
    except httpx.ConnectError:
        raise ConnectionTestError(
            "Proxmox : connexion impossible (DNS/TCP ou certificat TLS non vérifiable). "
            "Contrôler le FQDN et la CA Proxmox."
        ) from None
    except httpx.TimeoutException:
        raise ConnectionTestError("Proxmox : délai dépassé lors de la connexion à l'API") from None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise ConnectionTestError(
            "Proxmox : réponse API inattendue ("+type(exc).__name__+")"
        ) from None


def api(values, options=None):
    """GET/HEAD only to allow-listed HTTPS API with configurable expected status."""
    options = options or {}
    if not config.CONNECTION_TEST_ALLOWED_HOSTS.strip():
        raise ConnectionTestError("API : renseigner CONNECTION_TEST_ALLOWED_HOSTS dans .env")
    base = _allowed_origin(values.get("base_url"), config.CONNECTION_TEST_ALLOWED_HOSTS)
    path = _short(options, "path", 180) or str(values.get("test_path") or "/")
    if (not path.startswith("/") or path.startswith("//") or
        "?" in path or "#" in path or "\\" in path or
        ".." in path.split("/") or len(path) > 180):
        raise ConnectionTestError("API : chemin de test invalide")
    method = _short(options, "method", 6).upper() or "GET"
    if method not in ("GET", "HEAD"):
        raise ConnectionTestError("API : seules les méthodes GET et HEAD sont permises")
    expected = _short(options, "expected_status", 3) or "2xx"
    if expected != "2xx" and (not expected.isdigit() or
                              int(expected)<200 or int(expected)>599):
        raise ConnectionTestError("API : code HTTP attendu invalide")
    headers = {}
    if values.get("token"):
        headers["Authorization"] = "Bearer "+values["token"]
    elif values.get("username") and values.get("password"):
        import base64
        raw = (values["username"]+":"+values["password"]).encode()
        headers["Authorization"] = "Basic "+base64.b64encode(raw).decode()
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False) as client:
            if method=="HEAD":
                response = client.head(base+path, headers=headers)
            else:
                response = client.get(base+path, headers=headers)
        status = response.status_code
        if status in (401, 403):
            raise ConnectionTestError(
                "API : identifiants refusés ou permissions insuffisantes (HTTP "+str(status)+")"
            )
        accepted = (200 <= status < 300) if expected=="2xx" else status==int(expected)
        if not accepted:
            raise ConnectionTestError(
                "API : HTTP "+str(status)+" ; attendu "+expected+" (accès non confirmé)"
            )
        return ok("api", "Requête "+method+" HTTPS terminée, statut attendu obtenu.",
                  [check("HTTPS", "Certificat TLS vérifié"),
                   check("Requête de diagnostic", method+" : HTTP "+str(status)),
                   untested("Identité", "Le code HTTP ne garantit pas que le jeton a été validé")])
    except ConnectionTestError:
        raise
    except httpx.HTTPError:
        raise ConnectionTestError("API : erreur réseau ou certificat TLS") from None


OPTION_KEYS = {
    "smtp": {"send_email", "confirm_send", "recipient", "subject", "message"},
    "ssh": {"check_sftp", "check_whoami"},
    "ftp": {"check_directory"},
    "ldap": {"test_user", "test_group"},
    "matrix": {"check_room", "room_id", "send_message", "confirm_send", "message"},
    "proxmox": {"check_nodes", "node"},
    "api": {"path", "method", "expected_status"},
    "generic": set(),
}


def run(kind, values, options=None, credential_id=None):
    options = options or {}
    if not isinstance(options, dict):
        raise ConnectionTestError("Options de test invalides")
    if kind not in OPTION_KEYS:
        raise ConnectionTestError("Type de connexion non pris en charge")
    if set(options) - OPTION_KEYS[kind]:
        raise ConnectionTestError("Paramètre non autorisé pour ce protocole")
    if kind == "generic":
        raise ConnectionTestError(
            "Identifiant générique : aucun protocole ou serveur défini. "
            "Choisir un profil SSH, SMTP, LDAP ou API pour le tester."
        )
    if kind == "smtp":
        sending = _bool(options, "send_email")
        mode = values.get("tls_mode") or "starttls"
        if mode not in ("ssl", "starttls", "none"):
            raise ConnectionTestError("Mode SMTP invalide")
        authenticated = bool(values.get("auth", bool(values.get("username"))))
        if sending:
            reference = _send_mail(values, options, credential_id)
            message = "Message accepté par le serveur SMTP : vérifier sa présence dans la boîte destinataire."
        else:
            try:
                result = connections.test_smtp(values)
            except connections.ConnectionError as exc:
                raise ConnectionTestError(str(exc)) from None
            authenticated = bool(result.get("authenticated"))
            message = result["message"]
            reference = None
        checks = [check("Connectivité SMTP", "Serveur accessible"),
                  check("Sécurité", {
                      "ssl": "SSL/TLS direct vérifié",
                      "starttls": "STARTTLS négocié",
                      "none": "Relais interne sans TLS"
                  }[mode]),
                  check("Authentification", "Identifiants SMTP acceptés")
                  if authenticated else untested("Authentification", "Non requise")]
        if sending:
            checks.append(check("Remise au serveur SMTP", "Commande DATA acceptée ; référence "+reference))
            checks.append(untested("Réception en boîte", "À vérifier dans la boîte de réception et les spams"))
        else:
            checks.append(untested("Envoi / réception", "Aucun message envoyé ; cocher l'envoi réel pour tester"))
        result = ok("smtp", message, checks)
        if reference:
            result["reference"] = reference
        return result
    if kind == "matrix":
        return matrix(values, options, credential_id)
    registry = {"ssh": ssh, "ftp": ftp, "ldap": ldap,
                "proxmox": proxmox, "api": api}
    return registry[kind](values, options)
