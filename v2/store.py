"""Single-writer JSON workspace. Artifacts are immutable; decisions name versions."""
import copy
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2).encode() if not isinstance(value, bytes) else value
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9A-Za-z_-]{1,64}', value):
        raise ValueError('学号必须为1–64位字母、数字、下划线或连字符')
    if value.upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(10)), *('LPT'+str(i) for i in range(10))}:
        raise ValueError('学号不能使用系统保留文件名')
    return value


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.path = self.root / 'workspace.json'
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            self.data.setdefault('active_cohort', str(datetime.now().year)+'级')
            for student in self.data.get('students', {}).values(): student.setdefault('cohort', self.data['active_cohort'])
            if self.data.get('schema_version') != 2:
                raise ValueError('工作区版本不支持，请使用V2工作区目录')
        else:
            if (self.root / 'export_state.json').exists():
                raise ValueError('请选择新的V2目录，然后从迁移入口导入旧数据')
            self.data = dict(schema_version=2, revision=0, active_cohort=str(datetime.now().year)+'级', students={}, deliveries={}, jobs={}, config={}, events=[])
            atomic(self.path, self.data)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def change(self, action, callback):
        with self.lock:
            candidate = copy.deepcopy(self.data)
            result = callback(candidate)
            candidate['revision'] += 1
            candidate['events'].append(dict(id=uuid.uuid4().hex, at=now(), action=action))
            atomic(self.path, candidate)
            self.data = candidate
            return result

    def file(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError('文件不存在或超出工作区')
        return path

    def artifact(self, data):
        from PIL import Image
        if len(data) > 30 * 1024 * 1024:
            raise ValueError('图片超过30MB')
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > 40_000_000:
                raise ValueError('图片超过4000万像素')
            width, height = im.size
            fmt = im.format
            im.verify()
        ext = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp', 'BMP': 'bmp', 'TIFF': 'tif', 'GIF': 'gif'}.get(fmt)
        if not ext:
            raise ValueError('不支持此图片格式')
        sha = digest(data)
        relative = f'artifacts/{sha[:2]}/{sha}.{ext}'
        if not (self.root / relative).exists():
            atomic(self.root / relative, data)
        return dict(id=sha, file=relative, width=width, height=height, bytes=len(data))

    @staticmethod
    def add_roster(d, ids, cohort=None):
        ids = [valid_id(x.strip()) for x in ids]
        if not ids or len({x.casefold() for x in ids}) != len(ids):
            raise ValueError('名单为空或包含重复学号')
        cohort = cohort or d.get('active_cohort') or str(datetime.now().year)+'级'
        existing = {x.casefold(): x for x in d['students']}
        for sid in ids:
            if sid.casefold() in existing and existing[sid.casefold()] != sid:
                raise ValueError('学号大小写冲突')
            d['students'].setdefault(sid, dict(id=sid, cohort=cohort, sources=[], results=[], approved=None, delivered=None, replacement=False, history=[]))
        return {'count': len(d['students'])}

    def roster(self, ids, cohort=None):
        return self.change('更新名单', lambda d: self.add_roster(d, ids, cohort))

    def set_cohort(self, cohort):
        if not isinstance(cohort, str) or not re.fullmatch(r'\d{4}级', cohort):
            raise ValueError('届次格式应为YYYY级')
        return self.change('切换当前届次', lambda d: d.update(active_cohort=cohort) or {'active_cohort': cohort})

    def upload(self, sid, data):
        valid_id(sid)
        with self.lock:
            if sid not in self.data['students']:
                raise ValueError('请先导入该学号到名单')
            artifact = self.artifact(data)
            def update(d):
                s = d['students'][sid]
                if s['sources'] and s['sources'][-1]['id'] == artifact['id']:
                    return {'changed': False}
                s['sources'].append(dict(artifact, at=now()))
                s['history'].append(dict(at=now(), action='接收新原图', source=artifact['id']))
                return {'changed': True}
            return self.change('接收原图 '+sid, update)

    @staticmethod
    def current(s):
        source = s['sources'][-1] if s['sources'] else None
        results = [r for r in s['results'] if source and r['source_id'] == source['id']]
        return source, results[-1] if results else None

    @staticmethod
    def processing(d,sid):
        return any(j.get('kind')!='import' and j['status'] in {'running','pausing','paused','cancelling'}
                   and sid not in j['completed'] and any(x['student_id']==sid and x['execute'] for x in j['plan']['items'])
                   for j in d['jobs'].values())

    @staticmethod
    def status(s):
        source, result = Store.current(s)
        if s['delivered'] and not s['replacement']:
            return 'delivered_updated' if source and s['delivered'].get('source_id') != source['id'] else 'delivered'
        if not source:
            return 'missing'
        approved = next((r for r in s['results'] if r['id'] == s['approved']), None)
        if approved and approved['source_id'] == source['id']:
            return 'approved'
        if not result:
            return 'pending'
        if result.get('review') == 'rejected':
            return 'review_rejected'
        return {'success': 'review', 'warning': 'review', 'rejected': 'machine_rejected', 'failed': 'failed'}[result['status']]

    def review(self, sid, result_id, decision, expected_revision, reason=''):
        if decision not in {'approved', 'rejected', 'pending'}:
            raise ValueError('审核决定无效')
        def update(d):
            s = d['students'][sid]
            if any(b['status']=='prepared' and any(x['student_id']==sid for x in b['items']) for b in d['deliveries'].values()):
                raise ValueError('该版本已加入待交付包，请先取消交付包再修改审核')
            if self.processing(d,sid):
                raise ValueError('该学生在未完成处理任务中，请等待完成或中断后审核')
            if len(s['history']) != expected_revision:
                raise ValueError('该学生已变化，请刷新后审核')
            if s['delivered'] and not s['replacement']:
                raise ValueError('请先启动替换流程')
            source, r = self.current(s)
            if not r or r['id'] != result_id or not r.get('artifact'):
                raise ValueError('当前成片版本不匹配或无有效成片')
            if decision == 'approved' and r['status'] not in {'success', 'warning'}:
                raise ValueError('此版本不能通过')
            r.update(review=decision, review_reason=reason)
            s['approved'] = r['id'] if decision == 'approved' else None
            s['history'].append(dict(at=now(), action='审核 '+decision, result_id=r['id'], reason=reason))
            return {'status': self.status(s)}
        return self.change('审核 '+sid, update)
