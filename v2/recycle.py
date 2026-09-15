"""Atomic record ownership; immutable files remain until resumable final purge."""
from datetime import datetime, timedelta, timezone
import uuid
from .store import now

ACTIVE = {'running','queued','pausing','paused','cancelling'}

def files(value):
    result=set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'file','input_file'} and isinstance(child,str): result.add(child)
            else: result.update(files(child))
    elif isinstance(value,list):
        for child in value: result.update(files(child))
    return result

def bundle_files(bundle):
    result=files(bundle)
    for bid,b in bundle.get('deliveries',{}).items():
        if b.get('historical'): continue
        result.update({f'deliveries/{bid}/photos.zip',f'deliveries/{bid}/manifest.json'})
        for item in b['items']:
            from pathlib import Path
            result.add(f"deliveries/{bid}/photos/{item['student_id']}"+Path(item['artifact']['file']).suffix)
    return result

def inactive(service):
    if (service.thread and service.thread.is_alive()) or any(j['status'] in ACTIVE for j in service.store.data['jobs'].values()):
        raise ValueError('有活动接收或处理任务，请前往任务进度安全停止；暂停也需中断')

def impact(service, ids, all_students=False):
    d=service.store.data
    selected=set(d['students'] if all_students else ids)
    if not selected and not all_students: raise ValueError('请选择学生')
    if selected-set(d['students']): raise ValueError('学生不存在或已经移入回收站')
    related=set(selected)
    # Batch files and task plans are indivisible recovery units.
    batches=[('deliveries',bid,{i['student_id'] for i in b['items']}) for bid,b in d['deliveries'].items()]
    batches += [('jobs',jid,{i['student_id'] for i in j['plan']['items']}) for jid,j in d['jobs'].items()]
    while True:
        expanded=related | set().union(*(members for _,_,members in batches if members & related))
        if expanded==related: break
        related=expanded
    related &= set(d['students'])
    bundle={k:{} for k in ('students','jobs','deliveries')}
    bundle['students']={sid:d['students'][sid] for sid in sorted(related)}
    for kind,identity,members in batches:
        if members & related or all_students: bundle[kind][identity]=d[kind][identity]
    return dict(student_ids=sorted(related),requested=len(selected),students=len(related),
                photos=len(files(bundle['students'])),deliveries=len(bundle['deliveries']),jobs=len(bundle['jobs']),
                expanded=related!=selected,revision=d['revision'],bundle=bundle)

def move(service, ids, days, expected_revision, all_students=False):
    store=service.store
    with store.lock:
        inactive(service)
        preview=impact(service,ids,all_students)
        if preview['revision']!=expected_revision: raise ValueError('删除范围已变化，请重新预览确认')
        if preview['expanded']: raise ValueError('必须包含完整关联学生；请使用影响预览中的完整范围，或前往系统设置清空本届')
        if not any(preview['bundle'].values()): raise ValueError('当前届次没有可清空的业务数据')
        stamp=datetime.now(timezone.utc)
        item=dict(id=uuid.uuid4().hex,deleted_at=stamp.isoformat(),expires_at=(stamp+timedelta(days=days)).isoformat() if days else None,
                  status='recoverable',scope='cohort' if all_students else 'students',**preview['bundle'])
        def update(d):
            for kind in ('students','jobs','deliveries'):
                for identity in item[kind]: del d[kind][identity]
            d.setdefault('trash',{})[item['id']]=item
            return {'id':item['id']}
        return store.change('移入回收站 '+','.join(item['students']),update)

def restore(service, identity):
    store=service.store
    with store.lock:
        inactive(service)
        item=store.data.get('trash',{}).get(identity)
        if not item or item['status']!='recoverable': raise ValueError('记录不存在或已开始永久清理，不能恢复')
        for kind in ('students','jobs','deliveries'):
            if set(item[kind]) & set(store.data[kind]): raise ValueError('恢复冲突：同届学号或业务记录已存在，不会覆盖')
        if {s.casefold() for s in item['students']} & {s.casefold() for s in store.data['students']}:
            raise ValueError('恢复冲突：学号大小写冲突')
        for relative in bundle_files(item): store.file(relative)
        def update(d):
            for kind in ('students','jobs','deliveries'): d[kind].update(item[kind])
            del d['trash'][identity]
            return {'ok':True}
        return store.change('恢复回收站 '+identity,update)

def purge(service, identity):
    store=service.store
    with store.lock:
        inactive(service)
        item=store.data.get('trash',{}).get(identity)
        if not item: return {'ok':True}
        if item['status']=='recoverable':
            retained=bundle_files({k:store.data[k] for k in ('students','jobs','deliveries')})
            for key,value in store.data.get('trash',{}).items():
                if key!=identity: retained.update(bundle_files(value))
            pending=sorted(bundle_files(item)-retained)
            store.change('开始永久清理 '+identity,lambda d:d['trash'][identity].update(status='purging',pending_files=pending))
        try:
            retained=bundle_files({k:store.data[k] for k in ('students','jobs','deliveries')})
            for key,value in store.data.get('trash',{}).items():
                if key!=identity: retained.update(bundle_files(value))
            for relative in store.data['trash'][identity]['pending_files']:
                if relative in retained: continue
                path=(store.root/relative).resolve()
                if not path.is_relative_to(store.root): raise ValueError('清理路径超出工作区')
                path.unlink(missing_ok=True)
            store.change('永久清理成功 '+identity,lambda d:d['trash'].pop(identity))
        except Exception as exc:
            store.change('永久清理失败 '+identity,lambda d:d['trash'][identity].update(error=str(exc),last_attempt=now()),result_status='failed')
            raise
        return {'ok':True}

def cleanup(service, enabled=True, at=None):
    at=at or datetime.now(timezone.utc)
    for identity,item in service.store.snapshot().get('trash',{}).items():
        if item['status']=='purging' or (enabled and item.get('expires_at') and datetime.fromisoformat(item['expires_at'])<=at):
            try: purge(service,identity)
            except Exception: pass  # persisted by purge; active tasks defer to the next scan
