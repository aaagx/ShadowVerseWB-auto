# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['sv-auto.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('Image', 'Image'),
        ('shield', 'shield'),
        ('templates', 'templates'),
        ('templates2', 'templates2'),
        ('templates_cost', 'templates_cost'),
        ('config.json', '.'),
        (r'D:\Shadowverse_Auto\StarWishLXH_svb_aito\.venv\Lib\site-packages\uiautomator2\assets\u2.jar', 'uiautomator2/assets'),

    ],
    hiddenimports=[
        'PyQt5.sip',
        'cv2',
        'numpy',
        'uiautomator2',
        'adbutils',
        'logging',
        'queue',
        'json',
        'datetime',
        'random',
        'io',
        'ctypes',
        'ctypes.wintypes'
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
    name='ShadowverseAutomation',
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
    icon='app_icon.ico',  # 可选：添加图标文件路径
)

