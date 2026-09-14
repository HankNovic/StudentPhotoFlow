"""Synthetic photos and workbooks only; no student data."""
import hashlib
import shutil
import io
import json
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from PIL import Image, ImageDraw
root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
for sid,color in [('00001','#9bc9ed'),('00002','#ebc39c'),('00003','#b3dba6')]:
    im=Image.new('RGB',(600,800),color);draw=ImageDraw.Draw(im)
    draw.ellipse((160,90,440,420),fill='#f2d1b5');draw.rounded_rectangle((110,410,490,780),100,fill='#254454');draw.text((20,20),'SYNTHETIC TEST '+sid,fill='black')
    im.save(root/(sid+'.png'))
with zipfile.ZipFile(root/'photos.zip','w') as z:
    for sid in ('00001','00002'):z.write(root/(sid+'.png'),'photos/'+sid+'-Test.png')
(root/'roster.txt').write_text('00003\n',encoding='utf-8')
(root/'legacy').mkdir(exist_ok=True)
shutil.copy2(root/'00001.png',root/'legacy/original.png')
shutil.copy2(root/'00002.png',root/'legacy/result.png')
shutil.copy2(root/'00002.png',root/'legacy/archive.png')
(root/'legacy/export_state.json').write_text(json.dumps({'records':{'00999':{'original_file':'original.png','processing':{'processed_file':'result.png','status':'success','step_files':[{'file':'result.png','label':'Legacy result'}]}}}}),encoding='utf-8')
(root/'legacy/review_state.json').write_text(json.dumps({'reviews':{'00999':{'status':'approved','archive_file':'archive.png','snapshot':{'original':{'sha256':hashlib.sha256((root/'00001.png').read_bytes()).hexdigest()},'result':{'sha256':hashlib.sha256((root/'00002.png').read_bytes()).hexdigest()}}}}}),encoding='utf-8')
(root/'legacy/delivered_state.json').write_text(json.dumps({'schema_version':1,'student_ids':['00999'],'count':1,'sha256':hashlib.sha256(b'00999').hexdigest(),'revision':'test-only','actions':[]}),encoding='utf-8')
(root/'legacy/grade_roster.json').write_text(json.dumps({'student_ids':['00999']}),encoding='utf-8')
def workbook(name,year_values):
    rows=[['姓名','学号','个人免冠照片','入学年份']]
    for i,year in enumerate(year_values):rows.append(['Synthetic',str(100+i).zfill(5),(sys.argv[2]+'/photo/'+str(i) if len(sys.argv)>2 else ''),year])
    with zipfile.ZipFile(root/name,'w') as z:
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="xl/workbook.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/></Relationships>')
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
        data=''.join('<row r="'+str(i)+'">'+''.join('<c r="'+chr(65+j)+str(i)+'" t="inlineStr"><is><t>'+escape(str(v))+'</t></is></c>' for j,v in enumerate(row))+'</row>' for i,row in enumerate(rows,1))
        z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+data+'</sheetData></worksheet>')
workbook('single-year.xlsx',['2026级'])
workbook('multi-year.xlsx',['2026级','2025级'])
workbook('no-year.xlsx',[''])

workbook('slow.xlsx',['2026级']*5)
