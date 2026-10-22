# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Spedi (Windows one-folder bundle)."""
import os

# Prefer Windows runtime DLLs over unrelated tools injected into PATH (e.g.
# Poppler's icuuc.dll exports versioned symbols incompatible with Qt's system ICU).
if os.name == "nt":
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    os.environ["PATH"] = system32 + os.pathsep + os.environ.get("PATH", "")

from PyInstaller.utils.hooks import collect_all, collect_submodules

base_path = os.path.abspath(SPECPATH)
tacho_root = os.path.join(base_path, "spedi", "_vendor", "tachograph")
ddd_root = brud_root = ewi_root = disp_root = base_path

datas = [
    (os.path.join(base_path, "LICENSE"), "."),
    (os.path.join(base_path, "THIRD_PARTY_NOTICES.md"), "."),
    (os.path.join(tacho_root, "LICENSE"), "third-party/tachograph"),
    (os.path.join(base_path, "spedi", "locales"), "spedi/locales"),
    (os.path.join(base_path, "spedi", "ui", "app.qss"), "spedi/ui"),
    (os.path.join(base_path, "spedi", "ui", "app.ico"), "spedi/ui"),
]
binaries = []
hiddenimports = []

# Private offline OCR, downloaded separately by tools/setup_tesseract.py.
ocr_runtime = os.path.join(base_path, 'work', 'local-tools', 'tesseract')
if os.path.isfile(os.path.join(ocr_runtime, 'tesseract.exe')):
    datas.append((ocr_runtime, 'tesseract'))

examples = os.path.join(brud_root, "brudnopis_ot", "examples")
if os.path.isdir(examples):
    datas.append((examples, "brudnopis_ot/examples"))

for pkg in (
    "PySide6",
    "tzdata",
    "google_auth_oauthlib",
    "ddd_analyzer",
    "brudnopis_ot",
    "ewidentor",
    "markus_dispatcher",
    "reportlab",
    "spedi",
):
    try:
        tmp_ret = collect_all(pkg)
        datas += tmp_ret[0]
        binaries += tmp_ret[1]
        hiddenimports += tmp_ret[2]
    except Exception:
        pass

for src, dest in (
    (os.path.join(tacho_root, "certs"), "certs"),
    (os.path.join(tacho_root, "app"), "app"),
    (os.path.join(tacho_root, "core"), "core"),
):
    if os.path.isdir(src):
        datas.append((src, dest))

for extra in ("firebase_admin", "google.cloud.firestore"):
    try:
        fb = collect_all(extra)
        datas += fb[0]
        binaries += fb[1]
        hiddenimports += fb[2]
        hiddenimports += collect_submodules(extra)
    except Exception:
        pass

hiddenimports += [
    "spedi",
    "spedi.app",
    "spedi.shell",
    "spedi.paths",
    "spedi.ui",
    "brudnopis_ot",
    "brudnopis_ot.ui.cycle_start_dialog",
    "ewidentor",
    "markus_dispatcher",
    "markus_dispatcher.ui.main_window",
    "ddd_analyzer",
    "ddd_analyzer.ui.main_window",
    "app",
    "core",
    "cryptography",
    "httpx",
    "platformdirs",
    "PIL",
    "PySide6.QtSvg",
    "PySide6.QtPdf",
    "openpyxl",
    "xlrd",
    # Dispatcher loads OAuth only after the user clicks "Połącz z Google".
    "google_auth_oauthlib.flow",
    "google.oauth2.credentials",
    "google.auth.transport.requests",
]
hiddenimports += collect_submodules("ddd_analyzer")
hiddenimports += collect_submodules("brudnopis_ot")
hiddenimports += collect_submodules("ewidentor")
hiddenimports += collect_submodules("markus_dispatcher")
hiddenimports += collect_submodules("spedi")

a = Analysis(
    [os.path.join(base_path, "spedi", "app.py")],
    pathex=[base_path, tacho_root, ddd_root, brud_root, ewi_root, disp_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tkinter",
        "googleapiclient",
        "ddd_analyzer.sheets_export",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Spedi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(base_path, "spedi", "ui", "app.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Spedi",
)
