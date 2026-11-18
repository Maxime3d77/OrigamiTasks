"""Controller URL configured in the web UI, with optional legacy .env fallback.

This is a public *agent-facing* HTTPS origin (Nginx, load balancer, etc.),
never the application's local bind address. No environment variables need to
change to provision a new agent.
"""
import ipaddress
from urllib.parse import urlsplit
from fastapi import HTTPException

from . import config, db


def validate_origin(value):
    if not isinstance(value,str):
        raise ValueError("L'URL de la console doit être une chaîne de caractères")
    raw=value.strip()
    if len(raw)>253 or not raw:
        raise ValueError("Renseigner l'URL publique HTTPS de la console (ex. https://origami.example.org)")
    try:
        u=urlsplit(raw)
        hostname=u.hostname or ""
        port=u.port
    except ValueError:
        raise ValueError("URL HTTPS invalide") from None
    if (u.scheme.lower()!='https' or not hostname or u.username is not None
            or u.password is not None or u.query or u.fragment
            or u.path not in ('','/') or raw.endswith('?') or raw.endswith('#')):
        raise ValueError("Saisir uniquement l'origine HTTPS (sans chemin, identifiants ni paramètres)")
    if hostname.lower().rstrip('.')=='localhost':
        raise ValueError("localhost ne peut pas être utilisé par des agents distants")
    try:
        ip=ipaddress.ip_address(hostname)
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            raise ValueError("Utiliser un FQDN HTTPS joignable par les agents, pas une adresse locale")
    except ValueError as exc:
        if str(exc).startswith("Utiliser un FQDN"):
            raise
    if len(hostname)>253 or any(ch in hostname for ch in (' ', '\\', '\t')):
        raise ValueError("Nom de domaine HTTPS invalide")
    return raw.rstrip('/')


def current():
    with db.session() as con:
        row=con.execute("SELECT controller_url,updated_at FROM agent_settings WHERE id=1").fetchone()
    if row:
        return {'controller_url':row['controller_url'], 'source':'interface',
                'configured':True, 'updated_at':row['updated_at']}
    legacy=(config.AGENT_SERVER_URL or '').strip()
    try:
        url=validate_origin(legacy) if legacy else ''
    except ValueError:
        url=''
    return {'controller_url':url,'source':'.env' if url else 'non configuré',
            'configured':bool(url),'updated_at':None}


def effective_url():
    url=current()['controller_url']
    if not url:
        raise ValueError(
            "URL HTTPS des agents non configurée : ouvrir Agents → Paramètres de connexion "
            "et renseigner le FQDN HTTPS de la console derrière Nginx."
        )
    return validate_origin(url)


def update(payload):
    if not isinstance(payload,dict) or set(payload)!={'controller_url'}:
        raise HTTPException(422,"Paramètre attendu : controller_url")
    try:
        origin=validate_origin(payload['controller_url'])
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    with db.session() as con:
        con.execute(
            "INSERT INTO agent_settings(id,controller_url,updated_at) VALUES(1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET controller_url=excluded.controller_url,"
            "updated_at=excluded.updated_at",
            (origin,db.now())
        )
    return current()
