# PyInstaller onedir configuration for the Windows desktop application.
from pathlib import Path


ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "assets" / "icons"), "assets/icons"),
        (str(ROOT / "assets" / "logo" / "snapseek-icon.ico"), "assets/logo"),
        (str(ROOT / "assets" / "logo" / "snapseek-icon.png"), "assets/logo"),
        (str(ROOT / "assets" / "licenses" / "pypdfium2" / "5.14.0"), "licenses/pypdfium2/5.14.0"),
    ],
    # semantic.py and ocr.py import their runtime packages dynamically.
    hiddenimports=["fastembed", "pytesseract", "pypdfium2", "pypdfium2_raw", "watchdog", "watchdog.observers", "watchdog.observers.read_directory_changes"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# Some dependency hooks collect bundled test fixtures. They are not runtime
# requirements for SnapSeek and should not inflate the distributable folder.
a.datas = [
    entry
    for entry in a.datas
    if "tests" not in entry[0].replace("\\", "/").casefold().split("/")
]
a.binaries = [
    entry
    for entry in a.binaries
    if not Path(entry[0]).name.startswith("_multiarray_tests.")
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SnapSeek",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "logo" / "snapseek-icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SnapSeek",
)
