import asyncio
import json
import secrets
import tempfile
import urllib.request
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from photo_pipeline import PipelineOptions, test_hivision_api
from .store import Store
from .service import Service


class Ids(BaseModel):
    student_ids: list[str] = Field(min_length=1, max_length=10000, description='学号字符串列表，保留前导零；最多 10000 项。')


class Cohort(BaseModel):
    cohort: str = Field(..., pattern=r'^\d{4}级$', description='届次，例如 2026级')


class BatchReview(BaseModel):
    student_ids: list[str] = Field(min_length=1, max_length=10000)
    decision: str = Field(..., description='approved=通过，rejected=退回')
    reason: str = ''


class Processing(Ids):
    config: dict = Field(default_factory=dict, description='处理配置对象；省略的字段使用系统默认值。')
    dry_run: bool = Field(True, description='true 只预览执行计划；false 正式创建任务。')
    new_version: bool = Field(False, description='是否明确生成新处理版本，仍遵守交付保护。')
    expected_revision: int | None = Field(None, description='并发校验修订号：处理用计划 revision，审核用学生 revision。')


class Review(BaseModel):
    student_id: str = Field(..., description='学生学号。')
    result_id: str = Field(..., description='学生当前正式成片的版本 id。')
    decision: str = Field(..., description='approved=通过，rejected=退回，pending=撤回审核。')
    expected_revision: int = Field(..., description='并发校验修订号：处理用计划 revision，审核用学生 revision。')
    reason: str = Field('', description='操作原因或历史交付依据。')


class Reason(BaseModel):
    reason: str = Field(..., description='操作原因或历史交付依据。')


class Historical(Ids):
    reason: str = Field(..., description='操作原因或历史交付依据。')
    confirmed_sent: bool = Field(False, description='必须为 true，确认照片已实际发送。')


API_DESCRIPTION = '学生照片工作台的本地接口。所有学号使用字符串，保留前导零。\n\n### 认证与错误\n从启动器打开工作台后，浏览器使用会话 Cookie。外部程序在每次请求中添加 `Authorization: Bearer <令牌>`；可在启动前设置环境变量 `SPF_API_TOKEN`。端口由启动器分配，固定端口可用 `--port 8769`。\n\nJSON 请求使用 `Content-Type: application/json`；上传文件使用 multipart/form-data。\n403：认证失败或跨站访问；404：记录不存在；409：业务状态冲突或配置非法（message 为中文原因）；422：请求字段验证失败（detail 为字段错误列表）。遇到修订号冲突应刷新记录，检查后重试。\n\n### 推荐调用顺序\n登记名单 → 上传原图 → 预览处理计划 → 正式处理 → 查询任务 → 查询学生详情 → 审核成片 → 生成交付包 → 下载 → 实际发送后确认交付。\n\n预览计划示例：`{"student_ids":["001"],"config":{"crop_enabled":true,"crop_width":295,"crop_height":413},"dry_run":true}`。\n正式处理复用同一份 student_ids 和 config，设置 dry_run=false，并传入预览返回的 revision 作为 expected_revision。\n审核示例：`{"student_id":"001","result_id":"从详情取得的版本ID","decision":"approved","expected_revision":3}`，修订号也应从当前详情取得。\n\n### 学生状态\nmissing 未采集；pending 原图待处理；processing 任务未完成；review 待人工审核；approved 审核通过待交付；delivered 已交付；delivered_updated 交付后照片变化；machine_rejected 机器退回；review_rejected 人工退回；failed 处理失败。\n\n### 处理配置参数\nGET /config 返回每项默认值及已保存值。配置和单张预览接收相同字段；background_mode 为 none（不处理）、quick（快速换色）、ai（本地抠图）或 hivision（外部服务）。颜色使用 #RRGGBB，宽高单位为像素；布尔值使用 true/false。Hivision 参数仅在该引擎下生效；最终裁切在背景处理之后执行。\n\n| 字段 | 中文含义 |\n| --- | --- |\n| `quality_enabled` | 启用内置预检 |\n| `auto_orient` | 校正90/180/270度方向 |\n| `check_grayscale` | 黑白检查 |\n| `check_face` | 人脸检查 |\n| `check_glare` | 反光检查 |\n| `check_recapture` | 翻拍检查 |\n| `background_mode` | 图像处理引擎 |\n| `background_color` | 背景颜色 |\n| `crop_enabled` | 最终裁切并缩放 |\n| `crop_width` | 最终宽度（像素） |\n| `crop_height` | 最终高度（像素） |\n| `hivision_url` | Hivision API地址 |\n| `hivision_timeout` | API超时（秒） |\n| `hivision_width` | Hivision标准图宽 |\n| `hivision_height` | Hivision标准图高 |\n| `hivision_hd` | 使用高清返回 |\n| `hivision_face_align` | Hivision小角度对齐 |\n| `hivision_matting_model` | Hivision抠图模型 |\n| `hivision_face_model` | Hivision人脸模型 |\n| `hivision_dpi` | 打印DPI |\n| `stop_on_reject` | 预检不通过时停止 |\n| `grayscale_ratio_threshold` | 黑白比例阈值 |\n| `grayscale_delta_limit` | 灰度色差阈值 |\n| `face_confidence_threshold` | 人脸置信度阈值 |\n| `glare_ratio_threshold` | 反光比例阈值 |\n| `glare_luma_threshold` | 过曝亮度阈值 |\n| `recapture_score_threshold` | 翻拍分数阈值 |\n| `orientation_min_confidence` | 方向最低置信度 |\n| `orientation_confidence_margin` | 旋转置信度优势 |\n| `hivision_head_measure_ratio` | 面部占比 |\n| `hivision_head_height_ratio` | 面部中心高度 |\n| `hivision_top_distance_max` | 头顶留白最大比例 |\n| `hivision_top_distance_min` | 头顶留白最小比例 |\n| `hivision_brightness_strength` | 亮度调整 |\n| `hivision_contrast_strength` | 对比度调整 |\n| `hivision_sharpen_strength` | 锐化调整 |\n| `hivision_saturation_strength` | 饱和度调整 |\n'

