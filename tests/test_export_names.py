import base64
import io
import json
import tempfile
import unittest
import zipfile
import sys
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from fastapi.testclient import TestClient

from xlsx_photo_core import column_label, inspect_selection
from v2.export_profiles import preview, render, validate_profile
from v2.service import Service
from v2.store import Store
from v2.api import create_app


def image_bytes(color=(50, 100, 150)):
    out = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(out, format='JPEG')
    return out.getvalue()


def workbook(headers, values, *extra):
    stream = io.BytesIO()
    rows = [headers, values, *extra]
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="xl/workbook.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/></Relationships>')
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
        xml = ''.join(
            '<row r="%d">%s</row>' % (number, ''.join(
                '<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % (column_label(index), number, escape(str(value)))
                for index, value in enumerate(row)
            )) for number, row in enumerate(rows, 1)
        )
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>' % xml)
    return stream.getvalue()


class ExportNameTests(unittest.TestCase):
    def test_old_workspace_and_name_import_rules(self):
        root = Path(tempfile.mkdtemp())
        old = {'schema_version': 2, 'revision': 0, 'active_cohort': '2026级', 'students': {'00123': {'id': '00123', 'cohort': '2026级', 'sources': [], 'results': [], 'approved': None, 'delivered': None, 'replacement': False, 'history': []}}, 'deliveries': {}, 'jobs': {}, 'config': {}, 'hivision_urls': [], 'events': [], 'cohort_records': {'2026级': {'id': 'c', 'name': '2026级', 'archived': False}}, 'recycle_bin': []}
        (root / 'workspace.json').write_text(json.dumps(old), encoding='utf-8')
        before = (root / 'workspace.json').read_bytes()
        store = Store(root)
        self.assertIsNone(store.snapshot()['students']['00123']['name'])
        self.assertEqual(store.snapshot()['schema_version'], 2)
        self.assertEqual(store.path.read_bytes(), before)

    def test_excel_name_column_and_conflict_report(self):
        root = Path(tempfile.mkdtemp())
        data = base64.b64encode(image_bytes()).decode()
        path = root / 'names.xlsx'
        path.write_bytes(workbook(['学号', '照片', '姓名'], ['00123', 'data:image/jpeg;base64,' + data, '张三']))
        report = inspect_selection(path, 'Sheet1', 1, 0, 1, 2)
        self.assertEqual(report.rows[0].name, '张三')
        self.assertEqual(report.summary['name_empty'], 0)
        path.write_bytes(workbook(['学号', '照片', '姓名'], ['00123', 'data:image/jpeg;base64,' + data, '']))
        report = inspect_selection(path, 'Sheet1', 1, 0, 1, 2)
        self.assertEqual(report.summary['name_empty_rows'], [2])
        self.assertEqual(inspect_selection(path, 'Sheet1', 1, 0, 1).rows[0].name, None)

    def test_excel_api_reports_optional_name_column(self):
        root = Path(tempfile.mkdtemp())
        raw = workbook(['学号', '照片', '姓名'], ['00123', '', '张三'])
        client = TestClient(create_app(root, 'test-name-token'), headers={'Authorization': 'Bearer test-name-token'})
        response = client.post('/api/v1/imports/xlsx', files={'file': ('names.xlsx', raw)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['name_column'], 'C')
        self.assertEqual(response.json()['summary']['name_empty'], 0)

    def test_excel_import_persists_name_and_empty_does_not_overwrite(self):
        root = Path(tempfile.mkdtemp())
        store = Store(root)
        raw = workbook(['学号', '照片', '姓名'], ['00123', 'data:image/jpeg;base64,' + base64.b64encode(image_bytes()).decode(), '张三'])
        path = root / 'input.xlsx'; path.write_bytes(raw)
        report = inspect_selection(path, 'Sheet1', 1, 0, 1, 2)
        service = Service(store)
        with patch('xlsx_photo_core._fetch_source', return_value=image_bytes()):
            service.start_import(raw, 'Sheet1', 1, 0, 1, report.rows, 2)
            service.thread.join(10)
        self.assertEqual(store.snapshot()['students']['00123']['name'], '张三')
        raw2 = workbook(['学号', '照片', '姓名'], ['00123', 'data:image/jpeg;base64,' + base64.b64encode(image_bytes((70, 80, 90))).decode(), ''])
        path.write_bytes(raw2)
        report = inspect_selection(path, 'Sheet1', 1, 0, 1, 2)
        with patch('xlsx_photo_core._fetch_source', return_value=image_bytes((70, 80, 90))):
            service.start_import(raw2, 'Sheet1', 1, 0, 1, report.rows, 2)
            service.thread.join(10)
        self.assertEqual(store.snapshot()['students']['00123']['name'], '张三')

    def test_name_template_real_single_and_batch_zip_and_manifest(self):
        root = Path(tempfile.mkdtemp())
        store = Store(root)
        store.roster(['00123', '00456', '00789'])
        image = image_bytes()
        for sid, name in [('00123', '张三'), ('00456', '李四'), ('00789', '王五')]:
            artifact = store.artifact(image)
            store.change('测试成片', lambda d, sid=sid, name=name, artifact=artifact: (
                d['students'][sid].update(name=name, sources=[dict(artifact, at='now')], results=[dict(id='result-'+sid, source_id=artifact['id'], artifact=artifact, status='success')], approved='result-'+sid)
            ))
        profile = validate_profile('学号姓名', '{student_id}-{name}{ext}')
        profile.update(id='name-profile', revision=1, status='active', is_default=False, created_at='', updated_at='', history=[])
        store.change('测试格式', lambda d: d['export_profiles'].append(profile))
        service = Service(store)
        single = service.delivery(['00123'], 'name-profile')
        batch = service.delivery(['00456', '00789'], 'name-profile')
        for expected, batch_data in [('00123-张三.jpg', single), ('00456-李四.jpg', batch)]:
            archive = root / 'deliveries' / batch_data['id'] / 'photos.zip'
            with zipfile.ZipFile(archive) as z:
                self.assertIn(expected, z.namelist())
                manifest = json.loads(z.read('manifest.json'))
                self.assertEqual(manifest['format_snapshot']['template'], '{student_id}-{name}{ext}')
                self.assertTrue(any(item.get('name') for item in manifest['items']))
        self.assertEqual(render(profile, {'student_id': '00123', 'name': '张/三', 'ext': '.jpg'})['file_name'], '00123-张_三.jpg')
        self.assertFalse(preview(profile, [{'student_id': '00123', 'name': None, 'ext': '.jpg'}])['ok'])
        self.assertFalse(preview(profile, [{'student_id': '00123', 'name': '  ', 'ext': '.jpg'}])['ok'])
        duplicate_profile = validate_profile('姓名', '{name}{ext}')
        self.assertFalse(preview(duplicate_profile, [{'student_id': '1', 'name': '同名', 'ext': '.jpg'}, {'student_id': '2', 'name': '同名', 'ext': '.jpg'}])['ok'])
        self.assertEqual(render(validate_profile('默认', '{student_id}{ext}'), {'student_id': '00123', 'ext': '.jpg'})['file_name'], '00123.jpg')


class NameContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = create_app(self.root, 'synthetic-name-test')
        self.client = TestClient(self.app, headers={'Authorization': 'Bearer synthetic-name-test'})
        self.addCleanup(self.client.close)
        self.store = self.app.state.store
        self.service = self.app.state.service
        self.photo = image_bytes()
        self.uri = 'data:image/jpeg;base64,' + base64.b64encode(self.photo).decode()

    def accepted(self, r):
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def excel(self, headers, rows, inspect=False, **fields):
        r = self.client.post('/api/v1/imports/xlsx',
            data=dict(inspect_only=str(inspect).lower(), **fields),
            files={'file': ('test.xlsx', workbook(headers, *rows))})
        if not inspect and r.status_code == 200:
            self.service.thread.join(10)
            self.assertFalse(self.service.thread.is_alive())
        return r

    def approve(self, *students):
        # Synthetic approved results with real JPEG bytes; no external processing.
        self.store.roster([sid for sid, name in students])
        art = self.store.artifact(self.photo)
        for sid, name in students:
            self.store.upload(sid, self.photo)
            self.store.change('合成成片', lambda d:d['students'][sid].update(name=name,
                results=[dict(id='r-'+sid, source_id=art['id'],artifact=art,status='success')]))
            self.store.review(sid,'r-'+sid,'approved',len(self.store.snapshot()['students'][sid]['history']))

    def profile(self, template='{student_id}-{name}{ext}'):
        return self.accepted(self.client.post('/api/v1/export-profiles',json={'name':template,'template':template}))['id']

    def preview(self, ids, pid):
        return self.accepted(self.client.post('/api/v1/deliveries/preview',json={'student_ids':ids,'profile_id':pid}))

    def deliver(self, ids, pid=None):
        return self.client.post('/api/v1/deliveries',json={'student_ids':ids,'profile_id':pid})

    def zip_check(self, batch, expected):
        r=self.client.get('/api/v1/deliveries/'+batch['id']+'/download')
        self.assertEqual(r.status_code,200)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            self.assertEqual(set(z.namelist()),set(expected)|{'manifest.json'})
            manifest=json.loads(z.read('manifest.json'))
            self.assertEqual(manifest,batch)
            self.assertTrue(manifest['format_snapshot']['template'])
            for entry in manifest['items']:
                self.assertIn(entry['file_name'],expected)
                self.assertIn('name',entry)
                self.assertEqual(entry['result_id'],'r-'+entry['student_id'])
                self.assertTrue(entry['source_id'])
            for name in expected:
                self.assertEqual(z.read(name),self.photo)
                with Image.open(io.BytesIO(z.read(name))) as image:
                    self.assertEqual(image.format,'JPEG');image.verify()
        return r.content

    def test_actual_excel_old_format_update_and_manual_roster_preserves(self):
        for headers,row,fields,expected in [
            (['学号','照片'],['00123',self.uri],{},None),
            (['学号','照片','姓名'],['00123',self.uri,' 张三 '],{'name_column':'C'},'张三'),
            (['学号','照片','姓名'],['00123',self.uri,'李四'],{'name_column':'C'},'李四'),
            (['学号','照片','姓名'],['00123',self.uri,''],{'name_column':'C'},'李四')]:
            job=self.accepted(self.excel(headers,[row],**fields))
            self.assertFalse(self.store.snapshot()['jobs'][job['id']]['errors'])
            self.assertEqual(Store(self.root).snapshot()['students']['00123']['name'],expected)
        self.accepted(self.client.post('/api/v1/roster',json={'student_ids':['00123']}))
        self.assertEqual(self.accepted(self.client.get('/api/v1/students/00123'))['name'],'李四')
        self.assertEqual(self.accepted(self.client.get('/api/v1/students'))[0]['name'],'李四')
        self.assertEqual(len(self.store.snapshot()['students']['00123']['sources']),1)

    def test_selected_mapping_checked_and_imported(self):
        fields=dict(id_column='B',image_column='C',name_column='A')
        headers=['甲','乙','丙'];rows=[['张三','00123',self.uri]]
        report=self.accepted(self.excel(headers,rows,inspect=True,detect_columns='false',**fields))
        self.assertEqual(tuple(report[k] for k in fields),tuple(fields.values()))
        self.accepted(self.excel(headers,rows,**fields))
        self.assertEqual(self.store.snapshot()['students']['00123']['name'],'张三')

    def test_conflict_rejects_entire_batch_without_side_effects(self):
        rows=[['00123',self.uri,'张三'],['00123',self.uri,'李四'],['00456',self.uri,'王五']]
        checked=self.accepted(self.excel(['学号','照片','姓名'],rows,inspect=True))
        self.assertEqual(checked['summary']['name_conflicts'][0]['rows'],[2,3])
        before=self.store.snapshot()
        r=self.excel(['学号','照片','姓名'],rows,name_column='C')
        self.assertEqual(r.status_code,409)
        for text in ['00123','张三','李四','第2、3行']:self.assertIn(text,r.json()['message'])
        self.assertEqual(self.store.snapshot(),before)
        self.assertFalse((self.root/'inputs').exists())
        r=self.excel(['学号','照片','姓名'],[rows[0],rows[0]],name_column='C')
        self.assertEqual(r.status_code,409);self.assertIn('重复学号',r.json()['message'])

    def test_delivered_name_update_preserves_photos_review_and_old_package(self):
        self.approve(('00123',None))
        batch=self.accepted(self.deliver(['00123']))
        raw=self.zip_check(batch,['00123.jpg'])
        self.accepted(self.client.post('/api/v1/deliveries/'+batch['id']+'/confirm'))
        before=self.store.snapshot()['students']['00123']
        job=self.accepted(self.excel(['学号','照片','姓名'],[['00123',self.uri,'张三']],name_column='C'))
        after=self.store.snapshot()['students']['00123']
        self.assertEqual(after['name'],'张三')
        self.assertEqual({k:v for k,v in before.items() if k!='name'},{k:v for k,v in after.items() if k!='name'})
        self.assertIn('00123',self.store.snapshot()['jobs'][job['id']]['errors'])
        self.assertEqual(self.client.get('/api/v1/deliveries/'+batch['id']+'/download').content,raw)

    def test_missing_name_blocks_preview_and_delivery_without_writes(self):
        self.approve(('00123',None));pid=self.profile()
        for name in [None,'','  ']:
            self.store.change('合成空姓名',lambda d:d['students']['00123'].update(name=name))
            before=self.store.snapshot()
            p=self.preview(['00123'],pid);self.assertFalse(p['ok']);self.assertIn('name',p['items'][0]['reason'])
            r=self.deliver(['00123'],pid);self.assertEqual(r.status_code,409);self.assertIn('00123',r.json()['message'])
            self.assertEqual(self.store.snapshot(),before);self.assertFalse((self.root/'deliveries').exists())

    def test_single_batch_zip_bytes_and_manifest(self):
        self.approve(('00123','张三'),('00456','李四'),('00789','张/三'))
        pid=self.profile()
        self.assertEqual(self.preview(['00123'],pid)['items'][0]['file_name'],'00123-张三.jpg')
        single=self.accepted(self.deliver(['00123'],pid));self.zip_check(single,['00123-张三.jpg'])
        self.assertEqual(self.preview(['00456','00789'],pid)['items'][1]['status'],'warning')
        batch=self.accepted(self.deliver(['00456','00789'],pid))
        self.zip_check(batch,['00456-李四.jpg','00789-张_三.jpg'])
        self.assertEqual(batch['items'][1]['name'],'张/三')
        self.assertEqual(batch['format_snapshot']['template'],'{student_id}-{name}{ext}')

    def test_sanitized_and_windows_case_collisions_block_delivery(self):
        self.approve(('00123','张/三'),('00456','张:三'))
        pid=self.profile('{name}{ext}')
        for a,b in [('张/三','张:三'),('Alice','alice')]:
            self.store.change('合成重名',lambda d:(d['students']['00123'].update(name=a),d['students']['00456'].update(name=b)))
            before=self.store.snapshot()
            p=self.preview(['00123','00456'],pid);self.assertFalse(p['ok'])
            self.assertEqual(self.deliver(['00123','00456'],pid).status_code,409)
            self.assertEqual(self.store.snapshot(),before)
        r=render(validate_profile('尾部','{name}{ext} . '),{'name':'张三','ext':'.jpg'})
        self.assertEqual(r['file_name'],'张三.jpg');self.assertEqual(r['status'],'warning')

    def test_zip_does_not_parse_or_overwrite_name(self):
        self.store.roster(['00123'])
        self.store.change('合成姓名',lambda d:d['students']['00123'].update(name='原姓名'))
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('00123-另一个-姓名 空格.jpg',self.photo)
        files={'file':('test.zip',out.getvalue())}
        checked=self.accepted(self.client.post('/api/v1/imports/zip',files=files))
        self.accepted(self.client.post('/api/v1/imports/zip',files=files,data={'inspect_only':'false','check_token':checked['check_token']}))
        self.service.thread.join(10)
        self.assertEqual(self.store.snapshot()['students']['00123']['name'],'原姓名')

    def test_legacy_manifest_read_download_do_not_rewrite(self):
        self.approve(('00123',None));batch=self.accepted(self.deliver(['00123']))
        self.zip_check(batch,['00123.jpg'])
        batch.pop('format_snapshot')
        for e in batch['items']:e.pop('name');e.pop('file_name')
        folder=self.root/'deliveries'/batch['id'];raw=json.dumps(batch).encode()
        (folder/'manifest.json').write_bytes(raw)
        with zipfile.ZipFile(folder/'photos.zip','w') as z:
            z.writestr('00123.jpg',self.photo);z.writestr('manifest.json',raw)
        original=(folder/'photos.zip').read_bytes()
        self.store.change('旧批次测试',lambda d:d['deliveries'].update({batch['id']:batch}))
        self.assertEqual(self.accepted(self.client.get('/api/v1/deliveries'))[0],batch)
        self.assertEqual(self.client.get('/api/v1/deliveries/'+batch['id']+'/download').content,original)
        self.assertEqual((folder/'manifest.json').read_bytes(),raw)
        self.assertEqual(Store(self.root).snapshot()['deliveries'][batch['id']],batch)

    def test_json_roundtrip_and_unimplemented_fields_rejected(self):
        pid=self.profile()
        doc=self.accepted(self.client.get('/api/v1/export-profiles/'+pid+'/json'))
        self.assertEqual(doc['fields'],['student_id','name','ext'])
        imported=self.accepted(self.client.post('/api/v1/export-profiles/import',json=doc))
        self.assertEqual(imported['template'],doc['template'])
        for field in ['class_name','grade','major']:
            body={'name':field,'template':'{'+field+'}{ext}'}
            for route,method in [('/api/v1/export-profiles','POST'),('/api/v1/export-profiles/import','POST'),('/api/v1/export-profiles/'+pid,'PUT')]:
                self.assertEqual(self.client.request(method,route,json=body).status_code,409)

if __name__ == '__main__':
    unittest.main()
