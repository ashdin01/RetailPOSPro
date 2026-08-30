# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('assets',     'assets'),
        ('version.py', '.'),
    ],
    hiddenimports=[
        'requests',
        # OS keystore for secure credential storage
        'keyring',
        'keyring.backends',
        # ESC/POS receipt printer — hardware/printer.py's Network client
        'escpos',
        'escpos.printer',
        'usb.core',
        'usb.util',
        'serial',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Dev / test tools — never needed at runtime
        'pytest',
        'pytest_qt',
        'pytest_cov',
        'coverage',
        '_pytest',
        # Unused Qt subsystems (saves ~30 MB)
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebEngineCore',
        'PyQt6.Qt3DCore',
        'PyQt6.Qt3DRender',
        'PyQt6.QtBluetooth',
        'PyQt6.QtLocation',
        'PyQt6.QtMultimedia',
        'PyQt6.QtNfc',
        'PyQt6.QtSensors',
        'PyQt6.QtCharts',
        # Unused stdlib heavyweights
        'tkinter',
        '_tkinter',
        'unittest',
        'xmlrpc',
        'distutils',
        'setuptools',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # binaries go in COLLECT, not the exe
    name='RetailPOSPro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX causes antivirus false positives on Windows
    console=False,
    icon='assets/icon.ico',
)

# --onedir: instant launch, no temp-extraction on every run
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='RetailPOSPro',
)