def detect_cohort(reader, sheet, header_row, headers):
    import re
    column = next((i for i, h in enumerate(headers) if re.search(r'学年|届次|年级|入学年份|入学年', str(h), re.I)), None)
    if column is None:
        return None
    values, _ = reader._sheet_values(reader.sheet(sheet))
    found=set()
    for (row, col), value in values.items():
        if row <= header_row or col != column:
            continue
        match=re.search(r'(?<!\d)(20\d{2})(?:\s*级|\s*届)?', str(value))
        if match:
            found.add(match.group(1)+'级')
    if len(found)>1:
        raise ValueError('Excel中存在多个学年/届次：'+ '、'.join(sorted(found)))
    return next(iter(found), None)


def create_app(root, token=None):
    store = Store(root)
    service = Service(store)
    app = FastAPI(title='StudentPhotoFlow V2', version='2.1.0', description=API_DESCRIPTION)
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

    @app.post('/session', tags=['访问与服务'], summary='建立浏览器会话', description='提交 JSON：`{"token":"访问令牌"}`。成功返回 ok 并设置 HttpOnly 会话 Cookie。令牌错误返回 403。')
    async def session(request: Request):
        data = await request.json()
        if not secrets.compare_digest(str(data.get('token','')),token):
            return JSONResponse({'message':'令牌错误'},403)
        response = JSONResponse({'ok':True})
        response.set_cookie('spf_session',token,httponly=True,samesite='strict')
        return response

    @app.get('/api/v1/health', tags=['访问与服务'], summary='查看版本和工作区', description='返回 version（程序版本）及 workspace（当前工作区绝对路径）。')
    def health():
        return {'version':'2.1.0','workspace':str(store.root),'active_cohort':store.snapshot().get('active_cohort')}

    @app.get('/api/v1/update', tags=['访问与服务'], summary='检查在线更新')
    async def update():
        url='https://api.github.com/repos/HankNovic/StudentPhotoFlow/releases/latest'
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'StudentPhotoFlow'})
            data=json.loads((await asyncio.to_thread(urllib.request.urlopen,req,5)).read())
            return {'version':data.get('tag_name','').lstrip('v'),'url':data.get('html_url'),'assets':[{'name':a['name'],'url':a['browser_download_url']} for a in data.get('assets',[])]}
        except Exception as e:
            return JSONResponse({'message':f'检查更新失败：{e}'},status_code=503)

    @app.post('/api/v1/shutdown', tags=['访问与服务'], summary='安全退出程序', description='返回 ok；仍有运行任务时返回 409，应先中断任务并等待停止。')
    def shutdown():
        if service.thread and service.thread.is_alive():
            raise ValueError('请先安全中断当前任务，等待停止后退出')
        callback=getattr(app.state,'shutdown',None)
        app.state.stopping=True
        if callback:
            callback()
        return {'ok':True}

    @app.get('/api/v1/cohorts', tags=['学生与原图'], summary='读取届次列表')
    def cohorts():
        d=store.snapshot()
        values={s.get('cohort',d.get('active_cohort')) for s in d['students'].values()}
        values.add(d.get('active_cohort'))
        return {'active_cohort':d.get('active_cohort'),'cohorts':sorted(x for x in values if x)}

    @app.put('/api/v1/cohort', tags=['学生与原图'], summary='切换当前届次')
    def cohort(body: Cohort):
        return store.set_cohort(body.cohort)

    @app.get('/api/v1/exports/students.txt', tags=['学生与原图'], summary='导出当前届全部学号')
    def export_students():
        d=store.snapshot(); active=d.get('active_cohort')
        ids=sorted(sid for sid,s in d['students'].items() if s.get('cohort',active)==active)
        return PlainTextResponse('\n'.join(ids)+'\n',media_type='text/plain; charset=utf-8',headers={'Content-Disposition':'attachment; filename=students.txt'})

    @app.get('/api/v1/exports/delivered.txt', tags=['学生与原图'], summary='导出当前届已交付学号')
    def export_delivered():
        d=store.snapshot(); active=d.get('active_cohort')
        ids=sorted(sid for sid,s in d['students'].items() if s.get('cohort',active)==active and s.get('delivered'))
        return PlainTextResponse('\n'.join(ids)+'\n',media_type='text/plain; charset=utf-8',headers={'Content-Disposition':'attachment; filename=delivered.txt'})

    @app.get('/api/v1/students', tags=['学生与原图'], summary='查询学生列表', description='status 为状态代码，留空不过滤；q 按学号包含匹配。返回数组，每项含 id、status、source、result、revision。状态代码见上方说明。')
    def students(status: str = '', q: str = ''):
        rows = []
        snapshot=store.snapshot(); active=snapshot.get('active_cohort')

        for sid,s in snapshot['students'].items():
            if s.get('cohort', active) != active:
                continue
            source,result = Store.current(s)
            state = 'processing' if Store.processing(snapshot,sid) else Store.status(s)
            if (not status or status == state) and q.casefold() in sid.casefold():
                rows.append(dict(id=sid,status=state,source=source,result=result,revision=len(s['history'])))
        return sorted(rows,key=lambda r:r['id'])

    @app.get('/api/v1/students/{sid}', tags=['学生与原图'], summary='查询学生完整记录', description='sid 为学号（保留前导零）。返回 sources 原图版本、results 处理版本、history 操作记录、delivered 交付事实及 revision。不存在返回 404。')
    def student(sid: str):
        s = store.snapshot()['students'][sid]
        return dict(s,status=Store.status(s),revision=len(s['history']))

    @app.post('/api/v1/roster', tags=['学生与原图'], summary='追加学号名单', description='提交 student_ids 字符串数组，例如 {"student_ids":["001","002"]}。已有学号不会重复添加。返回名单登记结果。')
    def roster(body: Ids):
        return store.roster(body.student_ids, store.snapshot().get('active_cohort'))

    @app.post('/api/v1/students/{sid}/photos', tags=['学生与原图'], summary='上传学生原始照片', description='sid 必须已登记。使用 multipart/form-data，文件字段必须名为 photo，最大 30MB。同一原图重复上传不会清除审核记录；新原图保留旧版本。返回原图信息。')
    def upload(sid: str, photo: UploadFile = File(...)):
        return store.upload(sid,photo.file.read(30*1024*1024+1))

    @app.get('/api/v1/config', tags=['处理配置'], summary='读取默认和已保存配置', description='返回 defaults 和 saved 两个对象。配置项中文含义见下方参数对照。')
    def config():
        return dict(defaults=asdict(PipelineOptions()),saved=store.snapshot()['config'])

    @app.put('/api/v1/config', tags=['处理配置'], summary='校验并保存处理配置', description='提交配置 JSON 对象，返回完整配置。未提供的字段使用系统默认值，不是与旧配置合并。未知字段、错误类型或非法取值返回 409。保存不自动重做照片。')
    def save_config(body: dict):
        config = asdict(service.options(body))
        store.change('保存配置',lambda d:d.update(config=config))
        return config

    @app.get('/api/v1/engines/hivision/urls', tags=['处理配置'], summary='读取历史 Hivision 地址')
    def engine_urls():
        return store.snapshot().get('hivision_urls', [])

    @app.post('/api/v1/engines/hivision/urls', tags=['处理配置'], summary='添加历史 Hivision 地址', description='提交 url 字符串。保存在当前工作区，重复地址只保留一条；不测试连接、不修改处理配置。')
    def save_engine_url(body: dict):
        from urllib.parse import urlsplit
        url = body.get('url')
        if not isinstance(url, str):
            raise ValueError('请填写 HTTP 或 HTTPS API 地址')
        url = url.strip().rstrip('/')
        try:
            parts = urlsplit(url)
            valid = parts.scheme in {'http', 'https'} and parts.hostname and not parts.username and not parts.password and not parts.query and not parts.fragment
            parts.port
        except ValueError:
            valid = False
        if not valid or any(c.isspace() for c in url):
            raise ValueError('请输入有效的 HTTP 或 HTTPS 地址，不含账号密码、查询参数或片段')
        def update(d):
            urls = d.setdefault('hivision_urls', [])
            if url not in urls:
                urls.append(url)
            return urls

    @app.delete('/api/v1/engines/hivision/urls', tags=['处理配置'], summary='删除历史 Hivision 地址')
    def delete_engine_url(url: str):
        d=store.snapshot(); urls=d.setdefault('hivision_urls', [])
        if url in urls:
            urls.remove(url); store.save(d)
        return urls
        return store.change('保存 Hivision 地址', update)

    @app.post('/api/v1/engines/hivision/test', tags=['处理配置'], summary='测试 Hivision 服务', description='提交 {"url":"http://127.0.0.1:8080"}。只检查接口能力，不上传照片；返回服务检测结果。')
    def test_api(body: dict):
        return test_hivision_api(str(body['url']),15)

    @app.post('/api/v1/processing-jobs', tags=['任务与预览'], summary='预览计划或启动批处理', description='dry_run=true 返回 items、config、config_id、revision；items 的 execute 表示是否执行，reason 解释跳过原因。正式执行设置 dry_run=false，并把计划 revision 传入 expected_revision。返回任务记录，使用 GET /jobs 跟踪。默认跳过已通过、已交付及相同参数已有结果的照片。')
    def process(body: Processing):
        if body.dry_run:
            return service.plan(body.student_ids,body.config,body.new_version)
        return service.start(body.student_ids,body.config,body.new_version,body.expected_revision)

    @app.post('/api/v1/students/{sid}/preview', tags=['任务与预览'], summary='试处理单张照片', description='sid 为学号；请求体为配置对象（同 PUT /config）。返回 status、stages、reasons、artifact。试处理写入预览图片，但不成为正式成片，不改变审核状态。没有原图返回 409。')
    def preview(sid: str, body: dict):
        source,_ = Store.current(store.snapshot()['students'][sid])
        if not source:
            raise ValueError('没有原图')
        return service.execute(source,body)

    @app.get('/api/v1/jobs', tags=['任务与预览'], summary='查看任务进度', description='返回任务数组，最新在前；id 为任务标识，status 为状态，completed 为已完成学号，errors 为失败明细，current 为当前学号。completed 状态表示任务结束，不代表每张均成功。')
    def jobs():
        return list(store.snapshot()['jobs'].values())[::-1]

    @app.post('/api/v1/jobs/{job_id}/{action}', tags=['任务与预览'], summary='暂停、恢复或中断任务', description='job_id 为任务标识；action 可填 pause（暂停）、resume（继续未完成项）、cancel（安全中断）。暂停和中断在当前学生结束后生效。返回任务记录；不允许的操作返回 409。')
    def control(job_id: str, action: str):
        return service.control(job_id,action)

    @app.post('/api/v1/reviews', tags=['审核与交付'], summary='审核具体成片版本', description='decision：approved 通过、rejected 退回、pending 撤回审核。result_id 必须是当前成片版本；expected_revision 使用学生详情中的 revision，冲突返回 409，刷新后重试。返回更新后的学生记录。')
    def review(body: Review):
        return store.review(body.student_id,body.result_id,body.decision,body.expected_revision,body.reason)

    @app.post('/api/v1/reviews/batch', tags=['审核与交付'], summary='批量审核当前届学生')
    def batch_review(body: BatchReview):
        if body.decision not in {'approved','rejected'}:
            raise ValueError('批量审核只支持通过或退回')
        d=store.snapshot(); active=d.get('active_cohort'); results=[]
        for sid in dict.fromkeys(body.student_ids):
            s=d['students'].get(sid)
            if not s or s.get('cohort',active)!=active:
                raise ValueError(sid+' 不属于当前届次')
            if Store.status(s) != 'review':
                raise ValueError(sid+' 不是待人工审核状态')
            _, result=Store.current(s)
            if not result or not result.get('artifact'):
                raise ValueError(sid+' 没有可审核成片')
            results.append((sid,result['id'],len(s['history'])))
        for sid,result_id,revision in results:
            store.review(sid,result_id,body.decision,revision,body.reason)
        return {'count':len(results),'decision':body.decision}

    @app.post('/api/v1/students/{sid}/replacement', tags=['审核与交付'], summary='启动照片替换流程', description='sid 为学号；提交 {"reason":"更换照片"}。保留历史交付事实，允许后续重新处理。未结束任务或待交付包会阻止替换。返回 ok。')
    def replacement(sid: str, body: Reason):
        service.replacement(sid,body.reason)
        return {'ok':True}

    @app.get('/api/v1/deliveries', tags=['审核与交付'], summary='查询交付批次', description='返回批次数组：prepared 待交付、delivered 已实际交付、cancelled 已取消。historical=true 为历史登记批次，不提供照片包下载。')
    def deliveries():
        return list(store.snapshot()['deliveries'].values())[::-1]

    @app.post('/api/v1/deliveries', tags=['审核与交付'], summary='生成照片交付包', description='提交 student_ids。仅接收审核通过且不在其他待交付包中的学生。返回 prepared 批次及 id；此操作尚未标记实际交付。ZIP 内含学号命名照片与 manifest.json。')
    def deliver(body: Ids):
        return service.delivery(body.student_ids)

    @app.post('/api/v1/historical-deliveries', tags=['审核与交付'], summary='登记历史已交付名单', description='提交学号名单、交付依据 reason，并设置 confirmed_sent=true。只记录历史交付事实，不把当前成片标为审核通过。返回历史批次；缺少的学号自动加入名单；校验失败时整批不保存。')
    def historical(body: Historical):
        if not body.confirmed_sent:
            raise ValueError('必须确认名单对应实际已发送的照片')
        return service.historical_delivery(body.student_ids,body.reason)

    @app.post('/api/v1/deliveries/{batch_id}/confirm', tags=['审核与交付'], summary='确认照片已经实际交付', description='batch_id 为交付批次。请在真实发送照片之后调用，返回更新后的批次。重复确认已交付批次返回原批次。')
    def confirm(batch_id: str):
        return service.confirm_delivery(batch_id)

    @app.post('/api/v1/deliveries/{batch_id}/cancel', tags=['审核与交付'], summary='取消尚未交付的包', description='batch_id 为批次标识。仅 prepared 批次可取消，成功返回 ok；已交付批次返回 409。')
    def cancel_delivery(batch_id: str):
        def update(d):
            batch = d['deliveries'][batch_id]
            if batch['status'] != 'prepared':
                raise ValueError('只能取消尚未交付的包')
            batch['status'] = 'cancelled'
        store.change('取消交付包',update)
        return {'ok':True}

    @app.get('/api/v1/deliveries/{batch_id}/download', tags=['审核与交付'], summary='下载交付照片 ZIP', description='batch_id 为非历史批次标识。返回二进制 ZIP 附件；已取消批次返回 409。')
    def download(batch_id: str):
        batch = store.snapshot()['deliveries'][batch_id]
        if batch['status'] == 'cancelled':
            raise ValueError('交付包已取消')
        return FileResponse(store.file(f'deliveries/{batch_id}/photos.zip'),filename=f'学生照片_{batch_id[:8]}.zip')

    @app.get('/api/v1/artifacts/{sha}', tags=['图片与事件'], summary='读取原图或处理图片', description='sha 为图片内容的 64 位 SHA-256 标识（artifact.id）。返回图片二进制，使用响应 Content-Type 判断格式。')
    def artifact(sha: str):
        import re
        if not re.fullmatch('[0-9a-f]{64}',sha):
            raise ValueError('图片标识无效')
        paths = list((store.root/'artifacts'/sha[:2]).glob(sha+'.*'))
        if not paths:
            raise ValueError('图片不存在')
        return FileResponse(paths[0])

    @app.get('/api/v1/events', tags=['图片与事件'], summary='订阅工作区变化', description='返回 text/event-stream 长连接。数据形如 data: {"revision":12}，表示工作区修订号变化；收到后重新查询学生或任务接口。')
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

    @app.post('/api/v1/imports/zip', tags=['导入与迁移'], summary='检查或导入采集系统照片压缩包', description='multipart/form-data：file 为 ZIP，压缩包名称不限，内部图片为 学号-姓名.扩展名；inspect_only 默认 true 只检查，false 创建可暂停和恢复的后台任务。自动补入学号，重复照片不增加版本。最多500MB，解压总大小2GB，单张30MB；重复学号或不合法文件名拒绝导入。')
    def import_zip(file: UploadFile = File(...), cohort: str = Form(''), inspect_only: bool = Form(True)):
        import zipfile
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'input.zip'
            with path.open('wb') as output:
                size=0
                while chunk := file.file.read(1024*1024):
                    size+=len(chunk)
                    if size>500*1024**2:
                        raise ValueError('压缩包超过500MB')
                    output.write(chunk)
            try:
                rows=service.inspect_zip(path)
            except zipfile.BadZipFile:
                raise ValueError('不是有效的ZIP压缩包')
            if inspect_only:
                return dict(count=len(rows),student_ids=[r['student_id'] for r in rows])
            if cohort.strip(): store.set_cohort(cohort.strip())
            return service.start_zip(path,rows)

    @app.post('/api/v1/imports/xlsx', tags=['导入与迁移'], summary='检查 Excel 或创建原图接收任务', description='multipart/form-data：file 为 .xlsx（最大 50MB）；sheet 留空取首张；header_row 表头行从 1 开始；id_column 学号列默认 A；image_column 图片列默认 B。inspect_only=true 每次按表头识别列，未识别的列默认 A/B，返回 summary、headers、sheet、id_column、image_column；false 严格使用所选列并创建后台导入任务。照片链接须仍有效。')
    def import_xlsx(file: UploadFile = File(...), sheet: str = Form(''), header_row: int = Form(1), id_column: str = Form('A'), image_column: str = Form('B'), cohort: str = Form(''), inspect_only: bool = Form(True)):
        from xlsx_photo_core import WorkbookReader, inspect_selection, column_index, column_label, suggest_columns, _fetch_source
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
                headers = reader.headers(name, header_row)
                detected_cohort = detect_cohort(reader, name, header_row, headers)
            if inspect_only:
                id_col, image_col = suggest_columns(headers + [''] * max(0, 2-len(headers)))
            else:
                id_col, image_col = column_index(id_column or 'A'), column_index(image_column or 'B')
            if id_col == image_col:
                raise ValueError('学号列和图片列不能是同一列，请手动选择后接收原始图片')
            report=inspect_selection(path,name,header_row,id_col,image_col)
            if inspect_only:
                return dict(summary=report.summary,headers=report.headers,sheet=name,
                            id_column=column_label(id_col),image_column=column_label(image_col),cohort=detected_cohort)
            if report.summary['duplicate_count'] or report.summary['empty_ids']:
                raise ValueError('请先修正空学号或重复学号')
            target_cohort = (cohort or detected_cohort or store.snapshot().get('active_cohort')).strip()
            store.set_cohort(target_cohort)
            return service.start_import(raw,name,header_row,id_col,image_col,report.rows)

    @app.post('/api/v1/migrations/legacy', tags=['导入与迁移'], summary='检查或迁移旧版工作区', description='提交 {"path":"D:\\\\旧版\\\\导出结果","apply":false} 先检查；apply=true 才复制迁移，目标须符合空工作区要求。path 是运行服务的电脑路径。返回迁移报告；不覆盖旧数据。')
    def migrate(body: dict):
        from .migration import migrate_legacy
        return migrate_legacy(store,body['path'],bool(body.get('apply',False)))

    app.mount('/',StaticFiles(directory=Path(__file__).parent/'web',html=True),name='web')
    return app
