param(
    [string]$PythonPath = '',
    [switch]$SkipDependencies,
    [switch]$Installer,
    [switch]$Portable
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$buildPython = if ($PythonPath) { $PythonPath } else { Join-Path $PSScriptRoot '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $buildPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create a Python virtual environment.' }
}
if (-not $SkipDependencies) {
    & $buildPython -m pip install -e '.[dev,sync]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if (-not (Test-Path -LiteralPath 'work/local-tools/tesseract/tesseract.exe')) {
    & $buildPython tools/setup_tesseract.py
    if ($LASTEXITCODE -ne 0) { throw 'Offline OCR setup failed.' }
}
& $buildPython -m PyInstaller Spedi.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
$publicBundle = Join-Path $PSScriptRoot 'dist/Spedi'
foreach ($notice in @('LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'README.pl.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $notice) -Destination $publicBundle -Force
}
Write-Host "Built: $publicBundle/Spedi.exe"
Write-Host 'This bundle contains no user databases or service configuration.'
$report = Join-Path $PSScriptRoot 'work/frozen-self-test.json'
$check = Start-Process -FilePath (Join-Path $publicBundle 'Spedi.exe') -ArgumentList @('--self-test-report', ('"' + $report + '"')) -WindowStyle Hidden -Wait -PassThru
if ($check.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $report)) { throw 'Frozen application self-test failed.' }
$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
if (-not $result.ok) { throw 'Frozen application self-test failed.' }
if ($Installer) {
    $compiler = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6/ISCC.exe'
    if (-not (Test-Path -LiteralPath $compiler)) { throw 'Install Inno Setup 6 to build the installer.' }
    $version = (& $buildPython -c 'from spedi import __version__; print(__version__)').Trim()
    & $compiler "/DMyAppVersion=$version" installer/Spedi.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
}
if ($Portable) {
    & $buildPython tools/package_windows.py
    if ($LASTEXITCODE -ne 0) { throw 'Portable ZIP packaging failed.' }
}
