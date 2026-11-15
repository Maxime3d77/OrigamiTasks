"""Authentication, session/CSRF, LDAP over TLS and encrypted credential vault."""
import hashlib
import hmac
import json
import re
import secrets
import ssl
from datetime import datetime, timezone, timedelta
from cryptography.fernet import Fernet
from argon2 import PasswordHasher, exceptions as argon_exceptions
from fastapi import HTTPException, Request
from . import config, db

ph = PasswordHasher()


def sha(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def cipher():
    if not config.VAULT_KEY:
        raise RuntimeError('VAULT_KEY non défini : lancez ops/bootstrap.sh')
    return Fernet(config.VAULT_KEY.encode())


def encrypt(data):
    return cipher().encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()


def decrypt(blob):
    return json.loads(cipher().decrypt(blob.encode()))


def initialize_admin():
    if not config.ADMIN_PASSWORD:
        with db.session() as con:
            n = con.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        if not n:
            raise RuntimeError('ADMIN_PASSWORD manquant pour créer le premier administrateur')
        return
    with db.session() as con:
        existing = con.execute('SELECT id FROM users WHERE username=?', ('admin',)).fetchone()
        if not existing:
            if len(config.ADMIN_PASSWORD) < 12:
                raise RuntimeError('ADMIN_PASSWORD doit faire au moins 12 caractères')
            con.execute('INSERT INTO users (username,display_name,password_hash,source,role,groups_json,created_at) VALUES (?,?,?,?,?,?,?)',
                        ('admin', 'Administrateur local', ph.hash(config.ADMIN_PASSWORD), 'local', 'admin', '["admins"]', db.now()))


def ldap_authenticate(username, password):
    """LDAP users must bind successfully; directory group DNs map to job ACL and roles."""
    if not config.LDAP_URI:
        return None
    if not (config.LDAP_URI.startswith('ldaps://') or config.LDAP_STARTTLS):
        raise RuntimeError('LDAP refuse toute liaison en clair : LDAPS ou STARTTLS requis')
    try:
        from ldap3 import Server, Connection, SUBTREE, Tls
        from ldap3.utils.conv import escape_filter_chars
        from ldap3.core.exceptions import LDAPException
        uri = config.LDAP_URI
        tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=config.LDAP_CA_CERT or None)
        server = Server(uri, tls=tls, connect_timeout=8)
        svc = Connection(server, user=config.LDAP_BIND_DN, password=config.LDAP_BIND_PASSWORD,
                         auto_bind=not config.LDAP_STARTTLS, receive_timeout=8)
        if config.LDAP_STARTTLS:
            svc.open()
            if not svc.start_tls() or not svc.bind():
                return None
        attr = config.LDAP_USER_ATTRIBUTE
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*', attr):
            raise RuntimeError('LDAP_USER_ATTRIBUTE non valide')
        query = f'(&(objectClass=person)({attr}={escape_filter_chars(username)}))'
        svc.search(config.LDAP_BASE_DN, query, SUBTREE, attributes=['cn', 'displayName', 'memberOf'])
        if len(svc.entries) != 1:
            svc.unbind()
            return None
        entry = svc.entries[0]
        user_dn = entry.entry_dn
        groups = [str(g) for g in entry.memberOf.values] if 'memberOf' in entry else []
        display_name = str(entry.displayName.value or username) if 'displayName' in entry else username
        if config.LDAP_NESTED_GROUPS:
            # AD LDAP_MATCHING_RULE_IN_CHAIN: nested group memberships for user DN.
            svc.search(config.LDAP_GROUP_BASE_DN, f'(&(objectClass=group)(member:1.2.840.113556.1.4.1941:={escape_filter_chars(user_dn)}))', SUBTREE, attributes=['distinguishedName'])
            groups.extend(x.entry_dn for x in svc.entries)
        svc.unbind()
        user_conn = Connection(server, user=user_dn, password=password, auto_bind=not config.LDAP_STARTTLS, receive_timeout=8)
        if config.LDAP_STARTTLS:
            user_conn.open()
            if not user_conn.start_tls() or not user_conn.bind():
                return None
        user_conn.unbind()
        names = sorted({m.group(1).lower() for dn in groups if (m := re.search(r'CN=([^,]+)', dn, re.I))})
        if config.LDAP_ADMIN_GROUP.lower() in names:
            role = 'admin'
        elif config.LDAP_OPERATOR_GROUP.lower() in names:
            role = 'operator'
        elif config.LDAP_VIEWER_GROUP.lower() in names:
            role = 'viewer'
        else:
            return None  # refuse directory users with no mapped application group
        return {'username': username.lower(), 'display_name': display_name, 'role': role, 'groups': names}
    except (OSError, ConnectionError, LDAPException):
        return None


