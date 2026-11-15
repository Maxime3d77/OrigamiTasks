import os
import tempfile
from cryptography.fernet import Fernet

TEST_DIR=tempfile.mkdtemp(prefix='origami-tests-')
os.environ['ORIGAMI_DATA_DIR']=TEST_DIR
os.environ['ADMIN_PASSWORD']='test-admin-password-over-12'
os.environ['VAULT_KEY']=Fernet.generate_key().decode()
os.environ['COOKIE_SECURE']='false'
os.environ['ALLOW_LOCAL_EXECUTION']='true'
os.environ['LDAP_URI']=''
