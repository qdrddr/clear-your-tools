# Dev checkout convenience wrapper — canonical script ships in src/cyt/hook/.
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$wrapper = Join-Path $PSScriptRoot "..\src\cyt\hook\uv.ps1"
& $wrapper @Args
exit $LASTEXITCODE
