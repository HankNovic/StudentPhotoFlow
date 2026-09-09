import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient
from v2.api import create_app
from v2.store import Store, atomic


def photo(color='blue'):
    stream=io.BytesIO()
    Image.new('RGB',(590,826),color).save(stream,'JPEG')
    return stream.getvalue()


class V2Test(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(self.temp.name,'test-token')
        self.client=TestClient(self.app,headers={'Authorization':'Bearer test-token'})
        self.store=self.app.state.store
        self.service=self.app.state.service
        self.store.roster(['001','002'])

    def tearDown(self):
        if self.service.thread:
            self.service.thread.join(10)
        self.temp.cleanup()

    def process(self,sid='001'):
        self.store.upload(sid,photo())
        job=self.service.start([sid],{})
        self.service.thread.join(10)
        self.assertFalse(self.service.thread.is_alive())
        s=self.store.snapshot()['students'][sid]
        self.assertEqual(Store.status(s),'review')
        return s

    def test_review_delivery_update_and_replacement(self):
        s=self.process()
        self.store.review('001',s['results'][-1]['id'],'approved',len(s['history']))
        batch=self.service.delivery(['001'])
        self.assertEqual(batch['status'],'prepared')
        with self.assertRaises(ValueError):
            self.service.delivery(['001'])
        self.service.confirm_delivery(batch['id'])
        self.assertFalse(self.service.plan(['001'],{},True)['items'][0]['execute'])
        self.store.upload('001',photo('red'))
        self.assertEqual(Store.status(self.store.snapshot()['students']['001']),'delivered_updated')
        self.service.replacement('001','更换照片')
        self.assertTrue(self.service.plan(['001'],{})['items'][0]['execute'])

    def test_duplicate_keeps_review_and_stale_review_rejected(self):
        s=self.process()
        self.store.review('001',s['results'][-1]['id'],'approved',len(s['history']))
        self.assertFalse(self.store.upload('001',photo())['changed'])
        self.assertEqual(Store.status(self.store.snapshot()['students']['001']),'approved')
        with self.assertRaises(ValueError):
            self.store.review('001',s['results'][-1]['id'],'rejected',len(s['history']))

    def test_api_auth_and_exact_crop_preview(self):
        unauth=TestClient(self.app)
        self.assertEqual(unauth.get('/api/v1/students').status_code,403)
        self.store.upload('001',photo())
        r=self.client.post('/api/v1/students/001/preview',json={'crop_enabled':True,'crop_width':295,'crop_height':413})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual((r.json()['artifact']['width'],r.json()['artifact']['height']),(295,413))
        self.assertEqual(len(self.store.snapshot()['students']['001']['results']),0)
        self.assertEqual(self.client.get('/openapi.json').status_code,200)

    def test_plan_revision_and_migration(self):
        self.store.upload('001',photo())
        plan=self.service.plan(['001'],{})
        self.store.roster(['003'])
        with self.assertRaises(ValueError):
            self.service.start(['001'],{},expected_revision=plan['revision'])
        with tempfile.TemporaryDirectory() as legacy, tempfile.TemporaryDirectory() as target:
            root=Path(legacy)
            atomic(root/'original.jpg',photo())
            atomic(root/'export_state.json',{'records':{'001':{'original_file':'original.jpg'}}})
            from v2.migration import migrate_legacy
            dest=Store(target)
            report=migrate_legacy(dest,root,True)
            self.assertEqual(report['students'],1)
            self.assertTrue((root/'original.jpg').exists())
            self.assertEqual(Store.status(dest.snapshot()['students']['001']),'pending')

    def test_archive_files_are_student_named(self):
        s=self.process()
        self.store.review('001',s['results'][-1]['id'],'approved',len(s['history']))
        batch=self.service.delivery(['001'])
        import zipfile
        with zipfile.ZipFile(self.store.root/'deliveries'/batch['id']/'photos.zip') as z:
            self.assertIn('001.jpg',z.namelist())

    def test_cancel_resume_checkpoints_and_no_duplicate(self):
        import threading
        self.store.upload('001',photo())
        self.store.upload('002',photo())
        entered=threading.Event()
        release=threading.Event()
        actual=self.service.execute
        def slow(source,config):
            entered.set()
            release.wait(5)
            return actual(source,config)
        with patch.object(self.service,'execute',side_effect=slow):
            job=self.service.start(['001','002'],{})
            self.assertTrue(entered.wait(5))
            self.service.control(job['id'],'cancel')
            release.set()
            self.service.thread.join(10)
        saved=self.store.snapshot()['jobs'][job['id']]
        self.assertEqual(saved['status'],'cancelled')
        self.assertEqual(saved['completed'],['001'])
        self.service.control(job['id'],'resume')
        self.service.thread.join(10)
        self.assertEqual(len(self.store.snapshot()['students']['001']['results']),1)
        self.assertEqual(self.store.snapshot()['jobs'][job['id']]['status'],'completed')

    def test_historical_delivery_does_not_approve_and_is_idempotent(self):
        self.store.upload('001',photo())
        self.service.historical_delivery(['001'],'实际发送清单')
        self.service.historical_delivery(['001'],'重复清单')
        s=self.store.snapshot()['students']['001']
        self.assertIsNone(s['approved'])
        self.assertEqual(len(self.store.snapshot()['deliveries']),1)

    def test_configuration_rejects_string_booleans(self):
        with self.assertRaises(ValueError):
            self.service.options({'hivision_face_align':'false'})

    def test_failed_new_version_requires_review_again(self):
        s=self.process()
        self.store.review('001',s['results'][-1]['id'],'approved',len(s['history']))
        with patch.object(self.service,'execute',side_effect=ValueError('test failure')):
            self.service.start(['001'],{},True)
            self.service.thread.join(10)
        self.assertIsNone(self.store.snapshot()['students']['001']['approved'])
        with self.assertRaises(ValueError):
            self.service.delivery(['001'])

    def test_historical_delivery_cannot_override_prepared_package(self):
        s=self.process()
        self.store.review('001',s['results'][-1]['id'],'approved',len(s['history']))
        self.service.delivery(['001'])
        with self.assertRaises(ValueError):
            self.service.historical_delivery(['001'],'test')

    def test_invalid_xlsx_is_rejected(self):
        response=self.client.post('/api/v1/imports/xlsx',files={'file':('bad.xlsx',b'not a zip')})
        self.assertEqual(response.status_code,409)


if __name__=='__main__':
    unittest.main()
