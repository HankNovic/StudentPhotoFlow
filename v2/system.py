"""Cohort directories wrap the existing business API; no second photo pipeline."""
import asyncio
import copy
import json
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route
from .api import create_app
from .store import atomic, actor, now
from . import recycle
from .version import VERSION

class System:
    def __init__(self, root, token):
        self.root=Path(root).resolve()
        if (self.root/'workspace.json').exists() or (self.root/'export_state.json').exists():
            raise ValueError('此版本使用新的届次目录结构；请保留旧工作区并设置 SPF_WORKSPACE 为新的空目录，不支持自动迁移')
        self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'system.json'
        self.data=json.loads(self.path.read_text('utf-8')) if self.path.exists() else dict(schema_version=3,revision=0,cohorts=[],retention_days=30,audit=[])
        if self.data.get('schema_version')!=3: raise ValueError('系统数据格式不支持')
        atomic(self.path,self.data)
        self.token=token
        self.apps={}
        self.gate=asyncio.Lock()
        self.control=create_app(self.root/'control',token)
        self.control.state.docker_mode=True
        for c in self.data['cohorts']: self.child(c['id'])

    def save(self, action, mutate, result_status='success'):
        candidate=copy.deepcopy(self.data)
        result=mutate(candidate)
        if result_status=='success': candidate['revision']+=1
        candidate['audit'].append(dict(at=now(),actor=actor.get(),action=action,result=result_status))
        atomic(self.path,candidate); self.data=candidate
        return result

    def cohort(self, cid):
        c=next((x for x in self.data['cohorts'] if x['id']==cid),None)
        if not c: raise ValueError('届次不存在，请重新选择')
        return c

    def child(self,cid):
        c=self.cohort(cid)
        if cid not in self.apps:
            app=create_app(self.root/'cohorts'/cid,self.token)
            store=app.state.store
            store.fixed_cohort=cid
            store.cohort_name=lambda: self.cohort(cid)['name']
            if store.data['active_cohort']!=cid:
                store.change('绑定届次 ID',lambda d:d.update(active_cohort=cid))
            app.state.docker_mode=True
            self.apps[cid]=app
        return self.apps[cid]

    def active(self):
        return any((a.state.service.thread and a.state.service.thread.is_alive()) or any(j['status'] in recycle.ACTIVE for j in a.state.store.data['jobs'].values()) for a in self.apps.values())

    def blockers(self,cid):
        d=self.child(cid).state.store.snapshot()
        return {k:len(d.get(k,{})) for k in ('students','jobs','deliveries','trash') if d.get(k)}

    def settings(self):
        audit=list(self.data['audit'])
        for c in self.data['cohorts']:
            audit.extend(dict(e,cohort=c['name']) for e in self.child(c['id']).state.store.snapshot()['events'][-100:])
        return dict(revision=self.data['revision'],retention_days=self.data['retention_days'],
                    cohorts=[dict(c,blockers=self.blockers(c['id'])) for c in sorted(self.data['cohorts'],key=lambda c:c['year'],reverse=True)],audit=sorted(audit,key=lambda e:e['at'])[-100:])

    def update(self, body):
        if body.get('revision')!=self.data['revision']: raise ValueError('设置已变化，请刷新后重新编辑')
        days=body.get('retention_days',30)
        if days is not None and (type(days)!=int or not 1<=days<=3650): raise ValueError('保留天数为 1–3650，关闭自动清理使用空值')
        records=body.get('cohorts')
        if not isinstance(records,list): raise ValueError('届次列表无效')
        out=[]; names=set(); identities=set(); old={x['id']:x for x in self.data['cohorts']}
        for rec in records:
            year=rec.get('year','').strip() if isinstance(rec.get('year'),str) else ''
            if not re.fullmatch(r'[1-9][0-9]{3}',year): raise ValueError('年份必须是 1000–9999 的四位数字')
            if year in names: raise ValueError('届次年份不能重复（包含归档届次）')
            names.add(year)
            cid=rec.get('id') or uuid.uuid4().hex
            if rec.get('id') and cid not in old: raise ValueError('未知届次 ID')
            if cid in identities: raise ValueError('届次 ID 不能重复')
            identities.add(cid)
            archived=rec.get('archived',False)
            if type(archived)!=bool: raise ValueError('归档状态无效')
            if cid in old and archived!=old[cid]['archived'] and self.active(): raise ValueError('请先安全停止活动任务再归档或恢复')
            out.append(dict(id=cid,year=year,name=year+'级',archived=archived))
        for cid in old.keys()-identities:
            blocks=self.blockers(cid)
            if blocks: raise ValueError(old[cid]['name']+' 不能删除：'+json.dumps(blocks,ensure_ascii=False)+'；前往学生与审核、任务进度、照片交付或回收站管理')
            if cid not in body.get('confirmed_deletions',[]): raise ValueError('删除已保存届次需要专门确认')
        self.save('保存届次及回收站设置 '+','.join(x['name']+':'+x['id'] for x in out)+' 删除 '+','.join(old.keys()-identities),lambda d:d.update(cohorts=out,retention_days=days))
        for cid in old.keys()-identities: self.apps.pop(cid,None)
        return self.settings()

    def sweep(self):
        for c in self.data['cohorts']:
            recycle.cleanup(self.child(c['id']).state.service,self.data['retention_days'] is not None)

