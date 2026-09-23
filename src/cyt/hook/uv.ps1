# Run cyt with consumer workspace env. Shipped with clear-your-tools (installed or dev).

param(

    [Parameter(ValueFromRemainingArguments = $true)]

    [string[]]$Args

)



function Get-GitWorkspaceRoot([string]$StartPath) {

    $dir = $StartPath

    while ($dir) {

        if (Test-Path (Join-Path $dir ".git")) {

            return (Resolve-Path -LiteralPath $dir).Path

        }

        $parent = Split-Path -Parent $dir

        if (-not $parent -or $parent -eq $dir) {

            break

        }

        $dir = $parent

    }

    return $null

}



function Get-CytDevRepoRoot([string]$StartDir) {

    $dir = $StartDir

    while ($dir) {

        $manifest = Join-Path $dir "pyproject.toml"

        if (Test-Path $manifest) {

            $content = Get-Content -LiteralPath $manifest -Raw -ErrorAction SilentlyContinue

            if ($content -and ($content -match 'name\s*=\s*"clear-your-tools"')) {

                return (Resolve-Path -LiteralPath $dir).Path

            }

        }

        $parent = Split-Path -Parent $dir

        if (-not $parent -or $parent -eq $dir) {

            break

        }

        $dir = $parent

    }

    return $null

}



function Test-ScriptIsPackagedCytHook([string]$ScriptRoot) {

    $normalized = $ScriptRoot.Replace('\', '/').ToLowerInvariant()

    return $normalized -match '/(site-packages/cyt/hook|src/cyt/hook)$'

}



function Read-CytInvocationSidecar {

    $path = Join-Path $PSScriptRoot 'cyt-invocation.json'

    if (-not (Test-Path -LiteralPath $path)) {

        return $null

    }

    try {

        return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json

    }

    catch {

        return $null

    }

}



function Invoke-CytViaUv {

    param([string[]]$CytArgs)



    if ($CytArgs.Count -eq 0) {

        Write-Error "Usage: uv.ps1 hook cursor  (or other cyt subcommands)"

        exit 1

    }



    $usesDevCli = $false

    $first = [string]$CytArgs[0]

    if ($first -match 'cli[\\/]app\.py$') {

        $usesDevCli = $true

    }



    function Invoke-DevRepo {

        param([string]$RepoRoot)

        if ($usesDevCli) {

            & uv run --directory $RepoRoot @CytArgs

        }

        else {

            & uv run --directory $RepoRoot 'src/cyt/cli/app.py' @CytArgs

        }

        exit $LASTEXITCODE

    }



    if (Test-ScriptIsPackagedCytHook $PSScriptRoot) {

        $repo = Get-CytDevRepoRoot -StartDir $PSScriptRoot

        if ($repo) {

            Invoke-DevRepo -RepoRoot $repo

        }

    }



    $sidecar = Read-CytInvocationSidecar

    if ($sidecar -and $sidecar.mode -eq 'dev' -and $sidecar.repo_root) {

        if (Test-Path -LiteralPath $sidecar.repo_root) {

            Invoke-DevRepo -RepoRoot ([string]$sidecar.repo_root)

        }

    }



    if ($env:CYT_DEV_REPO) {

        try {

            $repo = (Resolve-Path -LiteralPath $env:CYT_DEV_REPO).Path

            Invoke-DevRepo -RepoRoot $repo

        }

        catch {

            Write-Error "CYT_DEV_REPO is not a valid path: $($env:CYT_DEV_REPO)"

            exit 1

        }

    }



    $package = 'clear-your-tools'

    if ($sidecar -and $sidecar.package) {

        $package = [string]$sidecar.package

    }

    $executable = 'cyt'

    if ($sidecar -and $sidecar.executable) {

        $executable = [string]$sidecar.executable

    }



    & uv tool run --from $package $executable @CytArgs

    exit $LASTEXITCODE

}



$shellWorkspace = Get-GitWorkspaceRoot -StartPath (Get-Location).Path

if ($shellWorkspace) {

    $env:CYT_SHELL_WORKSPACE = $shellWorkspace

}



$terminalWorkspace = $env:CYT_WORKSPACE

if ($terminalWorkspace -and -not ($terminalWorkspace -match '^\$\{.+}$')) {

    if ($terminalWorkspace -match '^~|[/\\]\.\.?([/\\]|$)|^\.\.?([/\\]|$)') {

        Write-Error "CYT_WORKSPACE must be a full absolute path (not ./, ../, or ~): $terminalWorkspace"

        exit 1

    }

    try {

        $terminalResolved = (Resolve-Path -LiteralPath $terminalWorkspace).Path

    }

    catch {

        Write-Error "CYT_WORKSPACE must be a full absolute path: $terminalWorkspace"

        exit 1

    }

    if ($shellWorkspace -and $terminalResolved -and ($terminalResolved -ne $shellWorkspace)) {

        Write-Error (

            "CYT workspace conflict: terminal CYT_WORKSPACE=$terminalResolved " +

            "vs shell CYT_SHELL_WORKSPACE=$shellWorkspace. " +

            "Set CYT_SHELL_WORKSPACE to override or align .vscode/settings.json CYT_WORKSPACE."

        )

        exit 1

    }

}



Invoke-CytViaUv -CytArgs $Args
