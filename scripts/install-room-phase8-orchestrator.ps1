[CmdletBinding()]
param(
    [string[]]$RunTimes = @("07:00", "08:00", "08:20", "12:00", "12:20", "19:00", "19:20", "20:30"),
    [switch]$Preview
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runner = Join-Path $projectRoot "src\room_phase8_runner.py"
$taskName = "RakutenROOMOrchestrator"
$legacyTasks = @(
    "RakutenROOMDailyEngagement",
    "RakutenROOMGenerationGuard",
    "RakutenROOMAutoPoster",
    "RakutenROOMPostGuard"
)

foreach ($path in @($python, $runner)) {
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

if ($Preview) {
    [pscustomobject]@{
        TaskName = $taskName
        Times = ($RunTimes -join ", ")
        LegacyTasks = "disabled"
        MultipleInstances = "IgnoreNew"
        ExecutionLimitMinutes = 45
        State = "Preview"
    }
    return
}

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Get-ScheduledTask -TaskName $taskName, $legacyTasks -ErrorAction SilentlyContinue |
    Select-Object TaskName, State
