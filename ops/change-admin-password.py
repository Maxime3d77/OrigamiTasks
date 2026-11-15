#!/usr/bin/env python3
"""Rotate local emergency admin password, runnable on host with the matching Python deps."""
import argparse
import getpass
import sqlite3
import sys
from pathlib import Path

try: from argon2 import PasswordHasher
except ImportError: raise SystemExit('Installer argon2-cffi ou exécuter dans le venv du projet')
parser=argparse.ArgumentParser()
parser.add_argument('--database',default=str(Path(__file__).resolve().parent.parent/'data/origamitasks.db'))
a=parser.parse_args()
p=getpass.getpass('Nouveau mot de passe (>= 12 caractères): ')
if len(p)<12:sys.exit('Trop court')
q=getpass.getpass('Confirmer : ')
if p!=q:sys.exit('Les mots de passe ne correspondent pas')
con=sqlite3.connect(a.database)
result=con.execute("UPDATE users SET password_hash=? WHERE username='admin' AND source='local'",(PasswordHasher().hash(p),))
con.commit();con.close()
if result.rowcount!=1:sys.exit('Compte local admin introuvable')
print('Mot de passe admin modifié. Les sessions existantes ne sont pas révoquées automatiquement.')
