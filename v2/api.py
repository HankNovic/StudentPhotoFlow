import asyncio
import json
import secrets
import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from photo_pipeline import PipelineOptions, test_hivision_api
from .store import Store
from .service import Service


class Ids(BaseModel):
    student_ids: list[str] = Field(min_length=1, max_length=10000)


class Processing(Ids):
    config: dict = Field(default_factory=dict)
    dry_run: bool = True
    new_version: bool = False
    expected_revision: int | None = None


class Review(BaseModel):
    student_id: str
    result_id: str
    decision: str
    expected_revision: int
    reason: str = ''


class Reason(BaseModel):
    reason: str


class Historical(Ids):
    reason: str
    confirmed_sent: bool = False


def create_app(root, token=None):
    store = Store(root)
    service = Service(store)
    app = FastAPI(title='StudentPhotoFlow V2', version='2.0.0')
    app.state.store, app.state.service = store, service
    token = token or secrets.token_urlsafe(32)
    app.state.token = token
    app.state.stopping = False

    @app.middleware('http')
    async def security(request, call_next):
        if request.url.path.startswith('/api/') or request.url.path in {'/docs','/openapi.json'}:
            bearer = request.headers.get('authorization', '').removeprefix('Bearer ')
            cookie = request.cookies.get('spf_session','')
            if not (secrets.compare_digest(bearer,token) or secrets.compare_digest(cookie,token)):
                return JSONResponse({'message':'请从启动器打开工作台，或携带Bearer令牌'},403)
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'message':'禁止跨站访问'},403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'message':str(exc)},409)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({'message':'记录不存在'},404)

    @app.post('/session')
    async def session(request: Request):
        data = await request.json()
        if not secrets.compare_digest(str(data.get('token','')),token):
            return JSONResponse({'message':'令牌错误'},403)
        response = JSONResponse({'ok':True})
        response.set_cookie('spf_session',token,httponly=True,samesite='strict')
        return response

    @app.get('/api/v1/health')
    def health():
        return {'version':'2.0.0','workspace':str(store.root)}

    @app.post('/api/v1/shutdown')
    def shutdown():
        if service.thread and service.thread.is_alive():
            raise ValueError('请先安全中断当前任务，等待停止后退出')
        callback=getattr(app.state,'shutdown',None)
        app.state.stopping=True
        if callback:
            callback()
        return {'ok':True}

    @app.get('/api/v1/students')
    def students(status: str = '', q: str = ''):
        rows = []
        snapshot=store.snapshot()
        for sid,s in snapshot['students'].items():
            source,result = Store.current(s)
            state = 'processing' if Store.processing(snapshot,sid) else Store.status(s)
            if (not status or status == state) and q.casefold() in sid.casefold():
                rows.append(dict(id=sid,status=state,source=source,result=result,revision=len(s['history'])))
        return sorted(rows,key=lambda r:r['id'])

    @app.get('/api/v1/students/{sid}')
    def student(sid: str):
        s = store.snapshot()['students'][sid]
        return dict(s,status=Store.status(s),revision=len(s['history']))

    @app.post('/api/v1/roster')
    def roster(body: Ids):
        return store.roster(body.student_ids)

    @app.post('/api/v1/students/{sid}/photos')
    def upload(sid: str, photo: UploadFile = File(...)):
        return store.upload(sid,photo.file.read(30*1024*1024+1))

    @app.get('/api/v1/config')
    def config():
        return dict(defaults=asdict(PipelineOptions()),saved=store.snapshot()['config'])

    @app.put('/api/v1/config')
    def save_config(body: dict):
        config = asdict(service.options(body))
        store.change('保存配置',lambda d:d.update(config=config))
        return config

    @app.post('/api/v1/engines/hivision/test')
    def test_api(body: dict):
        return test_hivision_api(str(body['url']),15)

    @app.post('/api/v1/processing-jobs')
    def process(body: Processing):
        if body.dry_run:
            return service.plan(body.student_ids,body.config,body.new_version)
        return service.start(body.student_ids,body.config,body.new_version,body.expected_revision)

    @app.post('/api/v1/students/{sid}/preview')
    def preview(sid: str, body: dict):
        source,_ = Store.current(store.snapshot()['students'][sid])
        if not source:
            raise ValueError('没有原图')
        return service.execute(source,body)

    @app.get('/api/v1/jobs')
    def jobs():
        return list(store.snapshot()['jobs'].values())[::-1]

    @app.post('/api/v1/jobs/{job_id}/{action}')
    def control(job_id: str, action: str):
        return service.control(job_id,action)

    @app.post('/api/v1/reviews')
    def review(body: Review):
        return store.review(body.student_id,body.result_id,body.decision,body.expected_revision,body.reason)

    @app.post('/api/v1/students/{sid}/replacement')
    def replacement(sid: str, body: Reason):
        service.replacement(sid,body.reason)
        return {'ok':True}

    @app.get('/api/v1/deliveries')
    def deliveries():
        return list(store.snapshot()['deliveries'].values())[::-1]

    @app.post('/api/v1/deliveries')
    def deliver(body: Ids):
        return service.delivery(body.student_ids)

    @app.post('/api/v1/historical-deliveries')
    def historical(body: Historical):
        if not body.confirmed_sent:
            raise ValueError('必须确认名单对应实际已发送的照片')
        return service.historical_delivery(body.student_ids,body.reason)

    @app.post('/api/v1/deliveries/{batch_id}/confirm')
    def confirm(batch_id: str):
        return service.confirm_delivery(batch_id)

    @app.post('/api/v1/deliveries/{batch_id}/cancel')
    def cancel_delivery(batch_id: str):
        def update(d):
            batch = d['deliveries'][batch_id]
            if batch['status'] != 'prepared':
                raise ValueError('只能取消尚未交付的包')
            batch['status'] = 'cancelled'
        store.change('取消交付包',update)
        return {'ok':True}

    @app.get('/api/v1/deliveries/{batch_id}/download')
    def download(batch_id: str):
        batch = store.snapshot()['deliveries'][batch_id]
        if batch['status'] == 'cancelled':
            raise ValueError('交付包已取消')
        return FileResponse(store.file(f'deliveries/{batch_id}/photos.zip'),filename=f'学生照片_{batch_id[:8]}.zip')

    @app.get('/api/v1/artifacts/{sha}')
    def artifact(sha: str):
        import re
        if not re.fullmatch('[0-9a-f]{64}',sha):
            raise ValueError('图片标识无效')
        paths = list((store.root/'artifacts'/sha[:2]).glob(sha+'.*'))
        if not paths:
            raise ValueError('图片不存在')
        return FileResponse(paths[0])

    @app.get('/api/v1/events')
    async def events(request: Request):
        async def stream():
            revision = -1
            while not app.state.stopping and not await request.is_disconnected():
                current = store.snapshot()['revision']
                if current != revision:
                    yield 'data: '+json.dumps({'revision':current})+'\n\n'
                    revision = current
                await asyncio.sleep(1)
        return StreamingResponse(stream(),media_type='text/event-stream')

    @app.post('/api/v1/imports/xlsx')
    def import_xlsx(file: UploadFile = File(...), sheet: str = Form(''), header_row: int = Form(1), id_column: str = Form('A'), image_column: str = Form('B'), inspect_only: bool = Form(True)):
        from xlsx_photo_core import WorkbookReader, inspect_selection, column_index, _fetch_source
        raw = file.file.read(50*1024*1024+1)
        if len(raw)>50*1024*1024:
            raise ValueError('表格超过50MB')
        import io, zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if len(archive.infolist())>10000 or sum(x.file_size for x in archive.infolist())>200*1024*1024:
                    raise ValueError('表格解压后过大')
        except zipfile.BadZipFile:
            raise ValueError('不是有效的xlsx文件')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'input.xlsx'
            path.write_bytes(raw)
            with WorkbookReader(path) as reader:
                sheets=reader.sheets
                name=sheet or sheets[0].name
            report=inspect_selection(path,name,header_row,column_index(id_column),column_index(image_column))
            if inspect_only:
                return dict(summary=report.summary,headers=report.headers,sheet=name)
            if report.summary['duplicate_count'] or report.summary['empty_ids']:
                raise ValueError('请先修正空学号或重复学号')
            return service.start_import(raw,name,header_row,column_index(id_column),column_index(image_column),report.rows)

    @app.post('/api/v1/migrations/legacy')
    def migrate(body: dict):
        from .migration import migrate_legacy
        return migrate_legacy(store,body['path'],bool(body.get('apply',False)))

    app.mount('/',StaticFiles(directory=Path(__file__).parent/'web',html=True),name='web')
    return app
