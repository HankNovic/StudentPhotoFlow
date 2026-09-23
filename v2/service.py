import hashlib
import json
import math
import re
import copy
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from contextlib import nullcontext
from .hivision import HivisionRequests
from dataclasses import asdict
from pathlib import Path

from photo_pipeline import PipelineOptions, run_pipeline, validate_pipeline_options
from .store import Store, atomic, now
from .export_profiles import default_profile, preview as preview_names, validate_profile


class Service:
    def __init__(self, store, requests=None):
        self.store = store
        self.requests = requests or HivisionRequests(store.snapshot()["config"].get("hivision_concurrency",1))
        self._ticks = {}
        self.thread = None
        self.gate = threading.Lock()
        # Additive task semantics upgrade; preserve a pre-upgrade index, never reset data.
        if store.data.get('task_semantics',1)<2:
            backup=store.root/'backups'/'before-task-semantics-2.json'
            if not backup.exists(): atomic(backup,store.snapshot())
        if store.data.get('timing_schema',0)<1:
            backup=store.root/'backups'/'before-task-timing-1.json'
            if not backup.exists(): atomic(backup,store.snapshot())
        def recover(d):
            d['timing_schema']=1
            d['task_semantics']=2
            for job in d['jobs'].values():
                job.setdefault('uncertain',{})
                job.setdefault('skipped',[])
                flight=set(job.get('in_flight',[])) | ({job['current']} if job.get('current') else set())
                for sid in flight-set(job['completed']):
                    job['uncertain'][sid]='服务中断时正在执行，外部服务结果尚未核对；不会自动重试'
                timing=job.get('timing')
                if timing and timing.get('running'):
                    timing.update(running=False,lower_bound=True)
                job['in_flight']=[]
                if job['status'] in {'cancelled','cancelling'}:
                    job.update(status='cancelled',end_reason='manual',current=None)
                elif job['status'] in {'running','pausing','queued'}:
                    job.update(status='interrupted',end_reason='system',current=None)
                elif job['status']=='completed': job.setdefault('end_reason','natural')
        store.change('启动与任务进度恢复', recover)

    @staticmethod
    def safe_error(value):
        text=str(value)
        text=re.sub(r'(?i)(bearer\s+)[^\s"\',}]+',r'\1[已隐藏]',text)
        text=re.sub(r'(?i)(["\']?(?:token|api[_-]?key|password|secret|authorization)["\']?\s*[:=]\s*)(?:"[^"\n]*"|\'[^\'\n]*\'|[^\s&,}]+)',r'\1[已隐藏]',text)
        text=re.sub(r'(https?://)[^/@\s]+:[^/@\s]+@',r'\1[已隐藏]@',text)
        text='\n'.join(line for line in text.splitlines() if not line.lstrip().startswith(('Traceback','File "','at ')))
        return text[:6000]

    def _clock(self, jid, stop=False):
        """Monotonic active intervals; persisted heartbeat never spans a stopped process."""
        with self.store.lock:
            j=self.store.data['jobs'][jid]
            t=j.get('timing')
            if not t or jid not in self._ticks: return
            mono=time.monotonic(); delta=max(0,mono-self._ticks[jid])
            stamp=now()
            def update(d):
                timing=d['jobs'][jid]['timing']
                timing.update(run_seconds=round(timing.get('run_seconds',0)+delta,6),observed_at=stamp,running=not stop)
            self.store.checkpoint(update)
            if stop: self._ticks.pop(jid,None)
            else: self._ticks[jid]=mono

    def _begin(self,jid):
        with self.store.lock:
            self._ticks[jid]=time.monotonic()
            def save(d):
                j=d['jobs'][jid]; stamp=now()
                # Historical jobs cannot acquire an invented historical start time.
                if 'timing' not in j: j['timing']={'started_at':None,'ended_at':None,'run_seconds':0,'partial':True}
                t=j['timing']
                if t.pop('new',False): t['started_at']=stamp
                t.update(running=True,observed_at=stamp)
            self.store.change('开始任务执行计时',save)

    def _worker(self,jid,target):
        with self.store.lock:
            if self.store.data['jobs'][jid]['status'] in {'completed','cancelled'}: return
            self._begin(jid)
        done=threading.Event()
        def pulse():
            while not done.wait(1): self._clock(jid)
        timer=threading.Thread(target=pulse,daemon=True);timer.start()
        try: target(jid)
        finally:
            done.set();timer.join();self._clock(jid,stop=True)

    @staticmethod
    def _new_timing():
        return dict(new=True,started_at=None,ended_at=None,run_seconds=0,running=False)

    def job_view(self, job):
        job=copy.deepcopy(job)
        job['errors']={sid:self.safe_error(msg) for sid,msg in job.get('errors',{}).items()}
        if job.get('message'): job['message']=self.safe_error(job['message'])
        total=sum(bool(i.get('execute',True)) for i in job['plan']['items'])
        skipped=set(job.get('skipped',[])); failed=set(job['errors']) & set(job['completed'])
        job['counts']=dict(total=total,success=len(set(job['completed'])-failed-skipped),failed=len(failed),
                           skipped=len(skipped),remaining=max(0,total-len(set(job['completed']))),uncertain=len(job.get('uncertain',{})))
        t=job.get('timing');job['server_now']=now()
        tick=self._ticks.get(job['id'])
        if t and t.get('running') and tick is not None:
            t['run_seconds']+=max(0,time.monotonic()-tick)
            t['observed_at']=job['server_now']
        return job

    def reconcile(self,jid,sid,decision):
        if decision not in {'retry','skip'}: raise ValueError('核对后请选择重试或不再执行')
        with self.gate, self.store.lock:
            job=self.store.data['jobs'][jid]
            if job['status'] not in {'interrupted','cancelled'} or sid not in job.get('uncertain',{}): raise ValueError('当前项目不处于可核对恢复状态')
            if job['status']=='cancelled' and decision!='skip': raise ValueError('手动结束任务仅可核对归档，不可重试或继续')
            def save(d):
                j=d['jobs'][jid];j['uncertain'].pop(sid)
                j.setdefault('reconciliations',[]).append(dict(student_id=sid,decision=decision,at=now()))
                if decision=='skip': j['skipped'].append(sid);j['completed'].append(sid)
            self.store.change('核对中断项目 '+sid+' '+decision,save)
        return {'ok':True}

    def options(self, values):
        defaults=asdict(PipelineOptions())
        for key,value in values.items():
            if key not in defaults:
                raise ValueError('未知配置项：'+key)
            default=defaults[key]
            if isinstance(default,bool):
                valid=isinstance(value,bool)
            elif isinstance(default,int):
                valid=type(value) is int
            elif isinstance(default,float):
                valid=type(value) in (int,float) and math.isfinite(value)
            else:
                valid=isinstance(value,str)
            if not valid:
                raise ValueError('配置值类型错误：'+key)
        options = PipelineOptions(**values)
        if not 1 <= options.hivision_concurrency <= 16:
            raise ValueError('Hivision 请求并发数必须为 1–16 的整数')
        validate_pipeline_options(options)
        return options

    def plan(self, ids, config, new_version=False):
        options = self.options(config)
        d = self.store.snapshot()
        image_config=asdict(options);image_config.pop('hivision_concurrency',None)
        fingerprint = hashlib.sha256(json.dumps(image_config, sort_keys=True).encode()).hexdigest()
        rows = []
        for sid in list(dict.fromkeys(ids)):
            s = d['students'].get(sid)
            if not s:
                raise ValueError('未知学号：'+sid)
            status = Store.status(s)
            source, r = Store.current(s)
            reason = None
            if s['delivered'] and not s['replacement']:
                reason = '已有交付记录；需启动替换流程'
            elif Store.processing(d,sid):
                reason = '已有未完成任务，请继续原任务或先安全结束'
            elif any(b['status']=='prepared' and any(x['student_id']==sid for x in b['items']) for b in d['deliveries'].values()):
                reason = '已在待交付包中；请先完成或取消交付'
            elif any(sid in j.get('uncertain',{}) for j in d['jobs'].values()):
                reason = '有结果不确定的中断项目，请先在任务进度核对'
            elif status == 'approved' and not new_version:
                reason = '当前照片已审核通过'
            elif not source:
                reason = '没有原图'
            elif r and r['config_id'] == fingerprint and r['status'] in {'success', 'warning'} and not new_version:
                reason = '同一原图和参数已有结果'
            rows.append(dict(student_id=sid, execute=reason is None, reason=reason, source_id=source['id'] if source else None))
        return dict(items=rows, config=asdict(options), config_id=fingerprint, revision=d['revision'])

    def start(self, ids, config, new_version=False, expected_revision=None, request_id=None):
        with self.gate, self.store.lock:
            if request_id:
                old=next((j for j in self.store.data['jobs'].values() if j.get('request_id')==request_id),None)
                if old:
                    if set(ids)!={x['student_id'] for x in old['plan']['items']} or asdict(self.options(config))!=asdict(self.options(old['plan']['config'])):
                        raise ValueError('请求编号已用于不同参数或学生')
                    return old
            if self.thread and self.thread.is_alive():
                raise ValueError('已有任务运行中，请暂停或中断后操作')
            plan = self.plan(ids, config, new_version)
            if not any(i['execute'] for i in plan['items']):
                return dict(status='skipped',plan=plan)
            if expected_revision is not None and plan['revision'] != expected_revision:
                raise ValueError('执行计划已变化，请重新预览')
            job_id = uuid.uuid4().hex
            job = dict(id=job_id, status='running', uncertain={}, skipped=[], created_at=now(), plan=plan, completed=[], errors={}, current=None,request_id=request_id,timing=self._new_timing(),in_flight=[])
            self.store.change('创建处理任务', lambda d: d['jobs'].update({job_id: job}))
            self._launch(job_id)
            return job

    def _launch(self, job_id):
        job=self.store.snapshot()['jobs'][job_id]
        target=(self._run_zip if job.get('format')=='zip' else self._run_import) if job.get('kind')=='import' else self._run
        self.thread = threading.Thread(target=self._worker, args=(job_id,target), daemon=True)
        self.thread.start()

    @staticmethod
    def inspect_zip(path):
        import re
        from .store import valid_id
        rows=[]
        seen=set()
        with zipfile.ZipFile(path) as archive:
            entries=archive.infolist()
            if len(entries)>10000 or sum(e.file_size for e in entries)>2*1024**3:
                raise ValueError('压缩包超过10000项或解压大小超过2GB')
            for entry in entries:
                name=entry.filename
                parts=name.replace('\\','/').split('/')
                if name.startswith(('/', '\\')) or '..' in parts or ':' in name:
                    raise ValueError('压缩包包含不安全路径')
                if entry.is_dir():
                    continue
                if entry.flag_bits & 1:
                    raise ValueError('不支持加密压缩包')
                if entry.file_size>30*1024**2:
                    raise ValueError('单张图片超过30MB')
                match=re.fullmatch(r'([0-9]+)-(.+)\.(png|jpe?g|webp|bmp|gif|tiff?)',parts[-1],re.IGNORECASE)
                if not match:
                    raise ValueError('图片文件名须为 学号-姓名.图片扩展名')
                sid=valid_id(match[1])
                if sid in seen:
                    raise ValueError('压缩包中存在重复学号：'+sid)
                seen.add(sid)
                rows.append(dict(student_id=sid,entry=name,execute=True,reason=None))
        if not rows:
            raise ValueError('压缩包内没有照片')
        return rows

    def start_zip(self, path, rows):
        with self.gate, self.store.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('已有任务运行中，请先中断或等待完成')
            jid=uuid.uuid4().hex
            relative='inputs/'+jid+'.zip'
            destination=self.store.root/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            import shutil
            shutil.copyfile(path,destination)
            job=dict(id=jid,kind='import',format='zip',status='running',uncertain={},skipped=[],created_at=now(),completed=[],errors={},current=None,input_file=relative,plan={'items':rows},timing=self._new_timing(),in_flight=[])
            def update(d):
                Store.add_roster(d,[row['student_id'] for row in rows])
                d['jobs'][jid]=job
            self.store.change('创建压缩包原图接收任务',update)
            self._launch(jid)
            return job

    def _run_zip(self,jid):
        try:
            job=self.store.snapshot()['jobs'][jid]
            with zipfile.ZipFile(self.store.file(job['input_file'])) as archive:
                for row in job['plan']['items']:
                    sid=row['student_id']
                    if sid in job['completed']:
                        continue
                    if not self._await_running(jid,sid):
                        return
                    error=None
                    try:
                        if self.store.snapshot()['students'][sid].get('delivered'):
                            raise ValueError('已交付学生已跳过，不重新导入')
                        with archive.open(row['entry']) as image:
                            self.store.upload(sid,image.read(30*1024**2+1))
                    except Exception as exc:
                        error='接收原图：'+self.safe_error(exc)
                    def checkpoint(d):
                        self._item_done(d['jobs'][jid],sid)
                        if error:
                            d['jobs'][jid]['errors'][sid]=error
                    self.store.change('压缩包接收检查点',checkpoint)
            self._finish(jid)
        except Exception as exc:
            self._interrupted(jid,exc)

    def start_import(self, raw, sheet, header_row, id_col, image_col, rows, name_col=None):
        with self.gate, self.store.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('已有任务运行中，请先中断或等待完成')
            relative='inputs/'+hashlib.sha256(raw).hexdigest()+'.xlsx'
            atomic(self.store.root/relative,raw)
            jid=uuid.uuid4().hex
            job=dict(id=jid,kind='import',status='running',uncertain={},skipped=[],created_at=now(),completed=[],errors={},current=None,
                     timing=self._new_timing(),in_flight=[],input_file=relative,sheet=sheet,header_row=header_row,id_col=id_col,image_col=image_col,name_col=name_col,
                     plan={'items':[dict(student_id=r.student_id,name=r.name,execute=True,reason=None) for r in rows]})
            def accept(d):
                Store.add_roster(d, [r.student_id for r in rows])
                for row in rows:
                    if row.name:
                        d['students'][row.student_id]['name'] = row.name
                d['jobs'][jid] = job
            # Metadata is accepted with the roster, independently of photo/processing state.
            self.store.change('创建原图接收任务并保存姓名',accept)
            self._launch(jid)
            return job

    def _await_running(self,jid,sid):
        with self.store.lock:
            j=self.store.data['jobs'][jid]
            if j['status']!='running':
                if not j.get('in_flight'): self._settle(jid)
                return False
            def dispatch(d):
                j=d['jobs'][jid];j.setdefault('in_flight',[]).append(sid);j['current']=sid
            self.store.change('开始执行 '+sid,dispatch)
            return True

    @staticmethod
    def _item_done(job,sid):
        if sid not in job['completed']: job['completed'].append(sid)
        job['in_flight']=[x for x in job.get('in_flight',[]) if x!=sid]
        job['current']=next(iter(job['in_flight']),None)

    def _settle(self,jid,natural=False):
        with self.store.lock:
            j=self.store.data['jobs'][jid]
            if j.get('in_flight'): return
            if j['status'] in {'completed','cancelled','interrupted','paused'}: return
            manual=j['status']=='cancelling'
            if not manual and j['status']!='pausing' and not natural: return
            self._clock(jid,stop=True)
            def save(d):
                j=d['jobs'][jid];j['current']=None
                if not manual and j['status']=='pausing': j['status']='paused'
                else:
                    j.update(status='cancelled' if manual else 'completed',end_reason='manual' if manual else 'natural')
                    if j.get('timing'): j['timing']['ended_at']=now()
            self.store.change('任务收尾',save)

    def _finish(self,jid): self._settle(jid,natural=True)

    def _interrupted(self,jid,exc):
        self._clock(jid,stop=True)
        def save(d):
            j=d['jobs'][jid]
            for sid in j.get('in_flight',[]):
                if sid not in j['completed']: j.setdefault('uncertain',{})[sid]='本地记录未完成，执行结果需核对'
            manual=j['status'] in {'cancelling','cancelled'}
            j.update(status='cancelled' if manual else 'interrupted',end_reason='manual' if manual else 'system',message=self.safe_error(exc),current=None,in_flight=[])
            if j.get('timing'): j['timing']['lower_bound']=True
        self.store.change('任务异常',save)

    def _run_import(self,jid):
        from xlsx_photo_core import inspect_selection,_fetch_source
        try:
            job=self.store.snapshot()['jobs'][jid]
            report=inspect_selection(self.store.file(job['input_file']),job['sheet'],job['header_row'],job['id_col'],job['image_col'],job.get('name_col'))
            for row in report.rows:
                sid=row.student_id
                if sid in job['completed']:
                    continue
                if not self._await_running(jid,sid):
                    return
                error=None
                try:
                    student = self.store.snapshot()['students'][sid]
                    if student.get('delivered'):
                        raise ValueError('已交付学生已跳过，不重新导入')
                    if row.source.kind=='file':
                        raise ValueError('本地路径请使用图片上传入口')
                    self.store.upload(sid,_fetch_source(row.source,25))
                except Exception as exc:
                    error='接收原图：'+self.safe_error(exc)
                def checkpoint(d):
                    self._item_done(d['jobs'][jid],sid)
                    if error:
                        d['jobs'][jid]['errors'][sid]=error
                self.store.change('原图接收检查点',checkpoint)
            self._finish(jid)
        except Exception as exc:
            self._interrupted(jid,exc)

    def control(self, job_id, action):
        with self.gate, self.store.lock:
            job=self.store.data['jobs'][job_id];status=job['status']
            if (action=='cancel' and status in {'cancelling','cancelled'}) or (action=='pause' and status in {'pausing','paused'}):
                return {'status':status}
            if action=='pause' and status=='running': target='pausing'
            elif action=='cancel' and status in {'running','pausing','paused','interrupted'}:
                target='cancelling'
            elif action=='resume' and status in {'paused','interrupted'}:
                if job.get('kind')=='delivery': raise ValueError('打包中断请重新生成交付包；原记录保留，不重做照片')
                if job.get('uncertain'): raise ValueError('请先核对结果不确定的学生，确认重试或不再执行')
                if self.thread and self.thread.is_alive(): raise ValueError('请等待当前任务停下后继续')
                target='running'
            else: raise ValueError('当前状态不能执行该操作；已安全结束的任务不可继续')
            def save(d):
                j=d['jobs'][job_id];j['status']=target
                if action=='cancel':j['end_reason']='manual'
                if action=='resume':j.pop('end_reason',None)
            self.store.change('任务 '+action,save)
            if action in {'pause','cancel'} and not job.get('in_flight') and not job.get('current'): self._settle(job_id)
            if action=='resume': self._launch(job_id)
            return {'status':target}

    def _run_item(self,job_id,item,job,reserved=False):
        sid=item['student_id'];config=job['plan']['config']
        slot=self.requests.slot(config['hivision_url'],reserved=True) if reserved else nullcontext()
        with slot:
            try:
                s = self.store.snapshot()['students'][sid]
                source, _ = Store.current(s)
                if source['id'] != item['source_id'] or (s['delivered'] and not s['replacement']):
                    raise ValueError('原图或交付状态已变化，请重新创建任务')
                result = self.execute(source, job['plan']['config'])
                result.update(id=uuid.uuid4().hex, at=now(), source_id=source['id'], config_id=job['plan']['config_id'], config=job['plan']['config'], review='pending')
                def save(d):
                    student = d['students'][sid]
                    student['results'].append(result)
                    if result['status'] not in {'success','warning'}:
                        d['jobs'][job_id]['errors'][sid]='预检/处理：'+self.safe_error('；'.join(str(x) for x in result.get('reasons',[])) or result['status'])
                    student['approved'] = None
                    student['history'].append(dict(at=now(), action='完成处理', result_id=result['id']))
                    self._item_done(d['jobs'][job_id],sid)
                self.store.change('保存成片 '+sid, save)
            except Exception as exc:
                message = ('Hivision 处理：' if job['plan']['config'].get('background_mode')=='hivision' else '照片处理：')+self.safe_error(exc)
                def fail(d):
                    d['jobs'][job_id]['errors'][sid] = message
                    self._item_done(d['jobs'][job_id],sid)
                    d['students'][sid]['approved'] = None
                    d['students'][sid]['results'].append(dict(id=uuid.uuid4().hex, at=now(), source_id=item['source_id'], config_id=job['plan']['config_id'], status='failed', message=message, stages=[], artifact=None))
                    d['students'][sid]['history'].append(dict(at=now(),action='处理失败',message=message))
                self.store.change('处理失败 '+sid, fail)

    def _run(self,job_id):
        try:
            job=self.store.snapshot()['jobs'][job_id];config=job['plan']['config']
            pending=[i for i in job['plan']['items'] if i['execute'] and i['student_id'] not in job['completed']]
            external=config.get('background_mode')=='hivision'
            key=self.requests.key(config['hivision_url']) if external else None
            # At most sixteen live workers; never queue undispatched students in the executor.
            with ThreadPoolExecutor(max_workers=16 if external else 1) as pool:
                futures=set()
                while pending or futures:
                    with self.store.lock:
                        running=self.store.data['jobs'][job_id]['status']=='running'
                        if running and pending and len(futures)<(16 if external else 1):
                            acquired=self.requests.acquire(key) if external else True
                            if acquired:
                                item=pending.pop(0)
                                try:
                                    self._await_running(job_id,item['student_id'])
                                    futures.add(pool.submit(self._run_item,job_id,item,job,external))
                                except BaseException:
                                    if external:self.requests.release(key)
                                    raise
                                continue
                    if not running and not futures:break
                    if futures:
                        done,futures=wait(futures,timeout=.1,return_when=FIRST_COMPLETED)
                        for f in done:f.result()
                    else:time.sleep(.05)
            self._finish(job_id)
        except Exception as exc:self._interrupted(job_id,exc)

    def execute(self,source,config):
        options=self.options(config)
        slot=self.requests.slot(options.hivision_url) if options.background_mode=='hivision' else nullcontext()
        with slot: result=run_pipeline(self.store.file(source['file']).read_bytes(),options)
        stages=[dict(code=s.code,label=s.label,status=s.status,detail=s.detail,metrics=s.metrics,artifact=self.store.artifact(s.image_bytes)) for s in result.stages]
        return dict(status=result.status,stages=stages,reasons=result.reasons,artifact=self.store.artifact(result.output_bytes) if result.output_bytes else None)

    def historical_delivery(self, ids, reason, cohort=None):
        if not reason.strip():
            raise ValueError('请注明历史交付依据')
        ids = list(dict.fromkeys(x.strip() for x in ids))
        def update(d):
            Store.add_roster(d, ids, cohort)
            batch_id='legacy-'+uuid.uuid4().hex
            entries=[]
            for sid in dict.fromkeys(ids):
                s=d['students'][sid]
                if s['delivered']:
                    continue
                if Store.processing(d,sid) or any(b['status']=='prepared' and any(x['student_id']==sid for x in b['items']) for b in d['deliveries'].values()):
                    raise ValueError(sid+' 有未完成处理任务或待交付包，请先完成或取消')
                source,_=Store.current(s)
                source_id=source['id'] if source else None
                s['delivered']=dict(batch_id=batch_id,result_id=None,source_id=source_id,at=now(),evidence=reason)
                s['history'].append(dict(at=now(),action='登记历史已交付',reason=reason))
                entries.append(dict(student_id=sid,result_id=None,source_id=source_id))
            batch=dict(id=batch_id,status='delivered',at=now(),items=entries,historical=True,reason=reason)
            if entries:
                d['deliveries'][batch_id]=batch
            return batch
        return self.store.change('登记历史交付名单',update)

    def export_profile(self, profile_id=None, d=None):
        profiles=(d or self.store.snapshot()).setdefault('export_profiles',[default_profile()])
        if profile_id is None: return next((p for p in profiles if p.get('is_default') and p.get('status')=='active'),profiles[0])
        try: return next(p for p in profiles if p['id']==profile_id)
        except StopIteration: raise ValueError('导出格式不存在')

    def export_preview(self, ids, profile_id=None, cohort=None):
        d=self.store.snapshot(); profile=self.export_profile(profile_id,d)
        if profile.get('status')!='active': raise ValueError('该导出格式已停用，请先恢复使用')
        active=cohort or d.get('active_cohort'); students=[]
        for sid in dict.fromkeys(ids):
            s=d['students'].get(sid)
            if not s or s.get('cohort',active)!=active: raise ValueError(sid+' 不属于当前届次')
            if Store.status(s)!='approved': raise ValueError(sid+' 未审核通过')
            r=next((x for x in s['results'] if x['id']==s['approved']),None)
            if not r or not r.get('artifact'): raise ValueError(sid+' 没有可交付成片')
            students.append({'student_id':sid,'name':s.get('name'),'ext':Path(r['artifact']['file']).suffix})
        result=preview_names(profile,students)
        result.update(profile_id=profile['id'],profile_snapshot={'profile_id':profile['id'],'name':profile.get('name'),'template':profile.get('template'),'revision':profile.get('revision'),'rules':profile.get('rules')})
        return result

    def delivery(self, ids, profile_id=None):
        with self.store.lock:
            d = self.store.snapshot()
            profile=self.export_profile(profile_id,d)
            if profile.get('status')!='active': raise ValueError('该导出格式已停用，请先恢复使用')
            entries = []
            reserved = {x['student_id'] for batch in d['deliveries'].values() if batch['status'] == 'prepared' for x in batch['items']}
            for sid in dict.fromkeys(ids):
                s = d['students'].get(sid)
                if not s or s.get('cohort',d.get('active_cohort')) != d.get('active_cohort'):
                    raise ValueError(sid+' 不属于当前届次')
                if Store.processing(d,sid):
                    raise ValueError(sid+' 在未完成处理任务中')
                if Store.status(s) != 'approved' or sid in reserved:
                    raise ValueError(sid+' 未审核通过或已在待交付包中')
                r = next(r for r in s['results'] if r['id'] == s['approved'])
                artifact = r['artifact']
                if hashlib.sha256(self.store.file(artifact['file']).read_bytes()).hexdigest() != artifact['id']:
                    raise ValueError('成片完整性校验失败：'+sid)
                entries.append(dict(student_id=sid, name=s.get('name'), result_id=r['id'], source_id=r['source_id'], artifact=artifact, ext=Path(artifact['file']).suffix))
            if not entries:
                raise ValueError('请选择已通过的学生')
            names=preview_names(profile,[{'student_id':e['student_id'],'name':e.get('name'),'ext':e['ext']} for e in entries])
            if not names['ok']:
                raise ValueError('导出文件名检查未通过：'+ '; '.join(x['student_id']+' '+x['reason'] for x in names['items'] if x['status']=='error'))
            for e,row in zip(entries,names['items']): e['file_name']=row['file_name']
            snapshot={'profile_id':profile['id'],'name':profile.get('name'),'template':profile.get('template'),'revision':profile.get('revision'),'rules':profile.get('rules')}
            batch_id = uuid.uuid4().hex
            batch = dict(id=batch_id, at=now(), status='prepared', format_snapshot=snapshot, items=entries)
            jid=uuid.uuid4().hex
            task=dict(id=jid,kind='delivery',status='running',created_at=now(),completed=[],errors={},uncertain={},skipped=[],current=None,in_flight=[],timing=self._new_timing(),plan={'items':[dict(student_id=e['student_id'],execute=True) for e in entries]})
            self.store.change('创建交付打包任务',lambda d:d['jobs'].update({jid:task}))
            self._begin(jid)
            try:
                folder = self.store.root / 'deliveries' / batch_id
                for entry in entries:
                    art = entry['artifact']
                    atomic(folder / 'photos' / entry['file_name'], self.store.file(art['file']).read_bytes())
                atomic(folder / 'manifest.json', batch)
                with zipfile.ZipFile(folder / 'photos.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                    for path in sorted((folder / 'photos').iterdir()):
                        archive.write(path, path.name)
                    archive.write(folder / 'manifest.json', 'manifest.json')
                self.store.change('生成交付包', lambda d: d['deliveries'].update({batch_id:batch}))
                self.store.change('交付打包检查点',lambda d:d['jobs'][jid].update(completed=[e['student_id'] for e in entries]))
                return batch
            except Exception as exc:
                def failed(d):
                    j=d['jobs'][jid];j['message']=self.safe_error(exc)
                    for e in entries:j['errors'][e['student_id']]='交付打包：'+self.safe_error(exc);self._item_done(j,e['student_id'])
                self.store.change('交付打包失败',failed)
                raise
            finally: self._finish(jid)

    def confirm_delivery(self, batch_id):
        def update(d):
            batch = d['deliveries'][batch_id]
            if batch['status'] == 'delivered':
                return batch
            if batch['status'] != 'prepared':
                raise ValueError('此交付包已取消')
            for entry in batch['items']:
                s = d['students'][entry['student_id']]
                s['delivered'] = dict(batch_id=batch_id, result_id=entry['result_id'], source_id=entry['source_id'], at=now())
                s['replacement'] = False
                s['history'].append(dict(at=now(), action='确认实际交付', batch_id=batch_id))
            batch.update(status='delivered', confirmed_at=now())
            return batch
        return self.store.change('确认交付', update)

    def replacement(self, sid, reason):
        if not reason.strip():
            raise ValueError('请填写替换原因')
        def update(d):
            s = d['students'][sid]
            if Store.processing(d,sid):
                raise ValueError('请先完成或中断该学生的处理任务')
            if any(b['status']=='prepared' and any(x['student_id']==sid for x in b['items']) for b in d['deliveries'].values()):
                raise ValueError('请先取消此学生所在的待交付包')
            s['replacement'] = True
            s['approved'] = None
            s['history'].append(dict(at=now(), action='启动替换流程', reason=reason))
        self.store.change('替换 '+sid, update)
