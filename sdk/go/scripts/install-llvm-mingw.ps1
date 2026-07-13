# Install native llvm-mingw (UCRT) for Windows ARM64 Go CGO on GitHub Actions.
# Chocolatey does not ship llvm-mingw; download a pinned release instead.
param(
    [string]$Version = '20260616',
    [ValidateSet('aarch64', 'x86_64')]
    [string]$Arch = 'aarch64'
)

$ErrorActionPreference = 'Stop'

$asset = "llvm-mingw-$Version-ucrt-$Arch.zip"
$url = "https://github.com/mstorsjo/llvm-mingw/releases/download/$Version/$asset"
$work = Join-Path $env:RUNNER_TEMP 'llvm-mingw-install'
$zip = Join-Path $work $asset

New-Item -ItemType Directory -Force -Path $work | Out-Null

Write-Host "Downloading $url"
Invoke-WebRequest -Uri $url -OutFile $zip

$extract = Join-Path $work 'root'
New-Item -ItemType Directory -Force -Path $extract | Out-Null
Expand-Archive -Path $zip -DestinationPath $extract -Force

$root = Get-ChildItem -Path $extract -Directory | Select-Object -First 1
if (-not $root) {
    throw "llvm-mingw archive did not contain a root directory"
}

$bin = Join-Path $root.FullName 'bin'
$gcc = Join-Path $bin 'aarch64-w64-mingw32-gcc.exe'
if ($Arch -eq 'x86_64') {
    $gcc = Join-Path $bin 'x86_64-w64-mingw32-gcc.exe'
}
if (-not (Test-Path $gcc)) {
    throw "expected compiler missing in llvm-mingw bin: $gcc"
}

Write-Host "llvm-mingw root: $($root.FullName)"
Write-Host "llvm-mingw bin:  $bin"

if ($env:GITHUB_PATH) {
    Add-Content -Path $env:GITHUB_PATH -Value $bin
}
else {
    $env:PATH = "$bin;$env:PATH"
}
