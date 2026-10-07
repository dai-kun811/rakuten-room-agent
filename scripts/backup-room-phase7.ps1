[CmdletBinding()]
param(
    [string]$Label = (Get-Date -Format "yyyyMMdd-HHmmss")
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $projectRoot ".local\room-worker"
$backupDir = Join-Path $stateDir "rollback\$Label"
$taskNames = @(
    "RakutenROOMDailyEngagement",
    "RakutenROOMGenerationGuard",
    "RakutenROOMAutoPoster",
    "RakutenROOMPostGuard"
)

New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
foreach ($taskName in $taskNames) {
    $xml = Export-ScheduledTask -TaskName $taskName
    $xmlPath = Join-Path $backupDir ($taskName + ".xml")
    [System.IO.File]::WriteAllText($xmlPath, $xml, [System.Text.UTF8Encoding]::new($false))
}

foreach ($name in @("operations.db", "operations.db-wal", "operations.db-shm", "post-ledger.jsonl")) {
    $source = Join-Path $stateDir $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $backupDir $name)
    }
}

$manifest = foreach ($file in Get-ChildItem -LiteralPath $backupDir -File) {
    [pscustomobject]@{
        Name = $file.Name
        Length = $file.Length
        Sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
    }
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backupDir "manifest.json") -Encoding UTF8
Write-Output $backupDir
