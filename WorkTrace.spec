# -*- mode: python ; coding: utf-8 -*-
# onefile 模式：打成单个 WorkTrace.exe，方便分发给小白（双击即用，无 _internal 目录）
import os

block_cipher = None

WORK_DIR = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    ['time_tracker_v2.py'],
    pathex=[WORK_DIR],
    binaries=[],
    datas=[
        (os.path.join(WORK_DIR, 'icons'), 'icons'),
    ],
    hiddenimports=[
        'win32gui', 'win32con', 'win32api', 'win32process',
        'pywintypes', 'psutil',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='WorkTrace',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(WORK_DIR, 'icons', 'worktrace.ico'),
)