[CmdletBinding()]
param(
    [string[]]$RunTimes = @(
        "07:10", "07:20", "07:35",
        "08:05", "08:12",
        "09:10",
        "12:05", "12:12",
        "19:05", "19:12",
        "20:10"
    ),
    [switch]$Preview
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$worker = Join-Path $projectRoot "src\room_x_schedule_worker.py"
$catalog = Join-Path $projectRoot "config\room-x-helpful-cards.json"
$tokenStore = Join-Path $projectRoot ".local\room-worker\x-publisher\oauth-token.dpapi"
$taskName = "RakutenROOMXPublisher"

foreach ($path in @($python, $worker, $catalog)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required file was not found: $path"
    }
}

if ($Preview) {
    [pscustomobject]@{
        TaskName = $taskName
        Times = ($RunTimes -join ", ")
        MultipleInstances = "IgnoreNew"
        StartWhenAvailable = $false
        ExecutionLimitMinutes = 10
        TokenStoreConfigured = Test-Path -LiteralPath $tokenStore
        ClientIdConfigured = -not [string]::IsNullOrWhiteSpace($env:X_OAUTH_CLIENT_ID)
        TrialStartDate = $env:X_TRIAL_START_DATE
        TrialEndDate = $env:X_TRIAL_END_DATE
        ApprovedMonthlyBudgetUsd = $env:X_APPROVED_MONTHLY_BUDGET_USD
        State = "Preview"
    }
    return
}

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ('"{0}" --apply' -f $worker) `
    -WorkingDirectory $projectRoot
$triggers = foreach ($runTime in $RunTimes) {
    New-ScheduledTaskTrigger -Daily -At $runTime
}
$task = New-ScheduledTask `
    -Action $action `
    -Trigger $triggers `
    -Principal $principal `
    -Settings $settings `
    -Description "Official X API publisher: four reviewed helpful posts and three ROOM-linked promotions per week."

if (-not (Test-Path -LiteralPath $tokenStore)) {
    throw "X OAuth token store is not configured. Run the approved OAuth setup first."
}
if ([string]::IsNullOrWhiteSpace($env:X_OAUTH_CLIENT_ID)) {
    throw "X_OAUTH_CLIENT_ID is not configured for the current user."
}
foreach ($name in @("X_TRIAL_START_DATE", "X_TRIAL_END_DATE", "X_APPROVED_MONTHLY_BUDGET_USD")) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
        throw "$name is not configured for the current user."
    }
}

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
