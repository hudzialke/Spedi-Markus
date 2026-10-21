$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path -LiteralPath 'app/google-services.json')) {
    throw 'Configure your own Firebase Android app and save app/google-services.json first.'
}
if (-not $env:JAVA_HOME) {
    throw 'Set JAVA_HOME to your JDK 17 installation.'
}
& ./gradlew.bat testDebugUnitTest lintDebug assembleDebug --console=plain --no-daemon
if ($LASTEXITCODE -ne 0) { throw 'Android build or verification failed.' }
