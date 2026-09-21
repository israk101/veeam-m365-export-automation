# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('scripts\\Invoke-SimpleM365BackupRestoreTest.ps1', 'scripts'), ('scripts\\Get-VeeamM365Inventory.ps1', 'scripts'), ('scripts\\RestoreSampleHelpers.ps1', 'scripts'), ('assets\\app-icon.ico', 'assets'), ('assets\\app-icon.svg', 'assets'), ('assets\\logos_logo.png', 'assets'), ('assets\\logos_footer.png', 'assets'), ('assets\\logos_icon.png', 'assets'), ('assets\\workload_email.png', 'assets'), ('assets\\workload_onedrive.png', 'assets'), ('assets\\workload_sharepoint.png', 'assets'), ('assets\\nav_dashboard.svg', 'assets'), ('assets\\nav_run.svg', 'assets'), ('assets\\nav_reports.svg', 'assets'), ('assets\\nav_settings.svg', 'assets'), ('assets\\status_verified.svg', 'assets'), ('assets\\status_attention.svg', 'assets'), ('assets\\status_idle.svg', 'assets'), ('assets\\checkbox_checked.svg', 'assets'), ('assets\\checkbox_dash.svg', 'assets')],
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
    name='VeeamM365RestoreTester',
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
    version='version_info.txt',
    uac_admin=True,
    icon=['assets\\app-icon.ico'],
)
