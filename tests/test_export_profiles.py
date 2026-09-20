import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from v2.export_profiles import *
from v2.store import Store
import json,tempfile

def test_templates_and_safety():
    p=validate_profile('校园卡','{student_id}-{name}{ext}')
    assert render(p,{'student_id':'00123','name':'张三','ext':'.jpg'})['file_name']=='00123-张三.jpg'
    assert render(validate_profile('x','{student_id} {ext}'),{'student_id':'A/B','ext':'.png'})['status']=='warning'
    assert render(validate_profile('x','{student_id}{ext}'),{'student_id':'CON','ext':'.jpg'})['status']=='error'
    assert preview(p,[{'student_id':'1','name':'张三','ext':'.jpg'},{'student_id':'1','name':'张三','ext':'.jpg'}])['ok'] is False
    for field in ('class_name','grade','major'):
        try: validate_profile('x','{'+field+'}{ext}')
        except ValueError as e: assert '不支持' in str(e)
        else: raise AssertionError('unsupported field accepted')
    try: validate_profile('x','{student_id}'+'a'*296+'{ext}')
    except ValueError: pass
    else: raise AssertionError('long template accepted')

def test_old_workspace_gets_default_without_rewriting_until_next_change():
    root=Path(tempfile.mkdtemp()); data={'schema_version':2,'revision':0,'active_cohort':'2026级','students':{},'deliveries':{},'jobs':{},'config':{},'hivision_urls':[],'events':[],'cohort_records':{'2026级':{'id':'c','name':'2026级','archived':False}},'recycle_bin':[]}
    (root/'workspace.json').write_text(json.dumps(data),encoding='utf-8'); s=Store(root)
    assert s.snapshot()['export_profiles'][0]['template']=='{student_id}{ext}'

if __name__=='__main__': test_templates_and_safety();test_old_workspace_gets_default_without_rewriting_until_next_change(); print('ok')
