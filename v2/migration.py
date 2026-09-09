"""Copy legacy data into an empty workspace; preserve delivery evidence separately."""
import hashlib
import json
import uuid
from pathlib import Path

from .store import now, valid_id


def migrate_legacy(store, directory, apply=False):
    root=Path(directory).resolve()
    def read(name,default):
        path=root/name
        return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default
    state=read('export_state.json',{})
    if not isinstance(state.get('records'),dict):
        raise ValueError('未找到有效旧版 export_state.json')
    roster=read('grade_roster.json',{}).get('student_ids',list(state['records']))
    reviews=read('review_state.json',{}).get('reviews',{})
    from delivery_state import load_delivery_locks
    delivered=load_delivery_locks(root)['student_ids']
    ids=list(dict.fromkeys(roster+list(state['records'])+delivered))
    for sid in ids:
        valid_id(sid)
    if len({x.casefold() for x in ids}) != len(ids):
        raise ValueError('旧数据学号大小写冲突')
    warnings=[]
    def source_file(relative):
        if not relative:
            return None
        path=(root/relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError('旧数据引用了目录外文件')
        if not path.is_file():
            warnings.append('文件缺失：'+relative)
            return None
        return path
    prepared=[]
    for sid in ids:
        record=state['records'].get(sid,{})
        original=source_file(record.get('original_file'))
        process=record.get('processing',{})
        result=source_file(process.get('processed_file'))
        review=reviews.get(sid,{})
        archive=source_file(review.get('archive_file')) if review.get('status')=='approved' else None
        prepared.append((sid,original,result,archive,process,review))
    report=dict(students=len(ids),historical_delivered=len(delivered),originals=sum(bool(x[1]) for x in prepared),results=sum(bool(x[2]) for x in prepared),archives=sum(bool(x[3]) for x in prepared),warnings=warnings)
    if not apply:
        return report
    with store.lock:
        if store.data['students']:
            raise ValueError('迁移目标必须是空V2工作区，避免混合数据')
        students={}
        batch_id='legacy-'+uuid.uuid4().hex
        entries=[]
        for sid,original,result,archive,process,review in prepared:
            s=dict(id=sid,sources=[],results=[],approved=None,delivered=None,replacement=False,history=[dict(at=now(),action='迁移旧版数据')])
            if original:
                s['sources'].append(dict(store.artifact(original.read_bytes()),at=now()))
            source_id=s['sources'][-1]['id'] if s['sources'] else None
            if result:
                art=store.artifact(result.read_bytes())
                rid=uuid.uuid4().hex
                stages=[]
                for step in process.get('step_files',[]):
                    step_path=source_file(step.get('file'))
                    if step_path:
                        stages.append(dict(code=step.get('code','legacy'),label=step.get('label','旧版步骤'),status=step.get('status','passed'),detail=step.get('detail',''),metrics=step.get('metrics',{}),artifact=store.artifact(step_path.read_bytes())))
                s['results'].append(dict(id=rid,source_id=source_id,config_id=process.get('config_fingerprint'),status=process.get('status') if process.get('status') in {'success','warning','rejected','failed'} else 'success',artifact=art,stages=stages,review='pending',at=process.get('last_processed_at')))
                # Only content evidence establishes approval; mtime/path changes do not.
                snapshot=review.get('snapshot',{})
                old_source=snapshot.get('original',{}).get('sha256')
                old_result=snapshot.get('result',{}).get('sha256')
                if archive and old_source==source_id and old_result==art['id'] and hashlib.sha256(archive.read_bytes()).hexdigest()==art['id']:
                    s['approved']=rid
                    s['results'][-1]['review']='approved'
            if archive:
                s['legacy_archive']=store.artifact(archive.read_bytes())
            if sid in delivered:
                s['delivered']=dict(batch_id=batch_id,result_id=None,source_id=source_id,at=now(),evidence='历史已交付名单；成片版本未核实')
                entries.append(dict(student_id=sid,result_id=None,source_id=source_id))
            students[sid]=s
        def commit(d):
            d['students']=students
            if entries:
                d['deliveries'][batch_id]=dict(id=batch_id,status='delivered',at=now(),items=entries,historical=True)
            d['migration']=dict(report,source=str(root),at=now())
        store.change('迁移旧版数据',commit)
        return report
