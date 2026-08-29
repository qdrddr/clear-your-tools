# Install Windows prerequisites for local prek / pre-commit hooks.
# Run from an elevated PowerShell if winget MSI installers prompt for UAC.
param(
    [switch]$SkipWinget,
    [switch]$SkipDocker
)

$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path

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
$llvmMingwRoot = Join-Path $toolsRoot 'llvm-mingw'
$ninjaDir = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\Ninja-build.Ninja_Microsoft.Winget.Source_8wekyb3d8bbwe'

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

Write-Host '==> Ensuring jq (export-rust-sbom / verify-pins)'
if (-not (Get-Command jq -ErrorAction SilentlyContinue) -and -not $SkipWinget) {
    winget install --id jqlang.jq --exact --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-Null
}

Write-Host '==> Ensuring Go Task (all-fallow / Taskfile hooks)'
if (-not (Get-Command task -ErrorAction SilentlyContinue) -and -not $SkipWinget) {
    winget install --id Task.Task --exact --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-Null
}

Write-Host '==> Ensuring Ninja (Windows compile_commands.json)'
if (-not (Get-Command ninja -ErrorAction SilentlyContinue) -and -not $SkipWinget) {
    winget install --id Ninja-build.Ninja --exact --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-Null
}

Write-Host '==> Ensuring GNU make (local-dev-sdk-c)'
if (-not (Get-Command make -ErrorAction SilentlyContinue) -and -not $SkipWinget) {
    winget install --id ezwinports.make --exact --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-Null
}

Write-Host '==> Ensuring llvm-mingw UCRT x86_64 (Go CGO: gendef/dlltool)'
if (-not (Test-Path (Join-Path $llvmMingwRoot 'bin\gendef.exe'))) {
    $llvmMingwVersion = '20260616'
    $asset = "llvm-mingw-$llvmMingwVersion-ucrt-x86_64.zip"
    $url = "https://github.com/mstorsjo/llvm-mingw/releases/download/$llvmMingwVersion/$asset"
    $work = Join-Path $env:TEMP 'llvm-mingw-install'
    $zip = Join-Path $work $asset
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    Invoke-WebRequest -Uri $url -OutFile $zip
    if (Test-Path $llvmMingwRoot) { Remove-Item -Recurse -Force $llvmMingwRoot }
    Expand-Archive -Path $zip -DestinationPath $work -Force
    $root = Get-ChildItem -Path $work -Directory | Where-Object { $_.Name -like 'llvm-mingw-*' } | Select-Object -First 1
    Move-Item $root.FullName $llvmMingwRoot
}

Write-Host '==> Ensuring shfmt + ast-grep (local hooks)'
$shfmt = Join-Path $localBin 'shfmt.exe'
if (-not (Test-Path $shfmt)) {
    Invoke-WebRequest -Uri 'https://github.com/mvdan/sh/releases/download/v3.13.1/shfmt_v3.13.1_windows_amd64.exe' -OutFile $shfmt
}
Write-Host '==> Ensuring ast-grep-cli 0.41.0 + basedpyright (matches CI / prek hooks)'
Push-Location $RepoRoot
try {
    uv sync --all-extras | Out-Null
    uv pip install 'ast-grep-cli==0.41.0' basedpyright | Out-Null
}
finally {
    Pop-Location
}

Write-Host '==> Ensuring cargo SBOM / audit CLIs (verify-pins, export-rust-sbom)'
if (-not (Get-Command cargo-cyclonedx -ErrorAction SilentlyContinue)) {
    cargo install cargo-cyclonedx --locked --version 0.5.9 | Out-Null
}
if (-not (Get-Command cargo-deny -ErrorAction SilentlyContinue)) {
    cargo install cargo-deny --locked --version 0.19.8 | Out-Null
}
if (-not (Get-Command cargo-audit -ErrorAction SilentlyContinue)) {
    cargo install cargo-audit --locked | Out-Null
}
if (-not (Get-Command cargo-udeps -ErrorAction SilentlyContinue)) {
    cargo install cargo-udeps --locked | Out-Null
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
    (Join-Path $llvmMingwRoot 'bin'),
    (Join-Path $llvmMingwRoot 'x86_64-w64-mingw32\lib'),
    $ninjaDir,
    "${env:ProgramFiles}\LLVM\bin",
    "${env:ProgramFiles}\Cppcheck",
    "${env:ProgramFiles}\Go\bin"
)

Write-Host '==> Ensuring npm deps (fallow + TypeScript SDK + e2e/typescript)'
Push-Location $RepoRoot
try {
    npm ci | Out-Null
    Push-Location (Join-Path $RepoRoot 'sdk\typescript')
    try {
        npm ci | Out-Null
    }
    finally {
        Pop-Location
    }
    $gitBashForE2e = (Get-Command bash -ErrorAction SilentlyContinue).Source
    if ($gitBashForE2e) {
        $tsVersion = (Get-Content (Join-Path $RepoRoot 'sdk\typescript\package.json') -Raw | ConvertFrom-Json).version
        & $gitBashForE2e -lc "export CYT_RELEASE_VERSION='$tsVersion' CYT_E2E_USE_WORKSPACE=1; bash sdk/e2e/scripts/render-manifests.sh"
        Push-Location (Join-Path $RepoRoot 'sdk\e2e\typescript')
        try {
            if (Test-Path 'package-lock.json') { npm ci | Out-Null } else { npm install | Out-Null }
        }
        finally {
            Pop-Location
        }
    }
}
finally {
    Pop-Location
}

Write-Host '==> Ensuring Go SDK linter binaries'
$gitBash = (Get-Command bash -ErrorAction SilentlyContinue).Source
if ($gitBash) {
    & $gitBash -lc 'source scripts/pre-commit-hooks/go-sdk-tools.sh; ensure_go_sdk_tool gofumpt "$GO_SDK_TOOL_GOFUMPT"; ensure_go_sdk_tool goimports "$GO_SDK_TOOL_GOIMPORTS"; ensure_go_critic_tool; ensure_go_sdk_tool gosec "$GO_SDK_TOOL_GOSEC"; ensure_go_sdk_tool staticcheck "$GO_SDK_TOOL_STATICCHECK"'
}

Write-Host '==> Ensuring verify-pins / SBOM helper CLIs'
if ($gitBash) {
    & $gitBash (Join-Path $RepoRoot 'scripts\ci\install-verify-pins-tools.sh')
    & $gitBash (Join-Path $RepoRoot 'scripts\local\dev\heal-cargo-lock.sh')
}

Write-Host '==> Installing prek git hooks'
Push-Location $RepoRoot
try {
    uv run prek install | Out-Null
}
finally {
    Pop-Location
}

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

Write-Host ''
Write-Host 'Windows hook setup complete. Open a new terminal, then from the repo root:'
Write-Host '  .\.venv\Scripts\activate   # or rely on activate-venv.sh in prek hooks'
Write-Host '  scripts\pre-commit-hooks\prek-loop.cmd --short --one-run'
Write-Host ''
Write-Host 'Optional: Docker Desktop for ShellCheck/PSSA docker paths (winget install Docker.DockerDesktop)'
