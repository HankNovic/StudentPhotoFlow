"""Real HTTP against a local simulated Hivision. No production service/photos."""
import base64
import io
import json
import shutil
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
from photo_pipeline import PipelineOptions
from v2.system import create_system_app
from v2.service import Service
from v2.store import Store


def until(fn, timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if fn():return
        time.sleep(.03)
    raise AssertionError('condition timed out')


class TaskTimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.lock=threading.Lock();self.active=0;self.peak=0;self.calls=[];self.delay=.3;self.modes=[]
        case=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                raw=self.rfile.read(int(self.headers['Content-Length']))
                part=BytesParser(policy=default).parsebytes(('Content-Type: '+self.headers['Content-Type']+'\r\n\r\n').encode()+raw)
                photo=next(p.get_payload(decode=True) for p in part.iter_parts() if p.get_param('name',header='content-disposition')=='input_image')
                with case.lock:
                    i=len(case.calls);case.calls.append({'start':time.monotonic()});case.active+=1;case.peak=max(case.peak,case.active)
                    mode=case.modes[i] if i<len(case.modes) else 'ok';delay=case.delay
                try:
                    time.sleep(5.5 if mode=='timeout' else delay)
                    data=b'{invalid json' if mode=='parse' else json.dumps({'status':True,'image_base64_standard':base64.b64encode(photo).decode()}).encode()
                    self.send_response(503 if mode=='error' else 200);self.send_header('Content-Length',str(len(data)));self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
                finally:
                    with case.lock:case.active-=1;case.calls[i]['end']=time.monotonic()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.app=create_system_app(Path(self.tmp.name)/'data','independent-test-secret')
        self.client=TestClient(self.app);self.client.post('/session',json={'token':'independent-test-secret'})
        settings=self.client.get('/api/v1/settings').json();settings['cohorts']=[{'year':'2035'},{'year':'2036'}]
        rows=self.client.put('/api/v1/settings',json=settings).json()['cohorts']
        self.services=[self.app.state.system.child(c['id']).state.service for c in rows]
        self.config=asdict(PipelineOptions(background_mode='hivision',hivision_url=self.url,hivision_timeout=5,auto_orient=False))
        self.configure(1)
        for service in self.services:
            service.store.roster(['0001','0002','0003','0004'])
            for n,sid in enumerate(service.store.data['students']):
                b=io.BytesIO();Image.new('RGB',(64,96),(30+n*40,80,190)).save(b,'PNG');service.store.upload(sid,b.getvalue())

    def tearDown(self):
        for service in self.services:
            if service.thread:service.thread.join(15)
        self.server.shutdown();self.server.server_close();self.tmp.cleanup()

    def configure(self,n):
        self.config['hivision_concurrency']=n
        r=self.client.put('/api/v1/config',json=self.config);self.assertEqual(r.status_code,200,r.text)

    def job(self,s,jid):return s.store.snapshot()['jobs'][jid]
    def done(self,s,jid):
        until(lambda:self.job(s,jid)['status'] in {'completed','cancelled','paused','interrupted'})
        s.thread.join(10)
        return self.job(s,jid)

    def test_serial_parallel_and_identity(self):
        s=self.services[0];jid=s.start(['0001','0002'],self.config)['id'];self.done(s,jid);self.assertEqual(self.peak,1)
        self.configure(3);jid=s.start(['0003','0004'],self.config)['id'];j=self.done(s,jid)
        self.assertEqual(self.peak,2);self.assertEqual(len(j['completed']),2)
        for sid in ['0003','0004']:
            st=s.store.snapshot()['students'][sid];result=st['results'][-1]
            self.assertEqual(result['source_id'],st['sources'][-1]['id'])
            with Image.open(s.store.file(result['artifact']['file'])) as im:self.assertAlmostEqual(im.getpixel((10,10))[0],110 if sid=='0003' else 150,delta=5)
        self.assertFalse(s.plan(['0003'],dict(self.config,hivision_concurrency=1))['items'][0]['execute'])

    def test_shared_across_tasks_and_preview_and_dynamic_lowering(self):
        self.configure(3);self.delay=1
        a,b=self.services
        ja=a.start(['0001','0002','0003','0004'],self.config)['id']
        until(lambda:self.active==3);self.configure(1);changed=time.monotonic()
        jb=b.start(['0001'],self.config)['id']
        self.done(a,ja);self.done(b,jb)
        self.assertEqual(self.peak,3)
        for call in self.calls:
            if call['start']>changed:self.assertLessEqual(sum(x['start']<=call['start']<x.get('end',float('inf')) for x in self.calls),1)
        self.configure(2);self.peak=0
        ja=a.start(['0001'],dict(self.config,background_color='#FFFFFF'))['id']
        source,_=Store.current(b.store.snapshot()['students']['0002'])
        preview=threading.Thread(target=b.execute,args=(source,self.config));preview.start()
        self.done(a,ja);preview.join(10);self.assertEqual(self.peak,2)

    def test_pause_resume_counts_and_clock(self):
        self.configure(2);self.delay=.9;s=self.services[0]
        jid=s.start(['0001','0002','0003','0004'],self.config)['id'];until(lambda:self.active==2)
        s.control(jid,'pause');self.assertEqual(self.job(s,jid)['status'],'pausing')
        j=self.done(s,jid);self.assertEqual(len(self.calls),2);self.assertEqual(j['status'],'paused');self.assertIsNone(j['timing']['ended_at'])
        active=j['timing']['run_seconds'];time.sleep(.4);self.assertEqual(self.job(s,jid)['timing']['run_seconds'],active)
        s.control(jid,'resume');j=self.done(s,jid);self.assertEqual(len(self.calls),4)
        self.assertGreater(j['timing']['run_seconds'],1.7);self.assertLess(j['timing']['run_seconds'],3.4)
        from datetime import datetime
        elapsed=(datetime.fromisoformat(j['timing']['ended_at'])-datetime.fromisoformat(j['timing']['started_at'])).total_seconds()
        self.assertGreater(elapsed-j['timing']['run_seconds'],.35)
        self.assertEqual(Service(Store(s.store.root)).store.snapshot()['jobs'][jid]['timing'],j['timing'])

    def test_manual_drain_is_terminal(self):
        self.configure(2);self.delay=.8;s=self.services[0]
        jid=s.start(['0001','0002','0003','0004'],self.config)['id'];until(lambda:self.active==2)
        s.control(jid,'cancel');s.control(jid,'cancel');self.assertIsNone(self.job(s,jid)['timing']['ended_at'])
        j=self.done(s,jid);self.assertEqual(j['status'],'cancelled');self.assertEqual(len(self.calls),2)
        self.assertEqual(s.job_view(j)['counts']['remaining'],2)
        with self.assertRaises(ValueError):s.control(jid,'resume')
        recovered=Service(Store(s.store.root));self.assertEqual(recovered.store.data['jobs'][jid]['timing'],j['timing'])
        with self.assertRaises(ValueError):recovered.control(jid,'resume')

    def test_error_timeout_parse_release_slots(self):
        self.configure(2);self.modes=['error','parse','timeout','ok'];s=self.services[0]
        jid=s.start(['0001','0002','0003','0004'],self.config)['id'];j=self.done(s,jid)
        self.assertEqual(len(j['completed']),4);self.assertEqual(len(j['errors']),3);self.assertEqual(s.job_view(j)['counts']['success'],1)
        self.assertEqual(sum(s.requests.active.values()),0);self.assertLess(j['timing']['run_seconds'],8)

    def test_crash_heartbeat_and_history(self):
        self.configure(2);self.delay=2;s=self.services[0]
        jid=s.start(['0001','0002','0003'],self.config)['id'];until(lambda:self.active==2);time.sleep(1.1)
        target=Path(self.tmp.name)/'crash'
        with s.store.lock:shutil.copytree(s.store.root,target)
        before=Store(target).snapshot()['jobs'][jid]['timing'];time.sleep(.4)
        recovered=Service(Store(target));j=recovered.store.snapshot()['jobs'][jid]
        self.assertEqual(j['timing']['run_seconds'],before['run_seconds']);self.assertFalse(j['timing']['running']);self.assertTrue(j['timing']['lower_bound']);self.assertEqual(len(j['uncertain']),2)
        with self.assertRaises(ValueError):recovered.control(jid,'resume')
        self.done(s,jid)
        old=dict(self.job(s,jid));old.pop('timing');self.assertNotIn('timing',s.job_view(old))

    def test_config_legacy_and_validation(self):
        for n in [0,17,1.2,True,'2']:
            self.assertEqual(self.client.put('/api/v1/config',json=dict(self.config,hivision_concurrency=n)).status_code,409)
        legacy=dict(self.config);legacy.pop('hivision_concurrency')
        self.assertEqual(self.client.put('/api/v1/config',json=legacy).json()['hivision_concurrency'],1)
        self.assertEqual(self.client.get('/api/v1/config').json()['saved']['hivision_concurrency'],1)


if __name__=='__main__':unittest.main(verbosity=2)
