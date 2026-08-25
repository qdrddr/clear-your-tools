# Install Windows prerequisites for local prek / pre-commit hooks.
# Run from an elevated PowerShell if winget MSI installers prompt for UAC.
param(
    [switch]$SkipWinget,
    [switch]$SkipDocker
)

$ErrorActionPreference = 'Stop'

function Add-UserPath([string[]]$Dirs) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    foreach ($dir in $Dirs) {
        if ((Test-Path $dir) -and ($userPath -notlike "*$dir*")) {
            $userPath = "$dir;$userPath"
        }
    }
    [Environment]::SetEnvironmentVariable('Path', $userPath, 'User')
}

$localBin = Join-Path $env:USERPROFILE '.local\bin'
$toolsRoot = Join-Path $env:USERPROFILE 'tools'
$goRoot = Join-Path $env:USERPROFILE 'go-sdk\go'
$llvmRoot = Join-Path $toolsRoot 'llvm'
$cmakeRoot = Join-Path $toolsRoot 'cmake'

New-Item -ItemType Directory -Force -Path $localBin | Out-Null
New-Item -ItemType Directory -Force -Path $toolsRoot | Out-Null

Write-Host '==> Ensuring uv / prek (Python hook runner)'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    winget install --id astral-sh.uv --exact --accept-package-agreements --accept-source-agreements --disable-interactivity
}

Write-Host '==> Ensuring Git Bash (hooks invoke bash scripts)'
if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
    winget install --id Git.Git --exact --accept-package-agreements --accept-source-agreements --disable-interactivity
}

Write-Host '==> Ensuring Rust (cargo / maturin hooks)'
if (-not (Get-Command rustup -ErrorAction SilentlyContinue)) {
    winget install --id Rustlang.Rustup --exact --accept-package-agreements --accept-source-agreements --disable-interactivity
}
if (Get-Command rustup -ErrorAction SilentlyContinue) {
    rustup default stable | Out-Null
}

Write-Host '==> Ensuring Go (sdk/go hooks)'
if (-not (Test-Path (Join-Path $goRoot 'bin\go.exe'))) {
    $goZip = Join-Path $env:TEMP 'go.zip'
    Invoke-WebRequest -Uri 'https://go.dev/dl/go1.26.7.windows-amd64.zip' -OutFile $goZip
    if (Test-Path (Split-Path $goRoot)) { Remove-Item -Recurse -Force (Split-Path $goRoot) }
    Expand-Archive -Path $goZip -DestinationPath (Split-Path $goRoot) -Force
    $extracted = Get-ChildItem (Split-Path $goRoot) -Directory | Select-Object -First 1
    if ($extracted.Name -ne 'go') {
        Rename-Item $extracted.FullName $goRoot
    }
}

Write-Host '==> Ensuring Node.js (TypeScript SDK hooks)'
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    winget install --id OpenJS.NodeJS.LTS --exact --accept-package-agreements --accept-source-agreements --disable-interactivity
}

Write-Host '==> Ensuring PowerShell 7 (PSScriptAnalyzer local fallback)'
if (-not (Get-Command pwsh -ErrorAction SilentlyContinue)) {
    winget install --id Microsoft.PowerShell --exact --accept-package-agreements --accept-source-agreements --disable-interactivity
}

Write-Host '==> Ensuring shfmt + ast-grep (local hooks)'
$shfmt = Join-Path $localBin 'shfmt.exe'
if (-not (Test-Path $shfmt)) {
    Invoke-WebRequest -Uri 'https://github.com/mvdan/sh/releases/download/v3.13.1/shfmt_v3.13.1_windows_amd64.exe' -OutFile $shfmt
}
Write-Host '==> Ensuring ast-grep-cli 0.41.0 (matches CI; in project venv)'
Push-Location $RepoRoot
try {
    uv pip install 'ast-grep-cli==0.41.0' | Out-Null
} finally {
    Pop-Location
}

Write-Host '==> Ensuring ShellCheck (shellcheck hook local fallback)'
if (-not (Get-Command shellcheck -ErrorAction SilentlyContinue)) {
    winget install --id koalaman.shellcheck --exact --accept-package-agreements --accept-source-agreements --disable-interactivity
}

