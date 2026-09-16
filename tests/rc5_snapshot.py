"""Snapshot only an explicitly supplied acceptance workspace; contains private data locally."""
import hashlib,json,sys
from pathlib import Path

def snapshot(root):
    root=Path(root); files={}; records={}
    for p in sorted(root.rglob('*')):
        if not p.is_file() or 'backups' in p.parts: continue
        rel=p.relative_to(root).as_posix()
        if p.name in {'workspace.json','system.json'}:
            d=json.loads(p.read_text('utf-8'))
            for key in ('revision','events','audit','task_semantics'):d.pop(key,None)
            for job in d.get('jobs',{}).values():
                for key in ('uncertain','skipped','end_reason'):job.pop(key,None)
            records[rel]=d
        else:files[rel]=hashlib.sha256(p.read_bytes()).hexdigest()
    return dict(records=records,files=files)

if __name__=='__main__':
    result=snapshot(sys.argv[1]);out=Path(sys.argv[2])
    if len(sys.argv)>3:
        assert result==json.loads(out.read_text('utf-8')), 'Persistent business records or file hashes differ'
        print('PASS all students/photos/config/cohorts/trash/reviews/deliveries/task checkpoints preserved; startup audit and additive metadata excluded')
    else:out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print('Snapshot saved locally')
