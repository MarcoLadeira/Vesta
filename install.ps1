param(
    [switch] $WithTools,
    [switch] $NoShellAliases,
    [string] $InstallRoot
)

$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:OPAI_REPO_URL) { $env:OPAI_REPO_URL } else { "https://github.com/MarcoLadeira/OPai.git" }
$Branch = if ($env:OPAI_BRANCH) { $env:OPAI_BRANCH } else { "main" }
$InstallTools = $WithTools -or $env:OPAI_WITH_TOOLS -eq "1"

if (-not $InstallRoot) {
    $InstallRoot = if ($env:OPAI_INSTALL_ROOT) {
        $env:OPAI_INSTALL_ROOT
    } else {
        Join-Path $env:USERPROFILE ".opai\source"
    }
}

function Get-LocalSourceRoot {
    $ScriptPath = $PSCommandPath
    if (-not $ScriptPath) {
        $ScriptPath = $MyInvocation.ScriptName
    }
    if (-not $ScriptPath) {
        $ScriptPath = $MyInvocation.MyCommand.Path
    }
    if ($ScriptPath) {
        $Candidate = Split-Path -Parent $ScriptPath
        if ($Candidate -and (Test-Path (Join-Path $Candidate "pyproject.toml"))) {
            return $Candidate
        }
    }
    return $null
}

function Ensure-RemoteSource {
    param(
        [string] $Target,
        [string] $Url,
        [string] $Ref
    )

    if (-not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) {
        Write-Error "Git is required for the one-command OPai installer. Install Git, then re-run this command."
        exit 127
    }

    $Parent = Split-Path -Parent $Target
    if ($Parent) {
        New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    }

    if (Test-Path (Join-Path $Target ".git")) {
        git -C $Target fetch origin $Ref
        git -C $Target checkout $Ref
        git -C $Target pull --ff-only origin $Ref
    } elseif (Test-Path $Target) {
        if (-not (Test-Path (Join-Path $Target "pyproject.toml"))) {
            Write-Error "Install target exists but is not an OPai checkout: $Target"
            exit 1
        }
    } else {
        git clone --depth 1 --branch $Ref $Url $Target
    }
    return $Target
}

$Root = Get-LocalSourceRoot
if (-not $Root) {
    $Root = Ensure-RemoteSource -Target $InstallRoot -Url $RepoUrl -Ref $Branch
}

$Root = (Resolve-Path $Root).Path
$env:PYTHONPATH = "$Root;$env:PYTHONPATH"

python -m pip install -e "$Root" --no-deps
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$InstallArgs = @("install", "--project", $Root)
if ($InstallTools) {
    $InstallArgs += "--with-tools"
} else {
    $InstallArgs += "--no-tools"
}
if (-not $NoShellAliases) {
    $InstallArgs += "--shell-aliases"
}

python -m opai @InstallArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "OPai 0.1.0 pre-alpha installed permanently."
Write-Host "Source: $Root"
Write-Host "Restart terminals and AI clients once so aliases and skills reload."
Write-Host "Use in any repo: op status"
Write-Host "Launch with OPai: op launch codex"