class BusinessRoute:
    def __init__(self,system): self.system=system
    async def __call__(self,scope,receive,send):
        system=self.system
        cid=scope['path_params'].get('cid')
        path=('/' if cid else '/api/v1/')+scope['path_params']['path']
        try:
            if not path.startswith('/api/v1/'): raise ValueError('无效接口')
            suffix=path[len('/api/v1/'):]
            if cid:
                c=system.cohort(cid)
                if suffix.startswith(('cohort','recycle-bin','migrations','shutdown','session','config','engines','update')): raise ValueError('请使用系统设置或全局配置入口')
                target=system.child(cid)
            else:
                if not (suffix in {'config','health','update'} or suffix.startswith('engines/')): raise ValueError('请先选择届次')
                target=system.control
            async def dispatch():
                if cid and scope['method']!='GET':
                    if system.cohort(cid)['archived']: raise ValueError('归档届次只读，请在系统设置恢复后操作')
                    starts_work=suffix in {'processing-jobs','imports/xlsx','imports/zip'} or suffix.endswith(('/resume','/preview'))
                    if starts_work and any(a.state.service.thread and a.state.service.thread.is_alive() for key,a in system.apps.items() if key!=cid): raise ValueError('其他届次有活动任务，请先等待完成或安全中断')
                inner=dict(scope,path=path,raw_path=path.encode(),root_path='')
                inner['headers']=[(k,v) for k,v in scope['headers'] if k not in {b'authorization',b'origin'}]+[(b'authorization',('Bearer '+system.token).encode())]
                await target(inner,receive,send)
            if scope['method']=='GET': await dispatch()
            else:
                async with system.gate: await dispatch()
        except ValueError as exc:
            await JSONResponse({'message':str(exc)},409)(scope,receive,send)

