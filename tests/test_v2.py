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
        with self.assertRaises(ValueError):
            self.store.upload('001',photo('red'))
        self.service.replacement('001','更换照片')
        self.store.upload('001',photo('red'))
        self.assertIsNotNone(self.store.snapshot()['students']['001']['delivered'])
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

    def test_history_addresses_persist_and_validate(self):
        route='/api/v1/engines/hivision/urls'
        self.assertEqual(self.client.get(route).json(), [])
        for url in ['http://127.0.0.1:8080/', 'http://127.0.0.1:8080']:
            self.assertEqual(self.client.post(route,json={'url':url}).status_code,200)
        self.assertEqual(Store(self.temp.name).snapshot()['hivision_urls'],['http://127.0.0.1:8080'])
        for url in ['', 'file:///tmp', 'https://user:password@example.com', 'http://host:wrong']:
            self.assertEqual(self.client.post(route,json={'url':url}).status_code,409)

    def test_history_can_register_unknown_ids_atomically(self):
        route='/api/v1/historical-deliveries'
        body={'student_ids':['00003'],'reason':'已发送','confirmed_sent':False}
        self.assertEqual(self.client.post(route,json=body).status_code,409)
        self.assertNotIn('00003',self.store.snapshot()['students'])
        body['confirmed_sent']=True
        self.assertEqual(self.client.post(route,json=body).status_code,200)
        student=self.store.snapshot()['students']['00003']
        self.assertEqual(Store.status(student),'delivered')
        self.assertIsNone(student['approved'])
        before=self.store.snapshot()
        body['student_ids']=['00004','bad/id']
        self.assertEqual(self.client.post(route,json=body).status_code,409)
        self.assertEqual(before,self.store.snapshot())
        self.store.roster(['ABC'])
        body['student_ids']=['00004','abc']
        self.assertEqual(self.client.post(route,json=body).status_code,409)
        self.assertNotIn('00004',self.store.snapshot()['students'])

    def test_excel_check_redetects_but_import_honors_selected_columns(self):
        import zipfile
        from xml.sax.saxutils import escape
        from xlsx_photo_core import column_label
        def workbook(headers):
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z:
                z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="xl/workbook.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/></Relationships>')
                z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
                rows=''.join('<row r="'+str(n)+'">'+''.join('<c r="'+column_label(i)+str(n)+'" t="inlineStr"><is><t>'+escape(v)+'</t></is></c>' for i,v in enumerate(values))+'</row>' for n,values in [(1,headers),(2,['0001']*len(headers))])
                z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+rows+'</sheetData></worksheet>')
            return stream.getvalue()
        route='/api/v1/imports/xlsx'
        for headers,expected in [(['姓名','学号','照片'],('B','C')),(['学号','照片'],('A','B')),(['其他','未知'],('A','B'))]:
            result=self.client.post(route,data={'id_column':'Z','image_column':'Y','inspect_only':'true'},files={'file':('sample.xlsx',workbook(headers))})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual((result.json()['id_column'],result.json()['image_column']),expected)
        with patch.object(self.service,'start_import',return_value={'ok':True}) as start:
            result=self.client.post(route,data={'id_column':'A','image_column':'B','inspect_only':'false'},files={'file':('sample.xlsx',workbook(['姓名','学号','照片']))})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(start.call_args.args[3:5],(0,1))

    def test_zip_import_checks_names_preserves_original_and_is_repeatable(self):
        import zipfile
        def archive(names):
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z:
                for name in names:
                    z.writestr(name,photo())
            return stream.getvalue()
        route='/api/v1/imports/zip'
        raw=archive(['照片/00003-测试.png'])
        checked=self.client.post(route,files={'file':('custom.zip',raw)})
        self.assertEqual(checked.json()['count'],1)
        self.assertNotIn('00003',self.store.snapshot()['students'])
        for _ in range(2):
            response=self.client.post(route,data={'inspect_only':'false'},files={'file':('other.zip',raw)})
            self.assertEqual(response.status_code,200,response.text)
            self.service.thread.join(10)
            self.assertFalse(self.service.thread.is_alive())
            self.assertFalse(self.store.snapshot()['jobs'][response.json()['id']]['errors'])
        sources=self.store.snapshot()['students']['00003']['sources']
        self.assertEqual(len(sources),1)
        self.assertEqual(self.store.file(sources[0]['file']).read_bytes(),photo())
        for names in [['../00004-测试.png'],['00004-甲.png','00004-乙.jpg'],['invalid.png']]:
            response=self.client.post(route,files={'file':('bad.zip',archive(names))})
            self.assertEqual(response.status_code,409,response.text)
        self.assertNotIn('00004',self.store.snapshot()['students'])


if __name__=='__main__':
    unittest.main()
