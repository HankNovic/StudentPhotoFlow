# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all


root = Path(SPECPATH)
python_root = Path(sys.base_prefix)
datas = [
    (str(root / "models"), "models"),
    (str(python_root / "Lib" / "tkinter"), "tkinter"),
    (str(python_root / "tcl" / "tcl8.6"), "_tcl_data"),
    (str(python_root / "tcl" / "tk8.6"), "_tk_data"),
]
binaries = [
    (str(python_root / "DLLs" / "_tkinter.pyd"), "."),
    (str(python_root / "DLLs" / "tcl86t.dll"), "."),
    (str(python_root / "DLLs" / "tk86t.dll"), "."),
]
hiddenimports = ["tkinter", "_tkinter", "PIL._tkinter_finder", "photo_workbench"]

for package in ("rembg", "onnxruntime", "cv2", "pymatting"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [str(root / "photo_exporter.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    runtime_hooks=[str(root / "pyi_rth_portable_tk.py")],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoExporter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="StudentPhotoFlow",
)
