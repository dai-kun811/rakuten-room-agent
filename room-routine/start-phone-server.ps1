$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $scriptDir "server.mjs"

node $server
