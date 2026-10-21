$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$buildPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create a Python virtual environment.' }
}
& $buildPython -m pip install -e '.[dev,sync]'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $buildPython -m PyInstaller Spedi.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
$publicBundle = Join-Path $PSScriptRoot 'dist/Spedi'
foreach ($notice in @('LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'README.pl.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $notice) -Destination $publicBundle -Force
}
Write-Host "Built: $publicBundle/Spedi.exe"
Write-Host 'This bundle contains no user databases or service configuration.'
Write-Host 'Inno Setup: installer/Spedi.iss'
