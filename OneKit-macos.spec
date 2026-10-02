# -*- mode: python ; coding: utf-8 -*-
"""macOS 打包：生成可双击的 dist/OneKit.app。

用法:
    python3 -m PyInstaller OneKit-macos.spec --noconfirm
    codesign --force --deep --sign - dist/OneKit.app
"""

a = Analysis(
    ['server.py'],
    pathex=[SPECPATH],
    binaries=[],
    datas=[('index.html', '.'), ('static', 'static')],
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
    [],
    exclude_binaries=True,
    name='OneKit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='OneKit',
)
app = BUNDLE(
    coll,
    name='OneKit.app',
    icon=None,
    bundle_identifier='local.onekit.app',
    info_plist={
        'CFBundleName': 'OneKit',
        'CFBundleDisplayName': 'OneKit',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
    },
)
