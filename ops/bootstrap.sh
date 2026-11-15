#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -e .env ]]; then echo '.env existe déjà, aucune modification.'; exit 0; fi
command -v python3 >/dev/null || { echo 'Python 3 nécessaire pour générer les secrets'; exit 1; }
umask 077
python3 - <<'PY'
from pathlib import Path
from secrets import token_urlsafe
try:
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
except ImportError:
    import base64, os
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
password=token_urlsafe(24)
s=f'''ADMIN_PASSWORD={password}\nVAULT_KEY={key}\nCOOKIE_SECURE=true\nALLOW_LOCAL_EXECUTION=false\nORIGAMI_PORT=8080\nORIGAMI_EXECUTOR_WORKERS=2\nMAX_STEP_SECONDS=1800\nSESSION_HOURS=12\nGIT_BRANCH=main\nGIT_ALLOWED_ORIGIN=\nLDAP_URI=\nLDAP_BIND_DN=\nLDAP_BIND_PASSWORD=\nLDAP_BASE_DN=\nLDAP_GROUP_BASE_DN=\nLDAP_USER_ATTRIBUTE=sAMAccountName\nLDAP_ADMIN_GROUP=origamitasks-admins\nLDAP_OPERATOR_GROUP=origamitasks-operators\nLDAP_VIEWER_GROUP=origamitasks-viewers\nLDAP_NESTED_GROUPS=false\nLDAP_STARTTLS=false\nLDAP_CA_CERT=\n'''
Path('.env').write_text(s)
print('==========================================================')
print('Utilisateur local : admin')
print('Mot de passe INITIAL (sauvegarder dans un coffre-fort):')
print(password)
print('==========================================================')
PY
chmod 600 .env
mkdir -p data/transfers
if [[ "$(id -u)" == 0 ]]; then chown -R 10001:10001 data; else
  echo 'IMPORTANT : le volume data doit appartenir à UID 10001 pour le conteneur.'
  echo 'Exécuter : sudo chown -R 10001:10001 data'
fi
printf 'Génération terminée. Configurez le HTTPS avant exposition réseau.\n'
