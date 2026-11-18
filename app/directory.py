"""LDAP/Active Directory workflow adapter: TLS only, explicit write gate."""
import json
import re
import ssl
from . import config, ldap_settings

READ_ACTIONS = {'search','get_user'}
WRITE_ACTIONS = {'create_user','modify_user','group_add','group_remove',
                 'enable_user','disable_user','unlock_user','reset_password'}
MODIFIABLE = {'displayName','mail','telephoneNumber','department','title','description',
              'givenName','sn','company','mobile','manager','physicalDeliveryOfficeName'}
SAFE_READ = {'cn','dn','distinguishedName','sAMAccountName','userPrincipalName','displayName',
             'mail','memberOf','objectClass','department','title','description','givenName',
             'sn','userAccountControl','telephoneNumber','lockoutTime','uid','member','manager'}


class DirectoryError(Exception):
    pass


def dn_within(dn, base):
    from ldap3.utils.dn import parse_dn
    try:
        parsed = parse_dn(dn, escape=True)
        base_parts = parse_dn(base, escape=True)
        return len(parsed) >= len(base_parts) and [
            (attr.lower(), val.lower()) for attr,val,_ in parsed[-len(base_parts):]
        ] == [(attr.lower(),val.lower()) for attr,val,_ in base_parts]
    except Exception:
        return False


