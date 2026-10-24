[CmdletBinding()]
param(
    [string[]]$RunTimes = @("07:00", "08:00", "08:20", "12:00", "12:20", "19:00", "19:20", "20:30"),
    [string]$EngagementTime = "05:30",
    [string]$ExternalAuditTime = "20:45",
    [switch]$Phase9Only,
    [switch]$Preview
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runner = Join-Path $projectRoot "src\room_phase8_runner.py"
$engagementWorker = Join-Path $projectRoot "src\room_engagement_worker.py"
$externalAudit = Join-Path $projectRoot "src\room_external_audit.py"
$taskName = "RakutenROOMOrchestrator"
$legacyTasks = @(
    "RakutenROOMDailyEngagement",
    "RakutenROOMGenerationGuard",
    "RakutenROOMAutoPoster",
    "RakutenROOMPostGuard"
)

foreach ($path in @($python, $runner, $engagementWorker, $externalAudit)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required file was not found: $path"
    }
}

$enabledLegacy = @(
    Get-ScheduledTask -TaskName $legacyTasks -ErrorAction SilentlyContinue |
        Where-Object { $_.State -ne "Disabled" }
)
if ($enabledLegacy.Count -gt 0) {
    throw "Legacy ROOM tasks must remain disabled before Phase 8 ownership is enabled."
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
    -ExecutionTimeLimit (New-TimeSpan -Minutes 45)
$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ('"{0}" --apply' -f $runner) `
    -WorkingDirectory $projectRoot
$triggers = foreach ($runTime in $RunTimes) {
    New-ScheduledTaskTrigger -Daily -At $runTime
}
$task = New-ScheduledTask `
    -Action $action `
    -Trigger $triggers `
    -Principal $principal `
    -Settings $settings `
    -Description "Unified fenced ROOM generation, one-slot posting, catch-up, and 20:30 audit."

$engagementAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ('"{0}" --apply --headful' -f $engagementWorker) `
    -WorkingDirectory $projectRoot
$engagementTask = New-ScheduledTask `
    -Action $engagementAction `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $EngagementTime) `
    -Principal $principal `
    -Settings $settings `
    -Description "Bounded ROOM follow/like routine; SQLite gated 05:30-07:30 and non-blocking profile lock."

$auditAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ('"{0}"' -f $externalAudit) `
    -WorkingDirectory $projectRoot
$auditTask = New-ScheduledTask `
    -Action $auditAction `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $ExternalAuditTime) `
    -Principal $principal `
    -Settings $settings `
    -Description "Read-only sanitized external audit for ROOM posting and engagement state."

if ($Preview) {
    [pscustomobject]@{
        Mode = if ($Phase9Only) { "Phase9Only" } else { "Full" }
        TaskName = if ($Phase9Only) { "unchanged" } else { $taskName }
        Times = if ($Phase9Only) { "unchanged" } else { ($RunTimes -join ", ") }
        EngagementTime = $EngagementTime
        ExternalAuditTime = $ExternalAuditTime
        LegacyTasks = "disabled"
        MultipleInstances = "IgnoreNew"
        ExecutionLimitMinutes = 45
        State = "Preview"
    }
    return
}

if (-not $Phase9Only) {
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
}
Register-ScheduledTask -TaskName "RakutenROOMEngagement" -InputObject $engagementTask -Force | Out-Null
Register-ScheduledTask -TaskName "RakutenROOMExternalAudit" -InputObject $auditTask -Force | Out-Null
Get-ScheduledTask -TaskName $taskName, "RakutenROOMEngagement", "RakutenROOMExternalAudit", $legacyTasks -ErrorAction SilentlyContinue |
    Select-Object TaskName, State
