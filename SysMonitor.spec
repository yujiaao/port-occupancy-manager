# -*- mode: python ; coding: utf-8 -*-
# 打包: pyinstaller SysMonitor.spec   → dist/SysMonitor.exe（双击即启动监控页面）
# 依赖: sysmon.py + sysmon.html（同目录）

a = Analysis(
    ['sysmon.py'],
    pathex=[],
    binaries=[],
    datas=[('sysmon.html', '.')],
    hiddenimports=[],
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
    name='SysMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
