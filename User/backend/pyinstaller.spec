# Build with:  pyinstaller pyinstaller.spec   (run from User/backend/)
# Output:      dist/ocr-backend/ocr-backend[.exe]  (onedir — much faster
#              startup than --onefile, which matters a lot once torch is
#              added; see architecture_and_requirements.md §10 "Thời gian
#              khởi động").
# electron-builder.yml's extraResources expects exactly this dist/ocr-backend
# directory to exist before `npm run electron:build`.
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("fitz", "uvicorn", "docx", "lxml", "bs4", "torch", "torchcrf", "PIL"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# app/weights/bilstm_attention_crf.pt (layout_reconstructor_v2.py's
# checkpoint) — collect_all() only picks up files inside installed packages,
# not our own app/ tree, so it needs to be added explicitly.
datas += [('app/weights/bilstm_attention_crf.pt', 'app/weights')]

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ocr-backend',
    debug=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='ocr-backend',
)
