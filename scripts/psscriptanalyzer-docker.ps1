# Run PSScriptAnalyzer inside an official Microsoft PowerShell container.
# Invoked by scripts/psscriptanalyzer-docker.sh (pre-commit docker hooks).
[CmdletBinding(DefaultParameterSetName = 'Check')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Check', Position = 0)]
    [switch]$Check,
    [Parameter(ParameterSetName = 'Check')]
    [string[]]$ExcludeRule,
    [Parameter(ParameterSetName = 'Check')]
    [switch]$Fix,
    [Parameter(Mandatory = $true, ParameterSetName = 'Format', Position = 0)]
    [switch]$Format,
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
    [string[]]$Path
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) {
    Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force -AllowClobber
}

Import-Module PSScriptAnalyzer

if ($Check) {
    $params = @{
        EnableExit = $true
    }
    if ($ExcludeRule) {
        $params.ExcludeRule = @($ExcludeRule)
    }
    if ($Fix) {
        $params.Fix = $true
    }
    $Path | Invoke-ScriptAnalyzer @params
}
elseif ($Format) {
    foreach ($file in $Path) {
        $content = (Get-Content -Raw -Path $file).Trim()
        Invoke-Formatter -ScriptDefinition $content | Out-File -FilePath $file
    }
}
else {
    throw 'Specify -Check or -Format.'
}
