import hashlib
import json
import math
import threading
import time
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path

from photo_pipeline import PipelineOptions, run_pipeline, validate_pipeline_options
from .store import Store, atomic, now


class Service:
    def __init__(self, store):
        self.store = store
        self.thread = None
        self.gate = threading.Lock()
        def recover(d):
            for job in d['jobs'].values():
                if job['status'] in {'running', 'pausing', 'paused', 'cancelling', 'queued'}:
                    job['status'] = 'interrupted'
        store.change('启动与中断恢复', recover)

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
        validate_pipeline_options(options)
        return options

    def plan(self, ids, config, new_version=False):
        options = self.options(config)
        d = self.store.snapshot()
        fingerprint = hashlib.sha256(json.dumps(asdict(options), sort_keys=True).encode()).hexdigest()
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
            elif any(b['status']=='prepared' and any(x['student_id']==sid for x in b['items']) for b in d['deliveries'].values()):
                reason = '已在待交付包中；请先完成或取消交付'
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
                    if set(ids)!={x['student_id'] for x in old['plan']['items']} or asdict(self.options(config))!=old['plan']['config']:
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
            job = dict(id=job_id, status='running', created_at=now(), plan=plan, completed=[], errors={}, current=None,request_id=request_id)
            self.store.change('创建处理任务', lambda d: d['jobs'].update({job_id: job}))
            self._launch(job_id)
            return job

    def _launch(self, job_id):
        job=self.store.snapshot()['jobs'][job_id]
        target=(self._run_zip if job.get('format')=='zip' else self._run_import) if job.get('kind')=='import' else self._run
        self.thread = threading.Thread(target=target, args=(job_id,), daemon=True)
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
            job=dict(id=jid,kind='import',format='zip',status='running',created_at=now(),completed=[],errors={},current=None,input_file=relative,plan={'items':rows})
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
                    if not self._await_running(jid):
                        return
                    self.store.change('接收原图 '+sid,lambda d:d['jobs'][jid].update(current=sid))
                    error=None
                    try:
                        if self.store.snapshot()['students'][sid].get('delivered'):
                            raise ValueError('已交付学生已跳过，不重新导入')
                        with archive.open(row['entry']) as image:
                            self.store.upload(sid,image.read(30*1024**2+1))
                    except Exception as exc:
                        error=str(exc)
                    def checkpoint(d):
                        d['jobs'][jid]['completed'].append(sid)
                        if error:
                            d['jobs'][jid]['errors'][sid]=error
                    self.store.change('压缩包接收检查点',checkpoint)
            self.store.change('接收任务结束',lambda d:d['jobs'][jid].update(status='completed',current=None))
        except Exception as exc:
            self.store.change('接收任务异常',lambda d:d['jobs'][jid].update(status='interrupted',message=str(exc)))

    def start_import(self, raw, sheet, header_row, id_col, image_col, rows):
        with self.gate, self.store.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('已有任务运行中，请先中断或等待完成')
            self.store.roster([r.student_id for r in rows], self.store.snapshot().get('active_cohort'))
            relative='inputs/'+hashlib.sha256(raw).hexdigest()+'.xlsx'
            atomic(self.store.root/relative,raw)
            jid=uuid.uuid4().hex
            job=dict(id=jid,kind='import',status='running',created_at=now(),completed=[],errors={},current=None,
                     input_file=relative,sheet=sheet,header_row=header_row,id_col=id_col,image_col=image_col,
                     plan={'items':[dict(student_id=r.student_id,execute=True,reason=None) for r in rows]})
            self.store.change('创建原图接收任务',lambda d:d['jobs'].update({jid:job}))
            self._launch(jid)
            return job

    def _await_running(self,jid):
        while True:
            state=self.store.snapshot()['jobs'][jid]['status']
            if state=='cancelling':
                self.store.change('中断任务',lambda d:d['jobs'][jid].update(status='cancelled',current=None))
                return False
            if state=='pausing':
                self.store.change('暂停完成',lambda d:d['jobs'][jid].update(status='paused',current=None))
            elif state=='running':
                return True
            time.sleep(.2)

    def _run_import(self,jid):
        from xlsx_photo_core import inspect_selection,_fetch_source
        try:
            job=self.store.snapshot()['jobs'][jid]
            report=inspect_selection(self.store.file(job['input_file']),job['sheet'],job['header_row'],job['id_col'],job['image_col'])
            for row in report.rows:
                sid=row.student_id
                if sid in job['completed']:
                    continue
                if not self._await_running(jid):
                    return
                self.store.change('接收原图 '+sid,lambda d:d['jobs'][jid].update(current=sid))
                error=None
                try:
                    student = self.store.snapshot()['students'][sid]
                    if student.get('delivered'):
                        raise ValueError('已交付学生已跳过，不重新导入')
                    if row.source.kind=='file':
                        raise ValueError('本地路径请使用图片上传入口')
                    self.store.upload(sid,_fetch_source(row.source,25))
                except Exception as exc:
                    error=str(exc)
                def checkpoint(d):
                    d['jobs'][jid]['completed'].append(sid)
                    if error:
                        d['jobs'][jid]['errors'][sid]=error
                self.store.change('原图接收检查点',checkpoint)
            self.store.change('接收任务结束',lambda d:d['jobs'][jid].update(status='completed',current=None))
        except Exception as exc:
            self.store.change('接收任务异常',lambda d:d['jobs'][jid].update(status='interrupted',message=str(exc)))

    def control(self, job_id, action):
        with self.gate:
            job = self.store.snapshot()['jobs'][job_id]
            status = job['status']
            if action == 'pause' and status == 'running':
                target = 'pausing'
            elif action == 'cancel' and status in {'running','pausing','paused','interrupted'}:
                target = 'cancelling' if self.thread and self.thread.is_alive() else 'cancelled'
            elif action == 'resume' and status in {'paused','interrupted','cancelled'}:
                if status != 'paused' and self.thread and self.thread.is_alive():
                    raise ValueError('请等待当前任务结束')
                target = 'running'
            else:
                raise ValueError('当前状态不能执行该操作')
            self.store.change('任务 '+action, lambda d: d['jobs'][job_id].update(status=target))
            if action == 'resume' and not (self.thread and self.thread.is_alive()):
                self._launch(job_id)
            return {'status': target}

    def _run(self, job_id):
        try:
            job = self.store.snapshot()['jobs'][job_id]
            for item in job['plan']['items']:
                sid = item['student_id']
                if not item['execute'] or sid in job['completed']:
                    continue
                if not self._await_running(job_id):
                    return
                self.store.change('开始处理 '+sid, lambda d: d['jobs'][job_id].update(current=sid))
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
                        student['approved'] = None
                        student['history'].append(dict(at=now(), action='完成处理', result_id=result['id']))
                        d['jobs'][job_id]['completed'].append(sid)
                    self.store.change('保存成片 '+sid, save)
                except Exception as exc:
                    message = str(exc)
                    def fail(d):
                        d['jobs'][job_id]['errors'][sid] = message
                        d['jobs'][job_id]['completed'].append(sid)
                        d['students'][sid]['approved'] = None
                        d['students'][sid]['results'].append(dict(id=uuid.uuid4().hex, at=now(), source_id=item['source_id'], config_id=job['plan']['config_id'], status='failed', message=message, stages=[], artifact=None))
                        d['students'][sid]['history'].append(dict(at=now(),action='处理失败',message=message))
                    self.store.change('处理失败 '+sid, fail)
            self.store.change('任务结束', lambda d: d['jobs'][job_id].update(status='completed', current=None))
        except Exception as exc:
            self.store.change('任务异常', lambda d: d['jobs'][job_id].update(status='interrupted', message=str(exc)))

    def execute(self, source, config):
        result = run_pipeline(self.store.file(source['file']).read_bytes(), self.options(config))
        stages = [dict(code=s.code, label=s.label, status=s.status, detail=s.detail, metrics=s.metrics, artifact=self.store.artifact(s.image_bytes)) for s in result.stages]
        return dict(status=result.status, stages=stages, reasons=result.reasons, artifact=self.store.artifact(result.output_bytes) if result.output_bytes else None)

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

    def delivery(self, ids):
        with self.store.lock:
            d = self.store.snapshot()
            entries = []
            reserved = {x['student_id'] for batch in d['deliveries'].values() if batch['status'] == 'prepared' for x in batch['items']}
            for sid in dict.fromkeys(ids):
                s = d['students'][sid]
                if Store.processing(d,sid):
                    raise ValueError(sid+' 在未完成处理任务中')
                if Store.status(s) != 'approved' or sid in reserved:
                    raise ValueError(sid+' 未审核通过或已在待交付包中')
                r = next(r for r in s['results'] if r['id'] == s['approved'])
                artifact = r['artifact']
                if hashlib.sha256(self.store.file(artifact['file']).read_bytes()).hexdigest() != artifact['id']:
                    raise ValueError('成片完整性校验失败：'+sid)
                entries.append(dict(student_id=sid, result_id=r['id'], source_id=r['source_id'], artifact=artifact))
            if not entries:
                raise ValueError('请选择已通过的学生')
            batch_id = uuid.uuid4().hex
            batch = dict(id=batch_id, at=now(), status='prepared', items=entries)
            folder = self.store.root / 'deliveries' / batch_id
            for entry in entries:
                art = entry['artifact']
                atomic(folder / 'photos' / (entry['student_id']+Path(art['file']).suffix), self.store.file(art['file']).read_bytes())
            atomic(folder / 'manifest.json', batch)
            with zipfile.ZipFile(folder / 'photos.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in sorted((folder / 'photos').iterdir()):
                    archive.write(path, path.name)
                archive.write(folder / 'manifest.json', 'manifest.json')
            self.store.change('生成交付包', lambda d: d['deliveries'].update({batch_id:batch}))
            return batch

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
