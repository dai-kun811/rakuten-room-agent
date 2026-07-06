[CmdletBinding()]
param(
    [string]$EngagementTime = "05:10",
    [string]$EngagementVerifyTime = "06:45",
    [string]$GenerationGuardTime = "07:30",
    [string[]]$PostTimes = @("08:15", "12:15", "18:15"),
    [string[]]$PostGuardTimes = @("08:30", "12:30", "18:30"),
    [switch]$Preview
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$engagementWorker = Join-Path $projectRoot "src\room_engagement_worker.py"
$postWorker = Join-Path $projectRoot "src\local_room_worker.py"
$dailyGuard = Join-Path $projectRoot "src\room_daily_guard.py"

foreach ($path in @($python, $engagementWorker, $postWorker, $dailyGuard)) {
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
$engagementTriggers = @(
    New-ScheduledTaskTrigger -Daily -At $EngagementTime
    New-ScheduledTaskTrigger -Daily -At $EngagementVerifyTime
)
$engagementTask = New-ScheduledTask `
    -Action $engagementAction `
    -Trigger $engagementTriggers `
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

$generationGuardAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ('"{0}" generation' -f $dailyGuard) `
    -WorkingDirectory $projectRoot
$generationGuardTrigger = New-ScheduledTaskTrigger -Daily -At $GenerationGuardTime
$generationGuardTask = New-ScheduledTask `
    -Action $generationGuardAction `
    -Trigger $generationGuardTrigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Ensure today's ROOM generation run exists and has all three ready slots."

$postGuardAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ('"{0}" post' -f $dailyGuard) `
    -WorkingDirectory $projectRoot
$postGuardTriggers = foreach ($time in $PostGuardTimes) {
    New-ScheduledTaskTrigger -Daily -At $time
}
$postGuardTask = New-ScheduledTask `
    -Action $postGuardAction `
    -Trigger $postGuardTriggers `
    -Principal $principal `
    -Settings $settings `
    -Description "Verify and safely recover due ROOM post slots."

if ($Preview) {
    [pscustomobject]@{ TaskName = "RakutenROOMDailyEngagement"; Times = (@($EngagementTime, $EngagementVerifyTime) -join ", "); State = "Preview" }
    [pscustomobject]@{ TaskName = "RakutenROOMGenerationGuard"; Times = $GenerationGuardTime; State = "Preview" }
    [pscustomobject]@{ TaskName = "RakutenROOMAutoPoster"; Times = ($PostTimes -join ", "); State = "Preview" }
    [pscustomobject]@{ TaskName = "RakutenROOMPostGuard"; Times = ($PostGuardTimes -join ", "); State = "Preview" }
} else {
    Register-ScheduledTask -TaskName "RakutenROOMDailyEngagement" -InputObject $engagementTask -Force | Out-Null
    Register-ScheduledTask -TaskName "RakutenROOMGenerationGuard" -InputObject $generationGuardTask -Force | Out-Null
    Register-ScheduledTask -TaskName "RakutenROOMAutoPoster" -InputObject $postTask -Force | Out-Null
    Register-ScheduledTask -TaskName "RakutenROOMPostGuard" -InputObject $postGuardTask -Force | Out-Null
    Get-ScheduledTask -TaskName "RakutenROOMDailyEngagement", "RakutenROOMGenerationGuard", "RakutenROOMAutoPoster", "RakutenROOMPostGuard" |
        Select-Object TaskName, State
}
