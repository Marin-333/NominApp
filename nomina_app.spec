# -*- mode: python ; coding: utf-8 -*-
"""
nomina_app.spec
Archivo de configuración para PyInstaller
Para compilar: pyinstaller nomina_app.spec
"""

import sys
import os

block_cipher = None

# Detectar reportlab data files
import reportlab
reportlab_dir = os.path.dirname(reportlab.__file__)

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Incluir datos de reportlab (fuentes, etc.)
        (reportlab_dir, 'reportlab'),
    ],
    hiddenimports=[
        'paths',
        'reportlab',
        'reportlab.graphics',
        'reportlab.graphics.renderPDF',
        'reportlab.lib',
        'reportlab.lib.colors',
        'reportlab.lib.pagesizes',
        'reportlab.lib.styles',
        'reportlab.lib.units',
        'reportlab.lib.enums',
        'reportlab.platypus',
        'reportlab.platypus.tables',
        'reportlab.platypus.flowables',
        'reportlab.pdfgen',
        'reportlab.pdfbase',
        'reportlab.pdfbase.ttfonts',
        'sqlite3',
        'hashlib',
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'tkinter.filedialog',
        'PIL',
        'PIL.Image',
        'database',
        'pdf_generator',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy', 'pandas'],
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
    name='NominaApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # Sin consola (modo GUI)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # Agregar ruta a .ico si se tiene
    version=None,
)
