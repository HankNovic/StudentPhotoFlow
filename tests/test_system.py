"""Synthetic inputs only. Run in image with httpx installed for TestClient."""
import io
import tempfile
import time
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from v2.system import create_system_app
from v2 import recycle
from v2.store import Store

class SystemTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_system_app(self.tmp.name,'synthetic-admin-secret-123456')
        self.client=TestClient(self.app)
        self.client.post('/session',json={'token':'synthetic-admin-secret-123456'})
        self.system=self.app.state.system
        d=self.client.get('/api/v1/settings').json()
        d.update(cohorts=[{'year':'2027'},{'year':'2026'}])
        r=self.client.put('/api/v1/settings',json=d);self.assertEqual(r.status_code,200,r.text)
        self.cid=r.json()['cohorts'][0]['id'];self.other=r.json()['cohorts'][1]['id']
        self.base='/cohorts/'+self.cid+'/api/v1/'
        self.service=self.system.child(self.cid).state.service
        self.store=self.service.store
        self.client.post(self.base+'roster',json={'student_ids':['00001','00002','00003']})
        out=io.BytesIO();Image.new('RGB',(100,140),'blue').save(out,'JPEG');self.photo=out.getvalue()

    def tearDown(self):
        if self.service.thread:self.service.thread.join(10)
        self.tmp.cleanup()

    def post(self,p,body):
        r=self.client.post(p,json=body);self.assertEqual(r.status_code,200,r.text);return r.json()

    def process(self,ids):
        for sid in ids:self.store.upload(sid,self.photo)
        job=self.service.start(ids,{'crop_enabled':True},request_id='check-'+','.join(ids));self.service.thread.join(10)
        for sid in ids:
            s=self.store.snapshot()['students'][sid];r=s['results'][-1]
            self.store.review(sid,r['id'],'approved',len(s['history']))
        return job

    def test_cohort_identity_archive_deletion_and_sessions(self):
        settings=self.client.get('/api/v1/settings').json();settings['cohorts'][0]['year']='2028'
        r=self.client.put('/api/v1/settings',json=settings);self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['cohorts'][0]['id'],self.cid)
        self.assertEqual(len(self.client.get(self.base+'students').json()),3)
        for year in ['abcd','20','2027.0','0000','2026']:
            d=self.client.get('/api/v1/settings').json();d['cohorts'][0]['year']=year
            self.assertEqual(self.client.put('/api/v1/settings',json=d).status_code,409)
        d=self.client.get('/api/v1/settings').json();d['cohorts'][0]['archived']=True
        self.assertEqual(self.client.put('/api/v1/settings',json=d).status_code,200)
        self.assertEqual(self.client.post(self.base+'roster',json={'student_ids':['x']}).status_code,409)
        d=self.client.get('/api/v1/settings').json();d['cohorts']=d['cohorts'][1:];d['confirmed_deletions']=[self.cid]
        self.assertEqual(self.client.put('/api/v1/settings',json=d).status_code,409)
        self.assertEqual(self.client.post('/cohorts/'+self.other+'/api/v1/roster',json={'student_ids':['00001']}).status_code,200)
        self.client.post('/logout');self.assertEqual(self.client.get('/api/v1/settings').status_code,403)

    def test_shared_bundle_restore_conflict_and_purge(self):
        self.process(['00001','00002'])
        batch=self.service.delivery(['00001','00002']);self.service.confirm_delivery(batch['id'])
        preview=recycle.impact(self.service,['00001']);self.assertTrue(preview['expanded'])
        with self.assertRaises(ValueError):recycle.move(self.service,['00001'],30,preview['revision'])
        tid=recycle.move(self.service,preview['student_ids'],30,preview['revision'])['id']
        self.assertEqual(len(self.store.data['students']),1)
        self.assertEqual(self.store.data['deliveries'],{})
        self.store.roster(['00001'])
        with self.assertRaises(ValueError):recycle.restore(self.service,tid)
        fresh=recycle.impact(self.service,['00001']);new=recycle.move(self.service,['00001'],None,fresh['revision'])['id']
        recycle.purge(self.service,new);recycle.restore(self.service,tid)
        self.assertEqual(Store.status(self.store.data['students']['00001']),'delivered')
        self.assertEqual(self.store.data['jobs'][next(iter(self.store.data['jobs']))]['status'],'completed')
        p=recycle.impact(self.service,[],True);tid=recycle.move(self.service,[],30,p['revision'],True)['id']
        self.assertTrue(self.store.file('deliveries/'+batch['id']+'/photos.zip').exists())
        recycle.purge(self.service,tid)
        self.assertFalse((self.store.root/'deliveries'/batch['id']/'photos.zip').exists())
        self.assertFalse(list((self.store.root/'artifacts').rglob('*.jpg')))

    def test_expiry_disable_and_interrupted_purge(self):
        self.store.upload('00001',self.photo);self.store.upload('00002',self.photo)
        p=recycle.impact(self.service,['00001']);tid=recycle.move(self.service,['00001'],30,p['revision'])['id']
        expiry=self.store.data['trash'][tid]['expires_at']
        future=datetime.now(timezone.utc)+timedelta(days=31)
        recycle.cleanup(self.service,False,future);self.assertIn(tid,self.store.data['trash'])
        self.assertEqual(expiry,self.store.data['trash'][tid]['expires_at'])
        recycle.cleanup(self.service,True,future);self.assertNotIn(tid,self.store.data['trash'])
        self.assertTrue(self.store.file(self.store.data['students']['00002']['sources'][0]['file']).exists())
        p=recycle.impact(self.service,['00002']);tid=recycle.move(self.service,['00002'],30,p['revision'])['id']
        with patch.object(Path,'unlink',side_effect=OSError('controlled interruption')):
            with self.assertRaises(OSError):recycle.purge(self.service,tid)
        self.assertEqual(self.store.data['trash'][tid]['status'],'purging')
        with self.assertRaises(ValueError):recycle.restore(self.service,tid)
        recycle.cleanup(self.service,False);self.assertNotIn(tid,self.store.data['trash'])

    def test_tasks_and_retries(self):
        self.store.upload('00001',self.photo)
        original=self.service.execute
        def slow(*args):time.sleep(.2);return original(*args)
        with patch.object(self.service,'execute',slow):
            first=self.service.start(['00001'],{},request_id='one')
            again=self.service.start(['00001'],{},request_id='one');self.assertEqual(first['id'],again['id'])
            with self.assertRaises(ValueError):recycle.move(self.service,['00001'],30,self.store.data['revision'])
            with self.assertRaises(ValueError):self.store.upload('00001',self.photo)
            self.service.thread.join(10)
        self.assertEqual(self.service.start(['00001'],{})['status'],'skipped')
        self.assertEqual(len(self.store.data['students']['00001']['results']),1)

    def test_old_workspace_refused(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'workspace.json';p.write_text('{"schema_version":2}')
            with self.assertRaises(ValueError):create_system_app(root,'synthetic-admin-secret-123456')
            self.assertEqual(p.read_text(),'{"schema_version":2}')

    def test_http_fixed_cohort_and_saved_config(self):
        r=self.client.put('/api/v1/config',json={'crop_enabled':True,'crop_width':123,'crop_height':156})
        self.assertEqual(r.status_code,200,r.text)
        self.client.post('/cohorts/'+self.other+'/api/v1/roster',json={'student_ids':['00001']})
        self.assertEqual(self.client.post(self.base+'students/00001/photos',files={'photo':('test.jpg',self.photo,'image/jpeg')}).status_code,200)
        self.assertEqual(self.system.child(self.other).state.store.data['students']['00001']['sources'],[])
        r=self.post(self.base+'processing-jobs',{'student_ids':['00001'],'config':{'crop_enabled':True,'crop_width':99},'dry_run':True})
        job=self.post(self.base+'processing-jobs',{'student_ids':['00001'],'config':r['config'],'dry_run':False,'expected_revision':r['revision'],'request_id':'http-one'})
        retry=self.post(self.base+'processing-jobs',{'student_ids':['00001'],'config':r['config'],'dry_run':False,'expected_revision':r['revision'],'request_id':'http-one'})
        self.assertEqual(job['id'],retry['id']);self.service.thread.join(10)
        self.assertEqual(self.client.get('/api/v1/config').json()['saved']['crop_width'],123)
        self.assertEqual(self.store.data['students']['00001']['results'][0]['config']['crop_width'],99)
        self.assertTrue(any(e.get('actor','').startswith('session-') for e in self.store.data['events']))
        self.assertNotEqual(self.client.post('/api/v1/shutdown').status_code,200)

    def test_trash_blocks_cohort_delete_and_retention_changes(self):
        p=recycle.impact(self.service,[],True);tid=recycle.move(self.service,[],30,p['revision'],True)['id']
        expiry=self.store.data['trash'][tid]['expires_at']
        d=self.client.get('/api/v1/settings').json();d['retention_days']=1
        self.assertEqual(self.client.put('/api/v1/settings',json=d).status_code,200)
        self.assertEqual(expiry,self.store.data['trash'][tid]['expires_at'])
        d=self.client.get('/api/v1/settings').json();d['cohorts']=[c for c in d['cohorts'] if c['id']!=self.cid];d['confirmed_deletions']=[self.cid]
        r=self.client.put('/api/v1/settings',json=d);self.assertEqual(r.status_code,409);self.assertIn('trash',r.text)
        recycle.purge(self.service,tid)
        d['revision']=self.system.data['revision']
        self.assertEqual(self.client.put('/api/v1/settings',json=d).status_code,200)

    def test_purge_retry_rechecks_new_references_and_preview_ownership(self):
        self.store.upload('00001',self.photo)
        response=self.client.post(self.base+'students/00001/preview',json={'crop_enabled':True,'crop_width':80,'crop_height':100})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(self.store.data['students']['00001']['previews'])
        p=recycle.impact(self.service,['00001']);tid=recycle.move(self.service,['00001'],30,p['revision'])['id']
        with patch.object(Path,'unlink',side_effect=OSError('controlled interruption')):
            with self.assertRaises(OSError):recycle.purge(self.service,tid)
        self.store.upload('00002',self.photo)
        kept=self.store.data['students']['00002']['sources'][0]['file']
        preview_file=response.json()['artifact']['file']
        recycle.purge(self.service,tid)
        self.assertTrue(self.store.file(kept).exists())
        self.assertFalse((self.store.root/preview_file).exists())

if __name__=='__main__':unittest.main(verbosity=2)
