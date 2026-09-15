"""Recreate ONLY a newly named synthetic test container; never removes mounted data.
Usage: python container_lifecycle.py IMAGE TEST_ROOT STOPPED_SYNTHETIC_SEED
"""
import hashlib
import http.cookiejar
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

image, root, seed = sys.argv[1:]
root=Path(root).resolve(); seed=Path(seed).resolve()
assert seed.is_relative_to(root), 'Seed must be inside the dedicated test root'
run=root/('lifecycle-'+uuid.uuid4().hex[:8]);run.mkdir()
data=run/'data';shutil.copytree(seed,data)
name='spf-lifecycle-'+run.name.split('-')[-1]
token=re.search(r'^SPF_API_TOKEN=(.*)$',(root/'test.env').read_text(),re.M)[1].strip()
base='http://127.0.0.1:18973'
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def docker(*args):
    return subprocess.check_output(['docker',*args],text=True,stderr=subprocess.STDOUT).strip()

def api(path,body=None,method=None):
    req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'},method=method)
    with client.open(req,timeout=10) as r:return json.load(r)

def start():
    docker('run','-d','--name',name,'--env-file',str(root/'test.env'),'-e','SPF_WORKSPACE=/data/workspace',
           '-p','127.0.0.1:18973:8769','-v',str(data)+':/data',image)
    for _ in range(60):
        try:
            health=api('/healthz');api('/session',{'token':token});return health
        except OSError:time.sleep(.5)
    raise RuntimeError('Container did not start: '+docker('logs',name))

def stop():
    docker('stop',name);docker('rm',name)

def snapshot():
    records={};hashes={}
    for p in data.rglob('*'):
        if not p.is_file():continue
        key=p.relative_to(data).as_posix()
        if p.name in {'system.json','workspace.json'}:
            obj=json.loads(p.read_text('utf-8'));obj.pop('events',None);obj.pop('audit',None);obj.pop('revision',None)
            records[key]=obj
        else:hashes[key]=hashlib.sha256(p.read_bytes()).hexdigest()
    return dict(records=records,files=hashes)

def save(name,value): (run/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8')

try:
    first=start();settings=api('/api/v1/settings')
    assert settings['retention_days'] is None, 'Browser fixture should have disabled expiry'
    trash=api('/api/v1/recycle-bin');assert trash, 'Need real recycle records for persistence test'
    before=snapshot();save('before.json',before)
    stop();shutil.copytree(data,run/'backup-before-recreate')
    second=start();after=snapshot();save('after.json',after)
    assert before==after, 'Recreated container changed business data, file hashes or expiry'
    stop()
    # Controlled clock fixture only, service stopped. Keep other metadata untouched.
    for p in (data/'workspace'/'cohorts').glob('*/workspace.json'):
        d=json.loads(p.read_text('utf-8'))
        for t in d.get('trash',{}).values():t['expires_at']='2000-01-01T00:00:00+00:00'
        p.write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8')
    start();disabled=api('/api/v1/recycle-bin')
    assert len(disabled)==len(trash) and all(t['expires_at'].startswith('2000-01-01') for t in disabled)
    live_before=snapshot()
    settings=api('/api/v1/settings');settings['retention_days']=30;api('/api/v1/settings',settings,'PUT')
    stop();start()
    for _ in range(60):
        if not api('/api/v1/recycle-bin'):break
        time.sleep(.2)
    assert not api('/api/v1/recycle-bin'), 'Startup failed to clean overdue trash'
    live_after=snapshot()
    for key,d in live_before['records'].items():
        if '/cohorts/' in key:
            for field in ('students','jobs','deliveries','config'):
                assert d[field]==live_after['records'][key][field], field+' changed during cleanup'
    assert live_before['files']==live_after['files'], 'Shared live artifacts unexpectedly deleted'
    save('lifecycle-result.json',dict(image=image,image_id=docker('image','inspect',image,'--format','{{.Id}}'),
        initial_health=first,recreated_health=second,files=len(before['files']),recycle_records=len(trash),
        checks=['full business records and all file hashes preserved after container removal/recreation',
                'existing expiry unchanged after restart with cleanup disabled',
                'controlled overdue entries cleaned on startup after re-enabling',
                'live students/photos/reviews/deliveries/config preserved during cleanup'],
        backup=str(run/'backup-before-recreate')))
    print('PASS lifecycle '+str(run/'lifecycle-result.json'))
finally:
    # Only this script-created container. Never remove volumes or mounted paths.
    if name in docker('ps','-a','--format','{{.Names}}').splitlines():stop()
