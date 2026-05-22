# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['client_lan.py'],
    pathex=[],
    binaries=[('C:\\Users\\Андрей\\AppData\\Local\\Python\\pythoncore-3.14-64\\Lib\\site-packages\\pytun_pmd3\\wintun\\bin\\amd64\\wintun.dll', 'pytun_pmd3/wintun/bin/amd64')],
    datas=[],
    hiddenimports=['pystray._win32', 'win32api', 'win32gui'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PyLAN',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
