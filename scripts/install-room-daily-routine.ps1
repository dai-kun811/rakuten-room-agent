[CmdletBinding()]
param(
    [string]$EngagementTime = "05:10",
    [string[]]$PostTimes = @("08:15", "12:15", "18:15"),
    [switch]$Preview
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$engagementWorker = Join-Path $projectRoot "src\room_engagement_worker.py"
$postWorker = Join-Path $projectRoot "src\local_room_worker.py"

foreach ($path in @($python, $engagementWorker, $postWorker)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required file was not found: $path"
    }
}

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$engagementAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ('"{0}" --apply --headful' -f $engagementWorker) `
    -WorkingDirectory $projectRoot
$engagementTrigger = New-ScheduledTaskTrigger -Daily -At $EngagementTime
$engagementTask = New-ScheduledTask `
    -Action $engagementAction `
    -Trigger $engagementTrigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Run the daily Rakuten ROOM follow and like routine up to 50 each."

$postAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ('"{0}"' -f $postWorker) `
    -WorkingDirectory $projectRoot
$postTriggers = foreach ($time in $PostTimes) {
    New-ScheduledTaskTrigger -Daily -At $time
}
$postTask = New-ScheduledTask `
    -Action $postAction `
    -Trigger $postTriggers `
    -Principal $principal `
    -Settings $settings `
    -Description "Post one Rakuten ROOM item in each morning, noon, and evening slot."

if ($Preview) {
    [pscustomobject]@{ TaskName = "RakutenROOMDailyEngagement"; Times = $EngagementTime; State = "Preview" }
    [pscustomobject]@{ TaskName = "RakutenROOMAutoPoster"; Times = ($PostTimes -join ", "); State = "Preview" }
} else {
    Register-ScheduledTask -TaskName "RakutenROOMDailyEngagement" -InputObject $engagementTask -Force | Out-Null
    Register-ScheduledTask -TaskName "RakutenROOMAutoPoster" -InputObject $postTask -Force | Out-Null
    Get-ScheduledTask -TaskName "RakutenROOMDailyEngagement", "RakutenROOMAutoPoster" |
        Select-Object TaskName, State
}
