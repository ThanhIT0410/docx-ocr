# Build with:  pyinstaller pyinstaller.spec   (run from Processor/backend/)
# Output:      dist/ocr-processor-backend/ocr-processor-backend[.exe]  (onedir)
# One binary, one process (run.py's admin API — OCR runs as a background
# task inside it, see app/services/ocr_pipeline.py, not a separate mode).
# Processor/frontend/electron-builder.yml's extraResources expects exactly
# this dist/ocr-processor-backend directory to exist before
# `npm run electron:build`.
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in (
    "uvicorn",
    "supabase",
    "supabase_auth",
    "supabase_functions",
    "postgrest",
    "storage3",
    "realtime",
    "openai",
    "httpx",
    "cv2",
    "bs4",
    "dotenv",
    "pythonjsonlogger",
    "tenacity",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

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
    name='ocr-processor-backend',
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
    name='ocr-processor-backend',
)
