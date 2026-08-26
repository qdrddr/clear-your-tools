# Simulate Cursor beforeSubmitPrompt via local cyt-client + hook daemon (Windows).
#
# Usage:
#   powershell -File scripts/cursor/simulate-cursor-hook.ps1
#   powershell -File scripts/cursor/simulate-cursor-hook.ps1 -SessionId "test-id" -Prompt "BM25 pipeline?"
param(
    [string]$SessionId = "dbd9f486-d3c9-44aa-a35a-2571841113d9",
    [string]$Prompt = "Where is my primary BM25 pruning pipeline located in codebase?",
    [int]$Runs = 1,
    [switch]$Fresh
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

Write-Host "==> Restarting hook daemon from repo source"
& uv run src/cyt/proxy/cli.py hook daemon restart --unattended

Write-Host "==> Waiting for daemon"
$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:8834/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $healthy = $true
        break
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $healthy) {
    throw "Timed out waiting for hook daemon on http://127.0.0.1:8834/health"
}

$SessionDir = Join-Path $RepoRoot ".cursor\cyt\sessions"
$SessionLog = Join-Path $SessionDir "$SessionId.jsonl"
$RulesFile = Join-Path $RepoRoot ".cursor\rules\cyt-injection.mdc"

Write-Host "==> Session log: $SessionLog"
New-Item -ItemType Directory -Force -Path $SessionDir | Out-Null
if ($Fresh) {
    '{"type":"meta","agent":"cursor"}' | Set-Content -Path $SessionLog -Encoding utf8
    Write-Host "    (cleared previous entries; kept meta line)"
}

function Invoke-CytHook {
    param([string]$RunPrompt)
    $generationId = [guid]::NewGuid().ToString()
    Write-Host ""
    Write-Host "---- hook run: $RunPrompt ----"
    $payload = @{
        conversation_id = $SessionId
        generation_id = $generationId
        model = "composer-2.5-fast"
        model_id = "composer-2.5"
        composer_mode = "agent"
        prompt = $RunPrompt
        session_id = $SessionId
        hook_event_name = "beforeSubmitPrompt"
        cursor_version = "3.12.17"
        workspace_roots = @($RepoRoot)
    } | ConvertTo-Json -Depth 6 -Compress
    $payload | & uv run src/cyt_client/cli.py
}

for ($i = 1; $i -le $Runs; $i++) {
    Invoke-CytHook "$Prompt (run $i)"
}

Write-Host ""
Write-Host "==> Assertions"
if (-not (Test-Path $SessionLog)) {
    throw "Expected session log at $SessionLog"
}
Write-Host "OK session log exists: $SessionLog"
if (Test-Path $RulesFile) {
    Write-Host "OK rules file exists: $RulesFile"
} else {
    Write-Warning "Rules file not created at $RulesFile (BM25 may have produced empty injection)"
}

$aggregator = Join-Path $env:USERPROFILE ".config\cyt\mcp-aggregator.yaml"
$mcpJson = Join-Path $env:USERPROFILE ".cursor\mcp.json"
if (Test-Path $aggregator) {
    Write-Host "OK mcp aggregator config: $aggregator"
} else {
    Write-Warning "Missing mcp aggregator at $aggregator (run: cyt hook cursor)"
}
if (Test-Path $mcpJson) {
    $mcp = Get-Content $mcpJson -Raw | ConvertFrom-Json
    if ($mcp.mcpServers.'cyt-mcp') {
        Write-Host "OK cyt-mcp registered in $mcpJson"
    } else {
        Write-Warning "cyt-mcp not found in $mcpJson"
    }
}

Write-Host ""
Write-Host "Done."
