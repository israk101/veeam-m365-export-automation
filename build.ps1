$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --uac-admin `
    --name VeeamM365RestoreTester `
    --icon "assets\app-icon.ico" `
    --version-file "version_info.txt" `
    --add-data "scripts\Invoke-SimpleM365BackupRestoreTest.ps1;scripts" `
    --add-data "scripts\Get-VeeamM365Inventory.ps1;scripts" `
    --add-data "scripts\RestoreSampleHelpers.ps1;scripts" `
    --add-data "assets\app-icon.ico;assets" `
    --add-data "assets\app-icon.svg;assets" `
    --add-data "assets\logos_logo.png;assets" `
    --add-data "assets\logos_footer.png;assets" `
    --add-data "assets\logos_icon.png;assets" `
    --add-data "assets\workload_email.png;assets" `
    --add-data "assets\workload_onedrive.png;assets" `
    --add-data "assets\workload_sharepoint.png;assets" `
    --add-data "assets\nav_dashboard.svg;assets" `
    --add-data "assets\nav_run.svg;assets" `
    --add-data "assets\nav_reports.svg;assets" `
    --add-data "assets\nav_settings.svg;assets" `
    --add-data "assets\status_verified.svg;assets" `
    --add-data "assets\status_attention.svg;assets" `
    --add-data "assets\status_idle.svg;assets" `
    --add-data "assets\checkbox_checked.svg;assets" `
    --add-data "assets\checkbox_dash.svg;assets" `
    main.py

Write-Host "Built: $ProjectRoot\dist\VeeamM365RestoreTester\VeeamM365RestoreTester.exe"
