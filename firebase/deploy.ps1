param([Parameter(Mandatory=$true)][string]$ProjectId)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
& firebase deploy --project $ProjectId --only firestore:rules
if ($LASTEXITCODE -ne 0) { throw 'Nie potwierdzono wdrożenia reguł Firestore.' }
