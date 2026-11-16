from pathlib import Path
import pytest
from app import config, executor


CTX={'run_id':42, 'variables':{}, 'steps':{}}


def step(action, **kwargs):
    return {'id':'file1', 'type':'file','name':'File operation','config':{'action':action,**kwargs}}


def test_file_create_read_copy_move_list_and_delete(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'TRANSFER_ROOT',tmp_path)
    assert executor.execute(step('mkdir',path='reports'),CTX)['status']=='success'
    assert executor.execute(step('write',path='reports/report.txt',content='Rapport OK'),CTX)['status']=='success'
    assert executor.execute(step('read',path='reports/report.txt'),CTX)['output']=='Rapport OK'
    assert 'report.txt' in executor.execute(step('list',path='reports'),CTX)['output']
    assert executor.execute(step('copy',source='reports/report.txt',path='reports/copy.txt'),CTX)['status']=='success'
    assert executor.execute(step('move',source='reports/copy.txt',path='reports/moved.txt'),CTX)['status']=='success'
    with pytest.raises(executor.StepError,match='confirmation'):
        executor.execute(step('delete',path='reports/moved.txt'),CTX)
    assert executor.execute(step('delete',path='reports/moved.txt',approved=True),CTX)['status']=='success'
    assert not (tmp_path/'reports/moved.txt').exists()


def test_file_rejects_traversal_and_oversized_writes(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'TRANSFER_ROOT',tmp_path)
    with pytest.raises(executor.StepError,match='hors'):
        executor.execute(step('read',path='../outside'),CTX)
    with pytest.raises(executor.StepError,match='100 Ko'):
        executor.execute(step('write',path='huge.txt',content='x'*100001),CTX)
    secret=tmp_path/'secret-link'
    secret.symlink_to('/etc/passwd')
    with pytest.raises(executor.StepError,match='hors'):
        executor.execute(step('read',path='secret-link'),CTX)


def test_smtp_html_and_attachment_are_assembled_without_network(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'TRANSFER_ROOT',tmp_path)
    (tmp_path/'report.txt').write_text('Report data')
    sent=[]
    class Fake:
        def __init__(self,host,port,timeout):pass
        def ehlo(self):pass
        def starttls(self,context=None):pass
        def login(self,user,password):pass
        def send_message(self,msg):sent.append(msg)
        def quit(self):pass
    monkeypatch.setattr(executor.smtplib,'SMTP',Fake)
    opts={'host':'smtp.example.org','to':'ops@example.org','from':'bot@example.org','subject':'Report',
          'body':'Plain content','html_body':'<b>HTML</b>','attachments':['report.txt']}
    result=executor.execute({'id':'mail','name':'Mail','type':'email','config':opts},CTX)
    assert result['status']=='success'
    assert len(sent)==1
    assert sent[0].is_multipart()
    parts=list(sent[0].walk())
    assert any(p.get_content_type()=='text/html' for p in parts)
    assert any(p.get_filename()=='report.txt' for p in parts)
