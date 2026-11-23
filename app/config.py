"""Runtime configuration: environment variables, no runtime secrets in git."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv('ORIGAMI_DATA_DIR', str(BASE / 'data'))).resolve()
DB = DATA / 'origamitasks.db'
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'true').lower() == 'true'
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
VAULT_KEY = os.getenv('VAULT_KEY', '')
LDAP_URI = os.getenv('LDAP_URI', '')
LDAP_BASE_DN = os.getenv('LDAP_BASE_DN', '')
LDAP_BIND_DN = os.getenv('LDAP_BIND_DN', '')
LDAP_BIND_PASSWORD = os.getenv('LDAP_BIND_PASSWORD', '')
LDAP_ADMIN_GROUP = os.getenv('LDAP_ADMIN_GROUP', 'origamitasks-admins')
LDAP_OPERATOR_GROUP = os.getenv('LDAP_OPERATOR_GROUP', 'origamitasks-operators')
LDAP_VIEWER_GROUP = os.getenv('LDAP_VIEWER_GROUP', 'origamitasks-viewers')
LDAP_USER_ATTRIBUTE = os.getenv('LDAP_USER_ATTRIBUTE', 'sAMAccountName')
LDAP_STARTTLS = os.getenv('LDAP_STARTTLS', 'false').lower() == 'true'
LDAP_CA_CERT = os.getenv('LDAP_CA_CERT', '')
# Explicit, high-risk exception, independently gated on the host.
ALLOW_INSECURE_LDAP_TLS = os.getenv('ALLOW_INSECURE_LDAP_TLS', 'false').lower() == 'true'
LDAP_GROUP_BASE_DN = os.getenv('LDAP_GROUP_BASE_DN', LDAP_BASE_DN)
LDAP_NESTED_GROUPS = os.getenv('LDAP_NESTED_GROUPS', 'false').lower() == 'true'
GIT_BRANCH = os.getenv('GIT_BRANCH', 'main')
APP_VERSION = '0.3.42-rc1'
SESSION_HOURS = int(os.getenv('SESSION_HOURS', '12'))
WORKERS = int(os.getenv('ORIGAMI_EXECUTOR_WORKERS', '2'))
ALLOW_LOCAL_EXECUTION = os.getenv('ALLOW_LOCAL_EXECUTION', 'false').lower() == 'true'
MAX_LOG_BYTES = int(os.getenv('MAX_LOG_BYTES', '40000'))
MAX_STEP_SECONDS = int(os.getenv('MAX_STEP_SECONDS', '1800'))
TRANSFER_ROOT = DATA / 'transfers'
KNOWN_HOSTS = Path(os.getenv('SSH_KNOWN_HOSTS', str(DATA / 'known_hosts')))
SSH_HOST_KEY_POLICY = os.getenv('SSH_HOST_KEY_POLICY', 'accept-new').strip().lower()

# Restricted outbound API integrations: comma-separated exact hostnames or host:port
CONNECTION_TEST_ALLOWED_HOSTS = os.getenv("CONNECTION_TEST_ALLOWED_HOSTS", "")
MATRIX_ALLOWED_HOSTS = os.getenv("MATRIX_ALLOWED_HOSTS", "")
PROXMOX_ALLOWED_HOSTS = os.getenv("PROXMOX_ALLOWED_HOSTS", "")
PROXMOX_CA_CERT = os.getenv("PROXMOX_CA_CERT", "")
ALLOW_PROXMOX_ACTIONS = os.getenv("ALLOW_PROXMOX_ACTIONS", "false").lower() == "true"

# Agent management. All high-impact features are opt-in, independently gated.
AGENT_SERVER_URL = os.getenv('AGENT_SERVER_URL', '')
ALLOW_AGENT_REMOTE_DEPLOY = os.getenv('ALLOW_AGENT_REMOTE_DEPLOY', 'false').lower() == 'true'
AGENT_DEPLOY_ALLOWED_HOSTS = os.getenv('AGENT_DEPLOY_ALLOWED_HOSTS', '')
ALLOW_AGENT_SCRIPTS = os.getenv('ALLOW_AGENT_SCRIPTS', 'false').lower() == 'true'
ALLOW_AGENT_MUTATIONS = os.getenv('ALLOW_AGENT_MUTATIONS', 'false').lower() == 'true'
ALLOW_DIRECTORY_WRITES = os.getenv('ALLOW_DIRECTORY_WRITES', 'false').lower() == 'true'