Write-Host '==> Ensuring portable CMake (sdk/c compile_commands.json hook)'
if (-not (Test-Path (Join-Path $cmakeRoot 'bin\cmake.exe'))) {
    $cmakeZip = Join-Path $env:TEMP 'cmake.zip'
    Invoke-WebRequest -Uri 'https://github.com/Kitware/CMake/releases/download/v4.4.2/cmake-4.4.2-windows-x86_64.zip' -OutFile $cmakeZip
    Expand-Archive -Path $cmakeZip -DestinationPath $toolsRoot -Force
    $dir = Get-ChildItem $toolsRoot -Directory | Where-Object { $_.Name -like 'cmake-*' } | Select-Object -First 1
    Rename-Item $dir.FullName $cmakeRoot
}

Write-Host '==> Ensuring LLVM (clang-format / clang-tidy C SDK hooks)'
if (-not (Test-Path (Join-Path $llvmRoot 'bin\clang-format.exe'))) {
    if (-not $SkipWinget) {
        winget install --id LLVM.LLVM --exact --accept-package-agreements --accept-source-agreements --disable-interactivity
    }
    if (-not (Test-Path (Join-Path $llvmRoot 'bin\clang-format.exe'))) {
        $llvmExe = Join-Path $env:TEMP 'LLVM-win64.exe'
        Invoke-WebRequest -Uri 'https://github.com/llvm/llvm-project/releases/download/llvmorg-22.1.8/LLVM-22.1.8-win64.exe' -OutFile $llvmExe
        New-Item -ItemType Directory -Force -Path $llvmRoot | Out-Null
        Start-Process -FilePath $llvmExe -ArgumentList "/S", "/D=$llvmRoot" -Wait
    }
}

Write-Host '==> Ensuring cppcheck (C SDK hook; cpplint falls back to uv run cpplint)'
if (-not (Get-Command cppcheck -ErrorAction SilentlyContinue) -and -not $SkipWinget) {
    winget install --id Cppcheck.Cppcheck --exact --accept-package-agreements --accept-source-agreements --disable-interactivity
}

if (-not $SkipDocker) {
    Write-Host '==> Optional: Docker Desktop (ShellCheck + PSScriptAnalyzer docker paths)'
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host '    Skipping auto-install (large). Install manually: winget install Docker.DockerDesktop'
    }
}

Add-UserPath @(
    $localBin,
    (Join-Path $env:USERPROFILE '.cargo\bin'),
    (Join-Path $goRoot 'bin'),
    (Join-Path $cmakeRoot 'bin'),
    (Join-Path $llvmRoot 'bin'),
    "${env:ProgramFiles}\LLVM\bin",
    "${env:ProgramFiles}\Cppcheck",
    "${env:ProgramFiles}\Go\bin"
)

Write-Host '==> Ensuring rtk (optional output shortener used by many local hooks)'
$rtk = Join-Path $localBin 'rtk.exe'
if (-not (Test-Path $rtk)) {
    $rtkZip = Join-Path $env:TEMP 'rtk.zip'
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/rtk-ai/rtk/releases/latest'
    $asset = $release.assets | Where-Object { $_.name -eq 'rtk-x86_64-pc-windows-msvc.zip' } | Select-Object -First 1
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $rtkZip
    $rtkDir = Join-Path $env:TEMP 'rtk-extract'
    Expand-Archive -Path $rtkZip -DestinationPath $rtkDir -Force
    Copy-Item (Join-Path $rtkDir 'rtk.exe') $rtk -Force
}

Write-Host '==> Optional: ripgrep (rtk filters)'
if (-not (Get-Command rg -ErrorAction SilentlyContinue)) {
    winget install --id BurntSushi.ripgrep.MSVC --exact --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-Null
}

Write-Host '  uv sync --all-extras'
Write-Host '  prek install'
Write-Host '  prek run -a'
Write-Host ''
Write-Host 'Optional: rtk (output shortener) - brew install rtk on macOS; hooks work without it.'
