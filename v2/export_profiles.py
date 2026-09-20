"""Small, portable export filename profiles and validation."""
import re
from pathlib import Path

FIELDS = {'student_id', 'name'}
TOKEN = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}')
BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
DEFAULT_TEMPLATE = '{student_id}{ext}'
DEFAULT_RULES = {'missing_field':'error','invalid_character':'replace','duplicate_name':'error'}

def parse_template(template):
    if not isinstance(template,str) or not template or len(template)>300:
        raise ValueError('模板不能为空且不得超过300个字符')
    fields = TOKEN.findall(template)
    unknown = sorted(set(fields)-FIELDS-{'ext'})
    if unknown: raise ValueError('模板包含不支持的字段：'+', '.join('{'+x+'}' for x in unknown))
    if not fields: raise ValueError('模板至少要包含一个字段')
    return fields

def validate_profile(name, template, rules=None):
    name=str(name or '').strip()
    if not name or len(name)>80: raise ValueError('格式名称不能为空且不得超过80个字符')
    fields=parse_template(template)
    rules={**DEFAULT_RULES,**(rules or {})}
    if rules != DEFAULT_RULES: raise ValueError('格式规则不受支持')
    return {'name':name,'template':template,'rules':rules,'fields':fields}

def render(profile, student):
    template=profile['template']; fields=parse_template(template)
    values={k:str(student.get(k,'') or '') for k in fields}
    if 'name' in values:
        values['name']=values['name'].strip()
    missing=[k for k in fields if k!='ext' and not values[k]]
    if missing: return {'file_name':None,'status':'error','reason':'缺少字段：'+', '.join(missing),'missing':missing}
    raw=TOKEN.sub(lambda m: values[m.group(1)], template)
    safe=BAD.sub('_',raw).rstrip(' .')
    changed=safe!=raw
    stem=Path(safe).stem.upper()
    if stem in RESERVED: return {'file_name':None,'status':'error','reason':'文件名使用系统保留名称：'+safe}
    if not safe: return {'file_name':None,'status':'error','reason':'文件名为空'}
    if len(safe)>255: return {'file_name':None,'status':'error','reason':'文件名超过255个字符'}
    return {'file_name':safe,'status':'warning' if changed else 'ok','reason':'非法字符已替换为 _，尾部空格或句点已移除' if changed else '正常'}

def preview(profile, students):
    rows=[]; names={}
    for student in students:
        row=render(profile,student); row.update(student_id=student.get('student_id',''),name=student.get('name'))
        rows.append(row)
        if row['file_name']: names.setdefault(row['file_name'].casefold(),[]).append(row['student_id'])
    duplicates={n:sids for n,sids in names.items() if len(sids)>1}
    if duplicates:
        for row in rows:
            key=(row.get('file_name') or '').casefold()
            if key in duplicates: row.update(status='error',reason='文件名重复：'+', '.join(duplicates[key]))
    return {'items':rows,'ok':not any(x['status']=='error' for x in rows),'duplicates':duplicates}

def default_profile():
    return {'id':'default-student-id','name':'默认学号格式','template':DEFAULT_TEMPLATE,'revision':1,'status':'active','is_default':True,'created_at':'','updated_at':'','rules':dict(DEFAULT_RULES),'fields':['student_id','ext']}
