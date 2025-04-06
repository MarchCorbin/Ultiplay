# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['ultiplay.py'],
    pathex=[],
    binaries=[('C:\\Program Files\\VideoLAN\\VLC\\libvlc.dll', 'vlc'), ('C:\\Program Files\\VideoLAN\\VLC\\libvlccore.dll', 'vlc'), ('C:\\Program Files\\VideoLAN\\VLC\\plugins', 'vlc\\plugins')],
    datas=[('C:\\Users\\march\\OneDrive\\Desktop\\Ultiplay\\uliplay_venv\\Lib\\site-packages\\tkinterdnd2', 'tkinterdnd2'), ('C:\\Users\\march\\OneDrive\\Desktop\\Ultiplay\\appicon.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=True,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [('v', None, 'OPTION')],
    name='ultiplay',
    debug=True,
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
    icon=['C:\\Users\\march\\OneDrive\\Desktop\\Ultiplay\\appicon.ico'],
)
