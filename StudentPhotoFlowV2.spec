from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
import os
import sys
sys.path.insert(0, str(root))
from build_candidate import validate_web
if not os.environ.get('SPF_WEB_ROOT'):
    raise SystemExit('SPF_WEB_ROOT is required; build the Vue frontend first.')
web = Path(os.environ['SPF_WEB_ROOT']).resolve()
validate_web(web)
datas = [(str(root/'models'),'models'), (str(web),'v2/web')]
binaries = []
hiddenimports = []
for package in ('rembg','onnxruntime','cv2','pymatting','uvicorn'):
    d,b,h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
a = Analysis([str(root/'launch_v2.py')],pathex=[str(root)],binaries=binaries,datas=datas,hiddenimports=hiddenimports,excludes=['tkinter'],noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='StudentPhotoFlowV2',console=False,upx=False)
coll = COLLECT(exe,a.binaries,a.datas,name='StudentPhotoFlowV2',upx=False)
