# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Music Tools.

Produces a single-file Windows executable (onefile build) in ``dist/main.exe``.
Build with::

    pyinstaller main.spec

The output is consumed by installer.iss.
"""

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ffmpeg.exe', '.'),
        ('icons/logo.png', 'icons'),
        ('resources/helvetica.otf', 'resources'),
        ('resources/style.qss', 'resources'),
    ],
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
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is disabled on purpose: compression breaks Windows code signing and
    # hurts antivirus reputation, which matters for signed releases.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icons/icon.ico'],
)
