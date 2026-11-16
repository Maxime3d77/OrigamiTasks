"""Editable LDAP settings overlay: .env defaults, encrypted SQLite overrides, safe bind test."""
import re
import ssl
from pathlib import Path
from urllib.parse import urlsplit

from . import config, db, security

FIELDS = ('enabled', 'uri', 'starttls', 'bind_dn', 'bind_password',
          'base_dn', 'group_base_dn', 'user_attribute', 'admin_group',
          'operator_group', 'viewer_group', 'nested_groups', 'ca_cert')
SECRET = 'bind_password'
BOOL = {'enabled', 'starttls', 'nested_groups'}


class LDAPSettingsError(Exception):
    pass


def defaults():
    return dict(enabled=bool(config.LDAP_URI), uri=config.LDAP_URI,
                starttls=config.LDAP_STARTTLS, bind_dn=config.LDAP_BIND_DN,
                bind_password=config.LDAP_BIND_PASSWORD,
                base_dn=config.LDAP_BASE_DN, group_base_dn=config.LDAP_GROUP_BASE_DN or config.LDAP_BASE_DN,
                user_attribute=config.LDAP_USER_ATTRIBUTE,
                admin_group=config.LDAP_ADMIN_GROUP, operator_group=config.LDAP_OPERATOR_GROUP,
                viewer_group=config.LDAP_VIEWER_GROUP, nested_groups=config.LDAP_NESTED_GROUPS,
                ca_cert=config.LDAP_CA_CERT)


def current():
    settings = defaults()
    with db.session() as con:
        row = con.execute('SELECT encrypted_config FROM ldap_settings WHERE id=1').fetchone()
    if row:
        settings.update(security.decrypt(row['encrypted_config']))
    return settings


def public(settings=None):
    data = dict(settings or current())
    has_password = bool(data.pop('bind_password', ''))
    data['has_bind_password'] = has_password
    return data


def check_settings(data):
    if not data.get('enabled'):
        return data
    uri = str(data.get('uri') or '').strip()
    try:
        parsed = urlsplit(uri)
        if parsed.scheme not in ('ldap', 'ldaps') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError()
        if (parsed.scheme == 'ldap') != bool(data.get('starttls')):
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
    except (ValueError, AttributeError):
        raise LDAPSettingsError('Renseigner ldaps://serveur:636 ou ldap://serveur:389 avec STARTTLS') from None
    if not data.get('bind_dn') or not data.get('bind_password') or not data.get('base_dn'):
        raise LDAPSettingsError('DN de service, mot de passe et base DN sont obligatoires')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*', str(data.get('user_attribute') or '')):
        raise LDAPSettingsError('Attribut utilisateur invalide, exemple uid ou sAMAccountName')
    if not str(data.get('group_base_dn') or '').strip():
        raise LDAPSettingsError('Base DN des groupes obligatoire')
    for key in ('admin_group', 'operator_group', 'viewer_group'):
        if not re.fullmatch(r'[A-Za-z0-9_.@\- ]{1,120}', str(data.get(key) or '')):
            raise LDAPSettingsError('Nom de groupe LDAP invalide : '+key)
    if data.get('ca_cert'):
        path = Path(str(data['ca_cert'])).resolve()
        # Paths are administrator-configured but must remain in trusted certificates or app data.
        roots = (Path('/etc/ssl').resolve(), config.DATA.resolve())
        if not path.is_file() or not any(path.is_relative_to(root) for root in roots):
            raise LDAPSettingsError('Chemin de certificat CA illisible ou non autorisé')
    return data


def merge(updates, previous=None):
    if not isinstance(updates, dict) or set(updates) - set(FIELDS):
        raise LDAPSettingsError('Paramètres LDAP non reconnus')
    data = dict(previous or current())
    for key, value in updates.items():
        if key in BOOL:
            if not isinstance(value, bool):
                raise LDAPSettingsError('Paramètre booléen LDAP invalide : '+key)
            data[key] = value
        elif key == SECRET:
            if not isinstance(value, str):
                raise LDAPSettingsError('Mot de passe de service LDAP invalide')
            if value:
                data[key] = value
        else:
            if not isinstance(value, str) or len(value) > 1000:
                raise LDAPSettingsError('Paramètre LDAP invalide : '+key)
            data[key] = value.strip()
    return check_settings(data)


def save(settings):
    with db.session() as con:
        con.execute('INSERT INTO ldap_settings(id,encrypted_config,updated_at) VALUES (1,?,?) '
                    'ON CONFLICT(id) DO UPDATE SET encrypted_config=excluded.encrypted_config,updated_at=excluded.updated_at',
                    (security.encrypt(settings), db.now()))


def test(settings, user_login=''):
    """Read-only LDAP bind + scoped searches, with no entries or credentials returned."""
    if not settings.get('enabled'):
        raise LDAPSettingsError('Activer LDAP pour lancer le test')
    check_settings(settings)
    try:
        from ldap3 import Server, Connection, SUBTREE, BASE, Tls
        from ldap3.utils.conv import escape_filter_chars
        server = Server(settings['uri'],
                        tls=Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=settings.get('ca_cert') or None),
                        connect_timeout=7)
        conn = Connection(server,user=settings['bind_dn'],password=settings['bind_password'],
                          auto_bind=not settings.get('starttls'),receive_timeout=10)
        try:
            if settings.get('starttls'):
                conn.open()
                if not conn.start_tls() or not conn.bind():
                    raise LDAPSettingsError('Liaison LDAP refusée')
            if not conn.search(settings['base_dn'],'(objectClass=*)',BASE,attributes=['objectClass'],size_limit=1):
                raise LDAPSettingsError('Base DN inaccessible')
            group_filter='(|(objectClass=posixGroup)(objectClass=groupOfNames)(objectClass=groupOfUniqueNames)(objectClass=group))'
            conn.search(settings['group_base_dn'], group_filter, SUBTREE, attributes=['cn'],size_limit=25)
            count = len(conn.entries)
            found = False
            login = str(user_login or '').strip()
            if login:
                if len(login)>100:
                    raise LDAPSettingsError('Login de test trop long')
                attr = settings['user_attribute']
                found = conn.search(settings['base_dn'],
                     '(&(objectClass=person)('+attr+'='+escape_filter_chars(login)+'))',
                     SUBTREE,attributes=['cn'],size_limit=2)
            return {'ok':True,'message':'Connexion TLS, bind et recherches LDAP réussis',
                    'group_sample_count':count,'user_tested':bool(login),'user_found':bool(found)}
        finally:
            conn.unbind()
    except LDAPSettingsError:
        raise
    except Exception as exc:
        # Avoid echoing credentials, server responses or sensitive LDAP results.
        raise LDAPSettingsError('Échec LDAP ('+type(exc).__name__+') : vérifier TLS, résolution DNS, bind et bases DN') from None
