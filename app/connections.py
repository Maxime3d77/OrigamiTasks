"""Typed encrypted vault connections and safe read-only connectivity diagnostics."""
import re
import ssl
import smtplib

KINDS = ('smtp', 'ssh', 'ftp', 'api', 'ldap', 'matrix', 'proxmox', 'generic')
SECRET_KEYS = {'password', 'token', 'private_key', 'passphrase'}
COMMON = {'username', 'password', 'token', 'private_key', 'passphrase'}
KIND_FIELDS = {
    'smtp': {'host', 'port', 'tls_mode', 'auth', 'from_address', 'username', 'password'},
    'ssh': {'host', 'port', 'username', 'password', 'private_key', 'passphrase'},
    'ftp': {'host', 'port', 'tls_mode', 'username', 'password'},
    'api': {'base_url', 'test_path', 'username', 'password', 'token'},
    'ldap': {'uri', 'starttls', 'base_dn', 'group_base_dn', 'username', 'password'},
    'matrix': {'homeserver', 'username', 'token'},
    'proxmox': {'url', 'username', 'token', 'ca_cert'},
    'generic': COMMON,
}
PUBLIC_KEYS = {'host', 'port', 'tls_mode', 'auth', 'from_address', 'username',
               'base_url', 'uri', 'starttls', 'base_dn', 'group_base_dn',
               'homeserver', 'url', 'test_path', 'ca_cert'}


class ConnectionError(Exception):
    pass


def safe_host(value):
    host = str(value or '').strip()
    if not host or len(host) > 253 or not re.fullmatch(r'[a-zA-Z0-9.\-:\[\]]+', host):
        raise ConnectionError('Nom DNS ou IP du serveur invalide')
    return host


def normalize(kind, updates, previous=None, *, is_new=False):
    if kind not in KINDS:
        raise ConnectionError('Type de connexion inconnu')
    if not isinstance(updates, dict):
        raise ConnectionError('Paramètres de connexion invalides')
    if set(updates) - KIND_FIELDS[kind]:
        raise ConnectionError('Champ non autorisé pour cette connexion')
    values = dict(previous or {})
    for key, value in updates.items():
        if key in ('auth', 'starttls'):
            values[key] = value is True
        elif key == 'port':
            try:
                port = int(value)
            except (ValueError, TypeError):
                raise ConnectionError('Port invalide')
            if not 1 <= port <= 65535:
                raise ConnectionError('Port hors plage 1-65535')
            values[key] = port
        elif value is not None:
            if not isinstance(value, str):
                raise ConnectionError('Valeur de connexion invalide')
            # Empty secret edits must not erase an existing encrypted value.
            if key in SECRET_KEYS and not value:
                continue
            values[key] = value.strip() if key not in SECRET_KEYS else value
    if kind in ('smtp', 'ftp', 'ssh'):
        if values.get('host'):
            values['host'] = safe_host(values.get('host'))
        defaults = {'smtp': 587, 'ftp': 21, 'ssh': 22}
        if kind != 'ssh' or values.get('host'):
            values.setdefault('port', defaults[kind])
    if kind == 'ssh' and is_new:
        if not values.get('username') or not (values.get('password') or values.get('private_key')):
            raise ConnectionError('Identité SSH : login et mot de passe ou clé privée obligatoires')
    if kind == 'smtp':
        values.setdefault('tls_mode', 'starttls')
        if values['tls_mode'] not in ('ssl', 'starttls', 'none'):
            raise ConnectionError('Sécurité SMTP : SSL/TLS, STARTTLS ou aucun')
        if is_new and 'auth' not in values:
            values['auth'] = bool(values.get('username'))
        if values.get('auth'):
            if values['tls_mode'] == 'none':
                raise ConnectionError('Authentification SMTP interdite sans TLS')
            if not values.get('username') or not values.get('password'):
                raise ConnectionError('Identifiant et mot de passe SMTP requis pour authentification')
        if values.get('from_address') and ('\r' in values['from_address'] or '\n' in values['from_address']):
            raise ConnectionError('Adresse expéditeur invalide')
    if kind == 'ftp':
        values.setdefault('tls_mode', 'explicit')
        if values['tls_mode'] not in ('explicit', 'none'):
            raise ConnectionError('Sécurité FTP non reconnue')
    if kind == 'ldap' and values.get('uri'):
        uri = values['uri']
        starttls = bool(values.get('starttls'))
        if not ((uri.startswith('ldaps://') and not starttls) or (uri.startswith('ldap://') and starttls)):
            raise ConnectionError('LDAP exige LDAPS ou STARTTLS')
    if kind == 'proxmox' and values.get('ca_cert'):
        from . import proxmox_cas
        try:
            proxmox_cas.filename(values['ca_cert'])
        except proxmox_cas.ProxmoxCAError as exc:
            raise ConnectionError(str(exc)) from None
    if kind in ('api', 'matrix', 'proxmox'):
        for key in ('base_url', 'homeserver', 'url'):
            if values.get(key) and not str(values[key]).startswith('https://'):
                raise ConnectionError('Une URL HTTPS est obligatoire')
    if not values or (is_new and kind == 'generic' and not any(values.get(k) for k in COMMON)):
        raise ConnectionError('Au moins un paramètre doit être renseigné')
    if len(str(values)) > 30000:
        raise ConnectionError('Connexion trop volumineuse')
    return values


def public(values):
    return {k: v for k,v in values.items() if k in PUBLIC_KEYS}


def open_smtp(values, timeout=8):
    """Connect and negotiate SMTP TLS/authentication; caller must close the session."""
    host=safe_host(values.get('host'))
    port=int(values.get('port') or 587)
    if not 1 <= port <= 65535:
        raise ConnectionError('Port SMTP invalide')
    mode=values.get('tls_mode') or 'starttls'
    if mode not in ('ssl', 'starttls', 'none'):
        raise ConnectionError('Mode TLS SMTP invalide')
    auth=bool(values.get('auth',bool(values.get('username'))))
    if auth and mode=='none':
        raise ConnectionError('Authentification SMTP interdite sans TLS')
    context=ssl.create_default_context()
    conn=None
    try:
        if mode=='ssl':
            conn=smtplib.SMTP_SSL(host,port,timeout=timeout,context=context)
        else:
            conn=smtplib.SMTP(host,port,timeout=timeout)
        conn.ehlo()
        if mode=='starttls':
            conn.starttls(context=context)
            conn.ehlo()
        if auth:
            if not values.get('username') or not values.get('password'):
                raise ConnectionError('Identifiants SMTP obligatoires avec authentification')
            conn.login(values['username'],values['password'])
        return conn
    except Exception:
        if conn is not None:
            conn.close()
        raise


def test_smtp(values):
    """No email is sent. TLS certificate verification is never disabled."""
    try:
        conn=open_smtp(values,timeout=8)
        try:
            authenticated=bool(values.get('auth',bool(values.get('username'))))
            message=('Connexion SMTP, TLS et authentification validés. Aucun e-mail envoyé.'
                     if authenticated else 'Connexion SMTP et mode de transport validés, sans authentification. Aucun e-mail envoyé.')
            return {'ok':True,'message':message,'tls_mode':values.get('tls_mode','starttls'),
                    'authenticated':authenticated}
        finally:
            try:
                conn.quit()
            except (OSError,smtplib.SMTPException):
                conn.close()
    except Exception as exc:
        raise ConnectionError('Test SMTP échoué : '+type(exc).__name__) from None