def execute(opts, credential):
    action = str(opts.get('action') or 'search')
    if action not in READ_ACTIONS | WRITE_ACTIONS:
        raise DirectoryError('Unknown LDAP/AD action')
    if action in WRITE_ACTIONS and (not config.ALLOW_DIRECTORY_WRITES or opts.get('approved') is not True):
        raise DirectoryError('Directory writes require ALLOW_DIRECTORY_WRITES=true and approved=true')
    saved = ldap_settings.current()
    uri = str(opts.get('uri') or credential.get('uri') or saved['uri'])
    starttls = bool(credential.get('starttls', saved['starttls'])) if not opts.get('uri') else bool(opts.get('starttls',False))
    if not ((uri.startswith('ldaps://') and not starttls) or (uri.startswith('ldap://') and starttls)):
        raise DirectoryError('LDAPS or LDAP+STARTTLS required')
    base = str(opts.get('base_dn') or credential.get('base_dn') or saved['base_dn'])
    if not base:
        raise DirectoryError('base_dn required')
    target = str(opts.get('target_dn') or '')
    group = str(opts.get('group_dn') or '')
    if action not in ('search','get_user') and not dn_within(target,base):
        raise DirectoryError('target_dn outside permitted LDAP base_dn')
    if action.startswith('group_') and not dn_within(group,base):
        raise DirectoryError('group_dn outside permitted LDAP base_dn')
    if not credential.get('username') and saved['enabled'] and saved['bind_password']:
        credential = dict(credential, username=saved['bind_dn'], password=saved['bind_password'])
    if not credential.get('username') or not credential.get('password'):
        raise DirectoryError('Compte de liaison LDAP manquant (coffre ou administration LDAP)')
    from ldap3 import Server, Connection, Tls, SUBTREE, BASE, MODIFY_REPLACE, MODIFY_ADD, MODIFY_DELETE, NONE
    from ldap3.utils.conv import escape_filter_chars
    import urllib.parse
    parsed = urllib.parse.urlparse(uri)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise DirectoryError('Invalid LDAP URI')
    tls = ldap_settings.tls_for(saved, uri=uri)
    server = Server(uri, tls=tls, connect_timeout=10, get_info=NONE)
    conn = Connection(server,user=credential['username'],password=credential['password'],receive_timeout=15,auto_bind=not starttls,check_names=False)
    try:
        if starttls:
            conn.open()
            if not conn.start_tls() or not conn.bind():
                raise DirectoryError('Directory bind refused')
        if action in ('search','get_user'):
            attrs = [x.strip() for x in str(opts.get('read_attributes') or 'cn,displayName,mail,sAMAccountName,memberOf').split(',')]
            if len(attrs)>20 or any(x not in SAFE_READ for x in attrs):
                raise DirectoryError('Read attribute not allowed')
            if action=='get_user':
                if not dn_within(target,base):
                    raise DirectoryError('target_dn outside permitted base')
                ok = conn.search(target,'(objectClass=*)',BASE,attributes=attrs,size_limit=1)
            else:
                # Escape user-provided exact name; do not accept raw LDAP filter text.
                attr = str(opts.get('lookup_attribute') or 'sAMAccountName')
                if attr not in {'sAMAccountName','uid','cn','mail','userPrincipalName'}:
                    raise DirectoryError('Lookup attribute not allowed')
                name = str(opts.get('lookup_value') or '')
                if not 1 <= len(name) <= 200:
                    raise DirectoryError('lookup_value required')
                query = '(&(|(objectClass=person)(objectClass=user))('+attr+'='+escape_filter_chars(name)+'))'
                ok = conn.search(base,query,SUBTREE,attributes=attrs,size_limit=50)
            if not ok and conn.result.get('result') not in (0,):
                raise DirectoryError('Directory search failed: '+str(conn.result.get('description')))
            entries = []
            for ent in conn.entries[:50]:
                entries.append({'dn':ent.entry_dn,'attributes':{
                    k:([str(v) for v in value] if isinstance(value,list) else str(value))
                    for k,value in ent.entry_attributes_as_dict.items() if k in SAFE_READ}})
            return json.dumps({'count':len(entries),'entries':entries},ensure_ascii=False)[:config.MAX_LOG_BYTES]
        attrs = opts.get('attributes') or {}
        if isinstance(attrs,str):
            try: attrs=json.loads(attrs) if attrs.strip() else {}
            except ValueError: raise DirectoryError('Invalid attributes JSON')
        if not isinstance(attrs,dict):
            raise DirectoryError('attributes must be an object')
        if action=='create_user':
            allowed = MODIFIABLE | {'cn','sAMAccountName','userPrincipalName','uid','objectClass'}
            if not attrs or any(k not in allowed for k in attrs):
                raise DirectoryError('Invalid create_user attributes')
            classes = attrs.pop('objectClass', ['top','person','organizationalPerson','user'])
            if not isinstance(classes,list) or not classes or any(c not in {'top','person','organizationalPerson','user','inetOrgPerson'} for c in classes):
                raise DirectoryError('Invalid LDAP objectClass')
            attrs.setdefault('userAccountControl',514) if 'user' in classes else None
            ok = conn.add(target,classes,attrs)
        elif action=='modify_user':
            if not attrs or any(k not in MODIFIABLE for k in attrs):
                raise DirectoryError('Only ordinary profile attributes may be modified')
            ok = conn.modify(target,{k:[(MODIFY_REPLACE,v if isinstance(v,list) else [v])] for k,v in attrs.items()})
        elif action in ('group_add','group_remove'):
            # Use member for AD, uniqueMember for POSIX groups where requested.
            member_attr = str(opts.get('member_attribute') or 'member')
            if member_attr not in ('member','uniqueMember'):
                raise DirectoryError('Invalid group membership attribute')
            ok = conn.modify(group,{member_attr:[(MODIFY_ADD if action=='group_add' else MODIFY_DELETE,[target])]})
        elif action in ('disable_user','enable_user'):
            if not conn.search(target,'(objectClass=*)',BASE,attributes=['userAccountControl'],size_limit=1) or not conn.entries:
                raise DirectoryError('AD user not found')
            current = int(conn.entries[0].userAccountControl.value)
            new = (current | 2) if action=='disable_user' else (current & ~2)
            ok = conn.modify(target,{'userAccountControl':[(MODIFY_REPLACE,[new])]})
        elif action=='unlock_user':
            ok = conn.modify(target,{'lockoutTime':[(MODIFY_REPLACE,[0])]})
        elif action=='reset_password':
            # The new password must come from a DIFFERENT vault secret, never steps JSON.
            pw_id = opts.get('password_credential_id')
            if not pw_id:
                raise DirectoryError('password_credential_id required')
            from .executor import credential as load_credential
            passwd = load_credential(pw_id).get('password')
            if not passwd or len(passwd)<12 or not uri.startswith('ldaps://'):
                raise DirectoryError('Password secret >=12 chars and LDAPS required')
            ok = conn.modify(target,{'unicodePwd':[(MODIFY_REPLACE,[('"'+passwd+'"').encode('utf-16-le')])]})
        else:
            raise DirectoryError('Unsupported action')
        if not ok:
            raise DirectoryError('Directory operation rejected: '+str(conn.result.get('description')))
        return json.dumps({'action':action,'ok':True,'dn':target},ensure_ascii=False)
    finally:
        conn.unbind()
