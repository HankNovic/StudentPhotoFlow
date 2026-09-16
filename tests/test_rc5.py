import tempfile,unittest,io,threading,json
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from v2.system import create_system_app
from v2.service import Service
from v2.store import Store

class FeedbackTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.app=create_system_app(self.tmp.name,'test-secret-for-rc5-only');self.c=TestClient(self.app);self.c.post('/session',json={'token':'test-secret-for-rc5-only'})
  d=self.c.get('/api/v1/settings').json();d['cohorts']=[{'year':'2027'}];r=self.c.put('/api/v1/settings',json=d);self.cid=r.json()['cohorts'][0]['id'];self.s=self.app.state.system.child(self.cid).state.service;self.store=self.s.store
  self.base='/cohorts/'+self.cid+'/api/v1/';self.store.roster(['00001','00002','00003']);im=io.BytesIO();Image.new('RGB',(64,96),'blue').save(im,'PNG')
  for sid in self.store.data['students']:self.store.upload(sid,im.getvalue())
 def tearDown(self):
  if self.s.thread:self.s.thread.join(15)
  self.tmp.cleanup()
 def slow(self,action):
  entered=threading.Event();release=threading.Event();actual=self.s.execute;calls=[]
  def execute(*a):calls.append(a);entered.set();release.wait(5);return actual(*a)
  with patch.object(self.s,'execute',execute):
   j=self.s.start(['00001','00002','00003'],{});self.assertTrue(entered.wait(5));self.s.control(j['id'],action);self.s.control(j['id'],action);release.set();self.s.thread.join(10)
  return j['id'],calls
 def test_pause_and_manual_end(self):
  jid,calls=self.slow('pause');j=self.store.data['jobs'][jid];self.assertEqual(j['status'],'paused');self.assertEqual(j['completed'],['00001']);self.assertEqual(len(calls),1)
  self.s.control(jid,'resume');self.s.thread.join(10);self.assertEqual(self.store.data['jobs'][jid]['status'],'completed');self.assertEqual(len(self.store.data['students']['00001']['results']),1)
  with self.assertRaises(ValueError):self.s.control(jid,'resume')
 def test_manual_end_survives_restart(self):
  jid,calls=self.slow('cancel');self.assertEqual(len(calls),1);self.assertEqual(self.store.data['jobs'][jid]['status'],'cancelled')
  new=Service(Store(self.store.root));self.assertEqual(new.store.data['jobs'][jid]['end_reason'],'manual')
  with self.assertRaises(ValueError):new.control(jid,'resume')
  counts=new.job_view(new.store.data['jobs'][jid])['counts'];self.assertEqual(counts['success'],1);self.assertEqual(counts['remaining'],2)
 def test_restart_uncertain_requires_reconcile(self):
  jid,calls=self.slow('pause')
  self.store.change('simulate stopped process',lambda d:d['jobs'][jid].update(status='running',current='00002'))
  new=Service(Store(self.store.root));self.assertIn('00002',new.store.data['jobs'][jid]['uncertain'])
  with self.assertRaises(ValueError):new.control(jid,'resume')
  self.assertFalse(new.plan(['00002'],{})['items'][0]['execute'])
  new.reconcile(jid,'00002','skip');new.control(jid,'resume');new.thread.join(10)
  j=new.store.data['jobs'][jid];self.assertEqual(j['status'],'completed');self.assertEqual(len(new.store.data['students']['00001']['results']),1);self.assertEqual(len(new.store.data['students']['00002']['results']),0)
 def test_failure_counts_redaction_and_phase(self):
  with patch.object(self.s,'execute',side_effect=ValueError('HTTP 503: server unavailable token=secret-value Authorization: Bearer abcdef')):
   j=self.s.start(['00001'],{});self.s.thread.join(10)
  result=self.c.get(self.base+'jobs').json()[0];self.assertEqual(result['counts']['failed'],1);self.assertEqual(result['counts']['success'],0)
  err=result['errors']['00001'];self.assertIn('照片处理',err);self.assertIn('503',err);self.assertNotIn('secret-value',err);self.assertNotIn('abcdef',err)
 def test_returned_rejection_counts(self):
  with patch.object(self.s,'execute',return_value={'status':'rejected','artifact':None,'stages':[],'reasons':['face not detected']}):
   j=self.s.start(['00001'],{});self.s.thread.join(10)
  r=self.s.job_view(self.store.data['jobs'][j['id']]);self.assertEqual(r['counts']['failed'],1);self.assertIn('face not detected',r['errors']['00001'])
 def test_cohort_delete_and_duplicate_roster(self):
  r=self.c.post(self.base+'roster',json={'student_ids':['00001','00001']});self.assertEqual(r.status_code,200)
  self.assertEqual(self.c.request('DELETE','/api/v1/cohorts/'+self.cid,json={'confirmed':True}).status_code,409)
  d=self.c.get('/api/v1/settings').json();d['cohorts'].append({'year':'2030'});r=self.c.put('/api/v1/settings',json=d);cid=next(x['id'] for x in r.json()['cohorts'] if x['year']=='2030')
  self.assertEqual(self.c.request('DELETE','/api/v1/cohorts/'+cid,json={'confirmed':True}).status_code,200)
  self.assertNotIn(cid,[x['id'] for x in self.c.get('/api/v1/settings').json()['cohorts']])
 def test_cache_identity_and_rc4_backup(self):
  health=self.c.get('/healthz');self.assertEqual(health.headers['cache-control'],'no-store');self.assertIn('build_id',health.json())
  self.assertEqual(self.c.get('/').headers['cache-control'],'no-store');self.assertTrue((self.store.root/'backups'/'before-task-semantics-2.json').exists())

if __name__=='__main__':unittest.main(verbosity=2)
