"""Build and verify a candidate from an exact, clean source commit. Never publish."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
import zipfile

ROOT=Path(__file__).resolve().parent

def validate_web(directory):
    web=Path(directory).resolve()
    info_path=web/'build-info.json'
    if not info_path.is_file():
        raise ValueError('Vue build metadata missing: '+str(info_path))
    info=json.loads(info_path.read_text(encoding='utf-8'))
    html=(web/'index.html').read_text(encoding='utf-8')
    refs=re.findall(r'(?:src|href)="(/assets/[^"]+)"',html)
    if not refs or not any(x.endswith('.js') for x in refs):
        raise ValueError('Vue module entry missing')
    if any((web/name).exists() for name in ('legacy.html','app.js','style.css')):
        raise ValueError('Legacy frontend must not be included in Vue output')
    if not info.get('source_commit') or not info.get('files'):
        raise ValueError('Source commit or asset manifest missing')
    for name,expected in info['files'].items():
        path=(web/name).resolve()
        if not path.is_relative_to(web) or not path.is_file():
            raise ValueError('Missing Vue asset: '+name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('Vue asset hash mismatch: '+name)
    for ref in refs:
        if ref.lstrip('/') not in info['files']:
            raise ValueError('Unverified Vue entry: '+ref)
    return info

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    def command(parts,**kw):
        return subprocess.run(parts,cwd=ROOT,check=True,**kw)
    if command(['git','status','--porcelain','--untracked-files=normal'],capture_output=True,text=True).stdout.strip():
        raise SystemExit('Commit or preserve working changes before building an exact candidate.')
    commit=command(['git','rev-parse','HEAD'],capture_output=True,text=True).stdout.strip()
    from v2.version import VERSION
    output=Path(args.output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    build=output/('build-'+uuid.uuid4().hex[:10])
    build.mkdir()
    web=build/'web'
    env=dict(os.environ,SPF_VUE_OUT=str(web))
    npm=shutil.which('npm.cmd') or shutil.which('npm')
    with (build/'build.log').open('w',encoding='utf-8') as log:
        subprocess.run([npm,'run','build'],cwd=ROOT/'v2/web-vue',env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        info=dict(version=VERSION,source_commit=commit,build_id=build.name,frontend='Vue 3 + Element Plus',
                  files={p.relative_to(web).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in web.rglob('*') if p.is_file()})
        (web/'build-info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
        validate_web(web)
        env['SPF_WEB_ROOT']=str(web)
        command([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(build/'dist'),'--workpath',str(build/'work'),str(ROOT/'StudentPhotoFlowV2.spec')],env=env,stdout=log,stderr=subprocess.STDOUT)
    portable=build/'dist/StudentPhotoFlowV2'
    validate_web(portable/'_internal/v2/web')
    for path in ROOT.glob('V2*.md'):
        shutil.copy2(path,portable/path.name)
    shutil.copy2(web/'build-info.json',portable/'build-info.json')
    package=output/f'StudentPhotoFlow_Windows_x64_v{VERSION}_{commit[:8]}_{build.name}.zip'
    with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in portable.rglob('*'):
            if path.is_file():
                archive.write(path,'StudentPhotoFlowV2/'+path.relative_to(portable).as_posix())
    digest=hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_suffix('.zip.sha256').write_text(digest+'  '+package.name+'\n',encoding='utf-8')
    receipt=dict(zip=str(package),sha256=digest,source_commit=commit,version=VERSION,build_log=str(build/'build.log'))
    (output/'candidate.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False))

if __name__=='__main__':
    main()
