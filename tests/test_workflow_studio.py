"""UI-support API regression tests. No external LDAP or SMTP servers are contacted."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from fastapi.testclient import TestClient

from app import scheduler, db, security
from app.main import app


def logged_admin():
    client = TestClient(app)
    client.__enter__()
    response = client.post('/api/login', json={
        'username': 'admin', 'password': 'test-admin-password-over-12'})
    assert response.status_code == 200, response.text
    return client, {'X-CSRF-Token': client.cookies['origami_csrf']}


def test_graphical_scheduler_modes():
    now = datetime.now(timezone.utc)
    assert scheduler.check_schedule({'mode': 'manual'}) is None
    t = scheduler.check_schedule({'mode': 'interval', 'every': 5,
                                   'unit': 'minutes', 'timezone': 'Europe/Paris'}, now)
    assert abs((datetime.fromisoformat(t) - (now + timedelta(minutes=5))).total_seconds()) < 1
    t = scheduler.check_schedule({'mode': 'interval', 'every': 2,
                                   'unit': 'hours', 'timezone': 'Europe/Paris'}, now)
    assert abs((datetime.fromisoformat(t) - (now + timedelta(hours=2))).total_seconds()) < 1
    future = (now + timedelta(hours=1)).astimezone(
        __import__('zoneinfo').ZoneInfo('Europe/Paris'))
    date = future.strftime('%Y-%m-%dT%H:%M')
    once = scheduler.check_schedule({'mode': 'once', 'at': date,
                                      'timezone': 'Europe/Paris'}, now)
    assert abs((datetime.fromisoformat(once) - future).total_seconds()) < 60
    assert scheduler.check_schedule({'mode': 'once',
                                     'at': '2000-01-01T08:00',
                                     'timezone': 'Europe/Paris'}, now) is None
    assert scheduler.check_schedule({'cron': '*/5 * * * *'}) is not None
    assert scheduler.check_schedule({'mode': 'cron',
                                     'cron': '*/5 * * * *'}) is not None
    for bad in [{'mode': 'bad'}, {'mode': 'once', 'at': 'not-a-date'},
                {'mode': 'interval', 'every': 0, 'unit': 'minutes'},
                {'mode': 'interval', 'every': 5, 'unit': 'seconds'},
                {'mode': 'cron', 'cron': '* * * *'}]:
        try:
            scheduler.check_schedule(bad, now)
        except ValueError:
            pass
        else:
            raise AssertionError(f'Invalid schedule accepted: {bad}')


def test_folder_tree_create_rename_delete_preserves_jobs():
    c, h = logged_admin()
    try:
        root = c.post('/api/folders', json={'parent': '', 'name': 'RecetteUI'}, headers=h)
        assert root.status_code == 200, root.text
        child = c.post('/api/folders', json={'parent': root.json()['path'], 'name': 'Linux'}, headers=h)
        assert child.status_code == 200, child.text
        path = child.json()['path']
        job = c.post('/api/jobs', json={
            'name': 'Dossier-test', 'folder': path, 'steps_json': [
                {'id': 'one', 'name': 'Pause', 'type': 'delay', 'config': {'seconds': 1}}],
            'acl_json': {'view': [], 'run': [], 'edit': []},
            'schedule_json': {}, 'enabled': False}, headers=h)
        assert job.status_code == 200, job.text
        jid = job.json()['id']
        listed = c.get('/api/folders').json()['items']
        assert any(f['path'] == path and f['depth'] == 1 for f in listed)
        renamed = c.put('/api/folders', json={'path': 'RecetteUI', 'name': 'RecetteUIRenommee'}, headers=h)
        assert renamed.status_code == 200, renamed.text
        assert c.get(f'/api/jobs/{jid}').json()['folder'] == 'RecetteUIRenommee/Linux'
        assert c.delete('/api/folders', params={'path': 'RecetteUIRenommee'}, headers=h).status_code == 200
        assert c.get(f'/api/jobs/{jid}').json()['folder'] == 'Général'
        assert c.get(f'/api/jobs/{jid}').status_code == 200
        assert c.delete('/api/folders', params={'path': 'Général'}, headers=h).status_code == 422
        assert c.post('/api/folders', json={'parent': '', 'name': '..'}, headers=h).status_code == 422
    finally:
        c.__exit__(None, None, None)


def test_vault_update_clone_keeps_existing_secrets_encrypted():
    c, h = logged_admin()
    try:
        new = c.post('/api/credentials', json={'name': 'CI-ui-vault-origin',
                  'kind': 'ldap', 'payload': {'username': 'bind', 'password': 'secret-A'}}, headers=h)
        assert new.status_code == 200, new.text
        cred_id = new.json()['id']
        listing = c.get('/api/credentials')
        assert 'secret-A' not in listing.text
        entry = next(item for item in listing.json()['items'] if item['id'] == cred_id)
        assert set(entry['fields']) == {'username', 'password'}
        modified = c.put(f'/api/credentials/{cred_id}', json={'name': 'CI-ui-vault-origin',
                  'kind': 'ldap', 'payload': {'username': 'new-bind'}}, headers=h)
        assert modified.status_code == 200, modified.text
        with db.session() as conn:
            row = conn.execute('SELECT encrypted_payload FROM credentials WHERE id=?', (cred_id,)).fetchone()
        assert security.decrypt(row['encrypted_payload']) == {'username': 'new-bind', 'password': 'secret-A'}
        cloned = c.post(f'/api/credentials/{cred_id}/clone',
                        json={'name': 'CI-ui-vault-copy'}, headers=h)
        assert cloned.status_code == 200, cloned.text
        copy_id = cloned.json()['id']
        with db.session() as conn:
            other = conn.execute('SELECT encrypted_payload FROM credentials WHERE id=?', (copy_id,)).fetchone()
        assert security.decrypt(other['encrypted_payload']) == security.decrypt(row['encrypted_payload'])
        assert other['encrypted_payload'] != row['encrypted_payload']
        assert 'secret-A' not in c.get('/api/credentials').text
        assert c.delete(f'/api/credentials/{copy_id}', headers=h).status_code == 200
        assert c.delete(f'/api/credentials/{cred_id}', headers=h).status_code == 200
    finally:
        c.__exit__(None, None, None)


def test_directory_group_picker_uses_known_groups_when_ldap_unavailable():
    c, h = logged_admin()
    try:
        result = c.get('/api/directory/groups', params={'q': 'admins'})
        assert result.status_code == 200, result.text
        assert any(g['name'] == 'admins' for g in result.json()['items'])
        assert c.post('/api/folders', json={'name': 'ForbiddenNoCsrf'}).status_code == 403
    finally:
        c.__exit__(None, None, None)


def test_navigation_buttons_have_single_delegated_handler():
    root = Path(__file__).resolve().parent.parent
    markup = (root/'static/index.html').read_text()
    js = (root/'static/app.js').read_text()
    assert 'data-page="dashboard"' in markup
    assert 'data-page="vault"' in markup
    assert "closest('[data-action], [data-page]')" in js
    assert "const a=b.dataset.action||'page'" in js


def test_folder_names_do_not_leak_to_unprivileged_users():
    c, h = logged_admin()
    try:
        created = c.post('/api/jobs', json={
            'name': 'Private-folder-smoke', 'folder': 'ConfidentielUI/Secret',
            'steps_json': [{'id': 'pause', 'name': 'Attente', 'type': 'delay',
                            'config': {'seconds': 1}}],
            'acl_json': {'view': ['security-team'], 'run': [], 'edit': []},
            'schedule_json': {}, 'enabled': False}, headers=h)
        assert created.status_code == 200, created.text
        with db.session() as conn:
            conn.execute('INSERT OR IGNORE INTO users(username,display_name,password_hash,source,role,groups_json,created_at) VALUES (?,?,?,?,?,?,?)',
                         ('viewer-ui-ci', 'Viewer UI', security.ph.hash('viewer-password-long-123'),
                          'local', 'viewer', '["unrelated-group"]', db.now()))
        with TestClient(app) as viewer:
            assert viewer.post('/api/login', json={
                'username': 'viewer-ui-ci', 'password': 'viewer-password-long-123'
            }).status_code == 200
            folders = viewer.get('/api/folders').json()['items']
            assert not any('ConfidentielUI' in f['path'] for f in folders)
            jobs = viewer.get('/api/jobs').json()['items']
            assert not any(j['id'] == created.json()['id'] for j in jobs)
    finally:
        c.__exit__(None, None, None)


def test_in_use_vault_credential_cannot_be_deleted():
    c, h = logged_admin()
    try:
        created = c.post('/api/credentials', json={
            'name': 'CI-ui-vault-referenced', 'kind': 'smtp',
            'payload': {'username': 'tester', 'password': 'secret'}}, headers=h)
        assert created.status_code == 200, created.text
        cid = created.json()['id']
        job = c.post('/api/jobs', json={
            'name': 'Vault reference guard', 'folder': 'Général',
            'steps_json': [{'id': 'send', 'name': 'Mail', 'type': 'email',
                            'config': {'credential_id': cid, 'host': 'example.org'}}],
            'acl_json': {'view': [], 'run': [], 'edit': []},
            'schedule_json': {}, 'enabled': False}, headers=h)
        assert job.status_code == 200, job.text
        assert c.delete(f'/api/credentials/{cid}', headers=h).status_code == 409
        assert c.delete(f'/api/jobs/{job.json()["id"]}', headers=h).status_code == 200
        assert c.delete(f'/api/credentials/{cid}', headers=h).status_code == 200
    finally:
        c.__exit__(None, None, None)
