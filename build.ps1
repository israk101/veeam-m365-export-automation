param([string]$PythonPath = '', [switch]$Incremental)
$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
if (-not $PythonPath) {
    $localPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $PythonPath = if (Test-Path -LiteralPath $localPython) { $localPython } else { 'python' }
}
$buildArgs = @('-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed', '--uac-admin',
    '--name', 'VeeamM365RestoreTester', '--distpath', 'portable', '--workpath', 'build',
    '--specpath', 'build', '--icon', "$ProjectRoot\assets\app-icon.ico",
    '--version-file', "$ProjectRoot\version_info.txt")
if (-not $Incremental) { $buildArgs += '--clean' }
# QWidget UI and WebEngineWidgets do not use QML Python bindings/import trees.
# PyInstaller still resolves DLL dependencies needed by Qt WebEngine itself.
foreach ($module in @('PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets')) {
    $buildArgs += @('--exclude-module', $module)
}
foreach ($folder in @('assets', 'scripts')) {
    foreach ($file in Get-ChildItem -LiteralPath "$ProjectRoot\$folder" -File) {
        $buildArgs += @('--add-data', "$($file.FullName);$folder")
    }
}
$buildArgs += "$ProjectRoot\main.py"
& $PythonPath @buildArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }
& $PythonPath "$ProjectRoot\tools\package_portable.py"
if ($LASTEXITCODE -ne 0) { throw "Portable packaging failed: $LASTEXITCODE" }
