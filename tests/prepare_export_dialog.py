"""Independent synthetic workspace for compiled Vue delivery-dialog acceptance."""
import io, json, secrets, shutil, sys
from pathlib import Path
from PIL import Image

repo=Path(__file__).resolve().parents[1]
root=Path(sys.argv[1]).resolve()
assert not (root/'workspace').exists(), 'Use a new independent directory; never reset existing data'
root.mkdir(parents=True,exist_ok=True)
app=root/'app';app.mkdir(exist_ok=True)
for p in repo.glob('*.py'):shutil.copyfile(p,app/p.name)
(app/'v2').mkdir(exist_ok=True)
(app/'v2/web').mkdir(exist_ok=True)
for p in (repo/'v2').glob('*.py'):shutil.copyfile(p,app/'v2'/p.name)
sys.path.insert(0,str(app))
from v2.system import create_system_app
token=secrets.token_urlsafe(32);(root/'token.txt').write_text(token,'utf-8')
system=create_system_app(root/'workspace',token).state.system
assert not system.data['cohorts'], 'Use a new independent directory; never reset existing data'
system.update(dict(revision=system.data['revision'],cohorts=[dict(year='2026'),dict(year='2025')],retention_days=30))
cid=system.data['cohorts'][0]['id'];store=system.child(cid).state.store
out=io.BytesIO();Image.new('RGB',(32,40),(45,93,137)).save(out,'JPEG');raw=out.getvalue()
(root/'synthetic.jpg').write_bytes(raw)
records=[('00123','张三'),('00124',None),('00125','张/三'),('00126','张:三')]+[(f'{n:05}',f'合成姓名{n}') for n in range(200,270)]
store.roster([sid for sid,_ in records])
for sid,name in records:store.upload(sid,raw)
def approved(d):
    for sid,name in records:
        s=d['students'][sid];source=s['sources'][-1]
        s.update(name=name,results=[dict(id='result-'+sid,source_id=source['id'],artifact=source,status='success',review='approved')],approved='result-'+sid)
        s['history'].append(dict(action='合成已审核测试成片',at='2026-09-21T00:00:00+00:00'))
store.change('准备独立合成数据',approved)
def profiles(d):
    for pid,name,template,status in [('named','姓名格式','{student_id}-{name}{ext}','active'),('same-name','仅姓名','{name}{ext}','active'),('disabled','停用格式','{student_id}{ext}','inactive')]:
        d['export_profiles'].append(dict(id=pid,name=name,template=template,revision=2,status=status,is_default=False,history=[],rules={'missing_field':'error','invalid_character':'replace','duplicate_name':'error'}))
store.change('准备合成格式',profiles)
(root/'fixture.json').write_text(json.dumps(dict(cohort=cid,other_cohort=system.data['cohorts'][1]['id'],ids=[sid for sid,_ in records]),indent=2),'utf-8')
(root/'server.py').write_text("import sys\nfrom pathlib import Path\nimport uvicorn\nr=Path(__file__).resolve().parent\nsys.path.insert(0,str(r/'app'))\nfrom v2.system import create_system_app\napp=create_system_app(r/'workspace',(r/'token.txt').read_text('utf-8'))\nif __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=19047)\n",'utf-8')
print('Prepared synthetic fixture:',len(records),'students; no external processing')