def authenticate(username, password):
    username = username.strip().lower()
    if not username or not password:
        return None
    with db.session() as con:
        local = con.execute("SELECT * FROM users WHERE username=? AND source='local'", (username,)).fetchone()
    if local:
        try:
            ph.verify(local['password_hash'], password)
            return db.obj(local, 'groups_json')
        except (argon_exceptions.VerifyMismatchError, argon_exceptions.VerificationError):
            return None
    info = ldap_authenticate(username, password)
    if not info:
        return None
    with db.session() as con:
        con.execute('INSERT INTO users (username,display_name,password_hash,source,role,groups_json,created_at) VALUES (?,?,?,?,?,?,?) '
                    'ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name,role=excluded.role,groups_json=excluded.groups_json',
                    (info['username'], info['display_name'], None, 'ldap', info['role'], json.dumps(info['groups']), db.now()))
        row = con.execute('SELECT * FROM users WHERE username=?', (info['username'],)).fetchone()
    return db.obj(row, 'groups_json')


def create_session(user):
    sid, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    expire = (datetime.now(timezone.utc) + timedelta(hours=config.SESSION_HOURS)).isoformat(timespec='seconds')
    with db.session() as con:
        con.execute('DELETE FROM sessions WHERE expires_at<?', (db.now(),))
        con.execute('INSERT INTO sessions (sid_hash,user_id,csrf_hash,expires_at) VALUES (?,?,?,?)',
                    (sha(sid), user['id'], sha(csrf), expire))
    return sid, csrf


def get_user(request: Request, mutate=False):
    sid = request.cookies.get('origami_session', '')
    if not sid:
        raise HTTPException(401, 'Connexion requise')
    with db.session() as con:
        user = con.execute('SELECT users.*,sessions.csrf_hash FROM sessions JOIN users ON users.id=sessions.user_id '
                           'WHERE sid_hash=? AND expires_at>?', (sha(sid), db.now())).fetchone()
    if not user:
        raise HTTPException(401, 'Session expirée')
    result = db.obj(user, 'groups_json')
    if mutate:
        csrf = request.headers.get('X-CSRF-Token', '')
        if not csrf or not hmac.compare_digest(sha(csrf), result['csrf_hash']):
            raise HTTPException(403, 'Jeton CSRF manquant ou invalide')
    result.pop('password_hash', None)
    result.pop('csrf_hash', None)
    return result


def require(request, role=None, mutate=False):
    u = get_user(request, mutate)
    roles = {'viewer': 1, 'operator': 2, 'admin': 3}
    if role and roles.get(u['role'], 0) < roles.get(role, 99):
        raise HTTPException(403, 'Droits insuffisants')
    return u


def can_job(user, job, action='view'):
    if user['role'] == 'admin':
        return True
    if action in ('run', 'edit') and user['role'] == 'viewer':
        return False
    acl = job.get('acl_json') or {}
    if isinstance(acl, str):
        acl = json.loads(acl)
    groups = {g.lower() for g in user.get('groups_json', [])}
    levels = {'view': 1, 'run': 2, 'edit': 3}
    for perm, min_level in [('view',1), ('run',2), ('edit',3)]:
        if levels[perm] < levels[action]:
            continue
        if groups.intersection({g.lower() for g in acl.get(perm, [])}):
            return True
    return False


def present_user(user):
    return {'id': user['id'], 'username': user['username'], 'display_name': user['display_name'],
            'role': user['role'], 'groups': user.get('groups_json', []), 'source': user['source']}