def create_system_app(root,token):
    if not token or len(token)<16 or token=='replace-with-a-long-random-secret': raise ValueError('请设置至少 16 位随机 SPF_API_TOKEN，不能使用示例值')
    build_file=Path(__file__).parent/'web'/'build-info.json'
    build_id=json.loads(build_file.read_text('utf-8')).get('build_id','unknown') if build_file.exists() else 'unknown'
    system=System(root,token)
    sessions={}
    async def cleaner():
        while True:
            async with system.gate: await asyncio.to_thread(system.sweep)
            await asyncio.sleep(60)
    @asynccontextmanager
    async def lifespan(app):
        task=asyncio.create_task(cleaner())
        yield
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
    app=FastAPI(title='StudentPhotoFlow',version=VERSION,lifespan=lifespan)
    app.state.system=system

    @app.middleware('http')
    async def auth(request,call_next):
        path=request.url.path
        protected=path.startswith(('/api/','/cohorts/')) or path in {'/docs','/openapi.json'}
        session=sessions.get(request.cookies.get('spf_session',''))
        bearer=request.headers.get('authorization','').removeprefix('Bearer ')
        who=''
        if session and session['expires']>time.time(): who=session['identity']
        elif secrets.compare_digest(bearer,token): who='api-admin'
        if protected and not who: return JSONResponse({'message':'会话已失效，请登录'},403)
        if request.method!='GET' and request.headers.get('origin') and request.headers['origin']!=str(request.base_url).rstrip('/'):
            return JSONResponse({'message':'禁止跨站访问'},403)
        context=actor.set(who or 'anonymous')
        try:
            response=await call_next(request)
            if protected and request.method!='GET' and response.status_code>=400:
                system.save('操作失败 '+path+' HTTP '+str(response.status_code),lambda d:None,result_status='failed')
            response.headers.update({'Cache-Control':('public, max-age=31536000, immutable' if path.startswith('/assets/') and response.status_code==200 else 'no-store'),'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'})
            return response
        finally: actor.reset(context)

    @app.exception_handler(ValueError)
    async def invalid(request,exc): return JSONResponse({'message':str(exc)},409)

    @app.post('/session')
    async def login(request:Request):
        body=await request.json()
        if not secrets.compare_digest(str(body.get('token','')),token): return JSONResponse({'message':'令牌错误'},403)
        sid=secrets.token_urlsafe(32)
        sessions[sid]={'identity':'session-'+uuid.uuid4().hex[:12],'expires':time.time()+28800}
        for key in list(sessions):
            if sessions[key]['expires']<=time.time(): del sessions[key]
        response=JSONResponse({'ok':True})
        response.set_cookie('spf_session',sid,httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=28800)
        return response

    @app.post('/logout')
    def logout(request:Request):
        sessions.pop(request.cookies.get('spf_session',''),None)
        response=JSONResponse({'ok':True}); response.delete_cookie('spf_session'); return response

    @app.get('/healthz')
    def probe(): return dict(status='ok',version=VERSION,source_commit=os.getenv('SPF_SOURCE_COMMIT','unknown'),build_id=build_id,schema_version=3)

    @app.get('/api/v1/health')
    def health(): return dict(probe(),workspace=str(system.root),docker_mode=True)

    @app.get('/api/v1/cohorts')
    @app.get('/api/v1/settings')
    def settings(): return system.settings()

    @app.put('/api/v1/settings')
    async def save_settings(request:Request):
        body=await request.json()
        async with system.gate: return system.update(body)

    @app.delete('/api/v1/cohorts/{cid}')
    async def remove_cohort(cid:str,request:Request):
        body=await request.json()
        async with system.gate:
            if body.get('confirmed') is not True: raise ValueError('删除空届次需要专门确认')
            current=system.settings()
            system.cohort(cid)
            current['cohorts']=[c for c in current['cohorts'] if c['id']!=cid]
            current['confirmed_deletions']=[cid]
            return system.update(current)

    @app.get('/api/v1/recycle-bin')
    def trash():
        rows=[]
        for c in system.data['cohorts']:
            for t in system.child(c['id']).state.store.snapshot().get('trash',{}).values():
                rows.append(dict(id=t['id'],cohort_id=c['id'],cohort=c['name'],students=list(t['students']),photos=len(recycle.files(t['students'])),deliveries=len(t['deliveries']),deleted_at=t['deleted_at'],expires_at=t['expires_at'],status=t['status'],error=t.get('error'),scope=t['scope']))
        return rows

    @app.post('/api/v1/recycle-bin/{cid}/preview')
    @app.post('/api/v1/recycle-bin/{cid}/move')
    async def delete_students(cid:str,request:Request):
        body=await request.json()
        async with system.gate:
            c=system.cohort(cid); service=system.child(cid).state.service
            if c['archived']: raise ValueError('归档届次只读，请先恢复')
            with service.store.lock:
                recycle.inactive(service)
                result=recycle.impact(service,body.get('student_ids',[]),body.get('all',False))
                if request.url.path.endswith('/preview'): return dict({k:v for k,v in result.items() if k!='bundle'},cohort=c['name'])
                if body.get('all') and body.get('confirmation')!=c['name']: raise ValueError('清空本届必须输入完整届次名称')
                if body.get('confirmed') is not True: raise ValueError('请确认实际影响范围')
                return recycle.move(service,body.get('student_ids',[]),system.data['retention_days'],body.get('revision'),body.get('all',False))

    @app.post('/api/v1/recycle-bin/{cid}/{identity}/{action}')
    async def trash_action(cid:str,identity:str,action:str,request:Request):
        body=await request.json()
        async with system.gate:
            if system.cohort(cid)['archived']: raise ValueError('归档届次只读，请先恢复')
            service=system.child(cid).state.service
            if action=='restore': return recycle.restore(service,identity)
            if action=='purge' and body.get('confirmation')=='永久删除': return recycle.purge(service,identity)
            raise ValueError('永久删除需明确输入“永久删除”')

    app.router.routes.append(Route('/cohorts/{cid}/{path:path}',BusinessRoute(system),methods=['GET','POST','PUT','DELETE']))
    app.router.routes.append(Route('/api/v1/{path:path}',BusinessRoute(system),methods=['GET','POST','PUT','DELETE']))
    app.mount('/',StaticFiles(directory=Path(__file__).parent/'web',html=True),name='web')
    return app
