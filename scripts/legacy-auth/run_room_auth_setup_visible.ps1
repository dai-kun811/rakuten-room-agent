$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$stateDir = Join-Path $repo '.local\room-worker'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
Start-Transcript -Path (Join-Path $stateDir 'room_auth_setup.log') -Force
$python = Join-Path $repo '.venv\Scripts\python.exe'
$script = Join-Path $PSScriptRoot 'room_auth_setup_visible.py'
try {
    & $python $script
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Authentication state setup failed. Review the message above.' -ForegroundColor Red
    } else {
        Write-Host 'Authentication state saved. Return to Codex.' -ForegroundColor Green
    }
} finally {
    Stop-Transcript
}
Read-Host 'Press Enter to close'
